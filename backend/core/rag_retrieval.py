"""
Vector RAG retrieval: multi-query embeddings, merged distances, wide candidate pool,
rerank (word overlap + optional pg_trgm), then char-budget fill.
"""

from __future__ import annotations

import logging
import re
import time

from django.db import connection
from django.db.models import Q
from pgvector.django import CosineDistance

logger = logging.getLogger("core.rag")

from core.embeddings import embed_texts
from core.models import DocumentChunk, Page
from core.rag_config import (
    RAG_KEYWORD_BOOST,
    RAG_LEXICAL_WEIGHT,
    RAG_MAX_CHARS,
    RAG_MAX_CHUNKS_PER_URL,
    RAG_MAX_DISTANCE,
    RAG_MULTI_EMBED,
    RAG_MULTI_EMBED_KEYWORD_LINE,
    RAG_RELAX_ON_EMPTY,
    RAG_RERANK_OVERLAP_WEIGHT,
    RAG_SNIPPET_CHARS,
    RAG_STEM_NOISE_URL_PENALTY,
    RAG_TOP_K,
    RAG_VECTOR_CANDIDATE_POOL,
    RAG_VECTOR_FILL_EXTRA,
)
from core.rag_keywords import (
    RAG_STEM_OR_ENGINEERING_INTENT_RE,
    rag_keywords_from_query,
    stem_engineering_boost_terms,
    structured_list_boost_terms,
)


_pg_trgm_cache: bool | None = None


def _pg_trgm_available() -> bool:
    global _pg_trgm_cache
    if _pg_trgm_cache is not None:
        return _pg_trgm_cache
    if connection.vendor != "postgresql":
        _pg_trgm_cache = False
        return False
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm')"
            )
            row = cursor.fetchone()
            _pg_trgm_cache = bool(row and row[0])
    except Exception:
        _pg_trgm_cache = False
    return _pg_trgm_cache


def _query_token_set(composed: str, raw_user: str | None) -> set[str]:
    blob = f"{composed} {raw_user or ''}".lower()
    return set(re.findall(r"[a-zA-ZğüşıöçĞÜŞİÖÇ]{4,}", blob))


def _word_overlap_count(chunk_text: str, tokens: set[str]) -> int:
    if not tokens or not chunk_text:
        return 0
    low = chunk_text.lower()
    return sum(1 for t in tokens if t in low)


_DEPT_LEADER_QUERY_RE = re.compile(
    r"head|chair|director|dean|başkan|baskan|dekan|müdür|mudur",
    re.IGNORECASE,
)
_DEPT_LEADER_CONTENT_RE = re.compile(
    r"head of department|message from head|department head|chair of|"
    r"bölüm başkanı|bolum baskan|dekan|müdür",
    re.IGNORECASE,
)
_DEPT_NAMES: list[tuple[str, str]] = [
    ("computer engineering", "bilgisayar mühendisliği"),
    ("electrical", "elektrik"),
    ("electronics", "elektronik"),
    ("mechanical engineering", "makine mühendisliği"),
    ("civil engineering", "inşaat mühendisliği"),
    ("industrial engineering", "endüstri mühendisliği"),
    ("biomedical engineering", "biyomedikal mühendisliği"),
    ("software engineering", "yazılım mühendisliği"),
    ("medicine", "tıp fakültesi"),
    ("nursing", "hemşirelik"),
    ("pharmacy", "eczacılık"),
    ("dentistry", "diş hekimliği"),
    ("health sciences", "sağlık bilimleri"),
]


def _intent_boost(composed_query: str, url: str, title: str, content: str) -> int:
    q = (composed_query or "").lower()
    blob = f"{url or ''} {title or ''} {content or ''}".lower()
    boost = 0
    if _DEPT_LEADER_QUERY_RE.search(q):
        if _DEPT_LEADER_CONTENT_RE.search(blob):
            boost += 8
        for en_name, tr_name in _DEPT_NAMES:
            if en_name in q or tr_name in q:
                if en_name in blob or tr_name in blob:
                    boost += 5
                break
    return boost


def _lexical_fallback_from_chunks(
    composed_query: str,
    raw_user_query: str | None,
) -> tuple[str, list[dict], bool, bool]:
    """
    Best-effort fallback when embeddings are unavailable:
    score chunks by query token overlap and build a bounded context.
    """
    tokens = _query_token_set(composed_query, raw_user_query)
    if not tokens:
        return "", [], True, False

    max_candidates = max(RAG_TOP_K + RAG_VECTOR_FILL_EXTRA, 40) * 5
    candidates: list[tuple[int, DocumentChunk]] = []
    for ch in DocumentChunk.objects.only(
        "pk", "content", "page_title", "source_url"
    ).iterator(chunk_size=2000):
        score = _word_overlap_count(str(ch.content or ""), tokens)
        score += _word_overlap_count(str(ch.page_title or ""), tokens) * 2
        score += _word_overlap_count(str(ch.source_url or ""), tokens) * 2
        score += _intent_boost(
            composed_query,
            str(ch.source_url or ""),
            str(ch.page_title or ""),
            str(ch.content or ""),
        )
        if score > 0:
            candidates.append((score, ch))
            if len(candidates) >= max_candidates:
                break
    if not candidates:
        return "", [], True, False

    candidates.sort(key=lambda x: (-x[0], x[1].pk))
    ranked = [ch for _, ch in candidates[: max(RAG_TOP_K + RAG_VECTOR_FILL_EXTRA, 40)]]

    context_parts: list[str] = []
    sources: list[dict] = []
    total = 0
    url_counts: dict[str, int] = {}

    # For head/chair/dean questions, prioritize exact department pages from Page rows.
    q = (composed_query or "").lower()
    if _DEPT_LEADER_QUERY_RE.search(q):
        url_keyword = None
        for en_name, tr_name in _DEPT_NAMES:
            if en_name in q or tr_name in q:
                url_keyword = en_name.split()[0]  # e.g. "computer", "electrical"
                break
        if url_keyword:
            page_hits: list[Page] = []
            for p in Page.objects.filter(url__icontains=url_keyword).iterator():
                blob = f"{str(p.url or '')} {str(p.content or '')}".lower()
                if _DEPT_LEADER_CONTENT_RE.search(blob):
                    page_hits.append(p)
                if len(page_hits) >= 3:
                    break
            for p in page_hits:
                if total >= RAG_MAX_CHARS:
                    break
                u = str(p.url or "")
                if url_counts.get(u, 0) >= RAG_MAX_CHUNKS_PER_URL:
                    continue
                snippet = str(p.content or "")[:RAG_SNIPPET_CHARS]
                if not snippet or total + len(snippet) > RAG_MAX_CHARS:
                    continue
                title = str(p.title or p.url or "")
                context_parts.append(f"[{title}]\n{snippet}")
                total += len(snippet)
                url_counts[u] = url_counts.get(u, 0) + 1
                sources.append(
                    {
                        "url": u,
                        "title": title[:200],
                        "cosine_distance": 0.9997,
                    }
                )
    for ch in ranked:
        if total >= RAG_MAX_CHARS:
            break
        u = str(ch.source_url or "")
        if url_counts.get(u, 0) >= RAG_MAX_CHUNKS_PER_URL:
            continue
        snippet = str(ch.content or "")[:RAG_SNIPPET_CHARS]
        if not snippet:
            continue
        if total + len(snippet) > RAG_MAX_CHARS:
            continue
        title = str(ch.page_title or ch.source_url or "")
        context_parts.append(f"[{title}]\n{snippet}")
        total += len(snippet)
        url_counts[u] = url_counts.get(u, 0) + 1
        sources.append(
            {
                "url": ch.source_url,
                "title": (title or "")[:200],
                "cosine_distance": 0.9999,
            }
        )
    return "\n\n".join(context_parts), sources, True, False


def _lexical_fallback_from_pages(
    composed_query: str,
    raw_user_query: str | None,
) -> tuple[str, list[dict], bool, bool]:
    """
    Second fallback when chunk embeddings were never built for some pages.
    """
    tokens = _query_token_set(composed_query, raw_user_query)
    if not tokens:
        return "", [], True, False

    max_candidates = max(RAG_TOP_K, 12) * 5
    candidates: list[tuple[int, Page]] = []
    for p in Page.objects.only("id", "url", "title", "content").iterator(chunk_size=2000):
        blob = f"{str(p.title or '')}\n{str(p.content or '')}"
        score = _word_overlap_count(blob, tokens)
        score += _word_overlap_count(str(p.url or ""), tokens) * 2
        score += _intent_boost(
            composed_query, str(p.url or ""), str(p.title or ""), str(p.content or "")
        )
        if score > 0:
            candidates.append((score, p))
            if len(candidates) >= max_candidates:
                break
    if not candidates:
        return "", [], True, False

    candidates.sort(key=lambda x: (-x[0], x[1].id))
    context_parts: list[str] = []
    sources: list[dict] = []
    total = 0
    for _, p in candidates[: max(RAG_TOP_K, 12)]:
        if total >= RAG_MAX_CHARS:
            break
        snippet = str(p.content or "")[:RAG_SNIPPET_CHARS]
        if not snippet:
            continue
        if total + len(snippet) > RAG_MAX_CHARS:
            continue
        title = str(p.title or p.url or "")
        context_parts.append(f"[{title}]\n{snippet}")
        total += len(snippet)
        sources.append(
            {
                "url": p.url,
                "title": (title or "")[:200],
                "cosine_distance": 1.0,
            }
        )
    return "\n\n".join(context_parts), sources, True, False


def _embedding_variants(composed: str, raw_user: str | None) -> list[str]:
    composed = (composed or "").strip()
    variants: list[str] = []
    if composed:
        variants.append(composed)
    if not RAG_MULTI_EMBED:
        return variants
    raw = (raw_user or "").strip()
    if raw and raw.casefold() != composed.casefold():
        variants.append(raw)
    if RAG_MULTI_EMBED_KEYWORD_LINE:
        kw = rag_keywords_from_query(composed)
        if kw:
            line = " ".join(kw) + " Acibadem Mehmet Ali Aydinlar University"
            if line.casefold() not in {v.casefold() for v in variants}:
                variants.append(line)
    # Dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for v in variants:
        key = v.casefold()
        if key not in seen:
            seen.add(key)
            out.append(v)
    return out


def _merge_best_distances(vectors: list[list[float]], per_vector_pool: int) -> dict[int, float]:
    best: dict[int, float] = {}
    for vec in vectors:
        if not vec:
            continue
        qs = (
            DocumentChunk.objects.annotate(distance=CosineDistance("embedding", vec))
            .order_by("distance")[:per_vector_pool]
        )
        for ch in qs:
            d = float(ch.distance)
            pk = ch.pk
            prev = best.get(pk)
            if prev is None or d < prev:
                best[pk] = d
    return best


# STEM: de-prioritize portal pages that mention many program names in passing.
_STEM_LOW_VALUE_PAGE_RE = re.compile(
    r"event|etkinlik|kariyer|career|duyur|announce|haber|news|kongre|congress|"
    r"life-sciences|burs|scholar|tuition|fee|mezun|alumni|ilan|job|kampus\s*gez",
    re.IGNORECASE,
)


def _stem_noise_penalty(url: str, title: str) -> float:
    if RAG_STEM_NOISE_URL_PENALTY <= 0:
        return 0.0
    blob = f"{url or ''} {title or ''}"
    if _STEM_LOW_VALUE_PAGE_RE.search(blob):
        return RAG_STEM_NOISE_URL_PENALTY
    return 0.0


def _rerank_items(
    items: list[tuple[DocumentChunk, float]],
    composed: str,
    raw_user: str | None,
    stem_query: bool = False,
) -> list[tuple[DocumentChunk, float]]:
    if len(items) <= 1:
        return items
    tokens = _query_token_set(composed, raw_user)
    trigram_q = f"{composed} {(raw_user or '').strip()}".strip()[:400]

    sim_map: dict[int, float] = {}
    if (
        RAG_LEXICAL_WEIGHT > 0
        and trigram_q
        and _pg_trgm_available()
    ):
        from django.contrib.postgres.search import TrigramSimilarity

        pks = [ch.pk for ch, _ in items]
        for ch in (
            DocumentChunk.objects.filter(pk__in=pks)
            .annotate(sim=TrigramSimilarity("content", trigram_q))
            .iterator()
        ):
            sim_map[ch.pk] = float(getattr(ch, "sim", 0.0) or 0.0)

    def sort_key(it: tuple[DocumentChunk, float]) -> float:
        ch, d = it
        ov = _word_overlap_count(str(ch.content or ""), tokens)
        sim = sim_map.get(ch.pk, 0.0)
        # Lower is better: pull down score when overlap/sim is high
        key = (
            d
            - RAG_RERANK_OVERLAP_WEIGHT * min(ov, 30) / 30.0
            - RAG_LEXICAL_WEIGHT * sim
        )
        if stem_query:
            key += _stem_noise_penalty(
                str(ch.source_url or ""), str(ch.page_title or "")
            )
        return key

    return sorted(items, key=sort_key)


def _cosine_distance_by_pk(
    pks: list[int], primary_vector: list[float]
) -> dict[int, float]:
    """Real cosine distance for metadata (avoids misleading constant 'boost' distances)."""
    if not pks or not primary_vector:
        return {}
    out: dict[int, float] = {}
    for ch in DocumentChunk.objects.filter(pk__in=pks).annotate(
        _cd=CosineDistance("embedding", primary_vector)
    ).iterator():
        out[ch.pk] = float(ch._cd)
    return out


def search_document_chunks(
    composed_query: str, raw_user_query: str | None = None
) -> tuple[str, list[dict], bool, bool]:
    """
    Returns (context_text, sources, used_relaxed_fallback, embedding_ok).
    """
    t0 = time.time()
    composed_query = (composed_query or "").strip()
    if not composed_query:
        return "", [], False, True

    variants = _embedding_variants(composed_query, raw_user_query)
    try:
        t_emb = time.time()
        vectors = embed_texts(variants) if variants else []
        logger.info("RAG embed: %.2fs (%d variants)", time.time() - t_emb, len(variants))
    except Exception:
        logger.warning("RAG embed failed, falling back to lexical search", exc_info=True)
        context, sources, relaxed, emb_ok = _lexical_fallback_from_chunks(
            composed_query, raw_user_query
        )
        if context:
            return context, sources, relaxed, emb_ok
        return _lexical_fallback_from_pages(composed_query, raw_user_query)
    vectors = [v for v in vectors if v]
    if not vectors:
        return "", [], False, False

    has_rows = DocumentChunk.objects.exists()
    per_pool = max(
        RAG_VECTOR_CANDIDATE_POOL,
        RAG_TOP_K + RAG_VECTOR_FILL_EXTRA + 8,
    )

    t_vec = time.time()
    best = _merge_best_distances(vectors, per_pool)
    logger.info("RAG vector search: %.2fs (pool=%d)", time.time() - t_vec, per_pool)
    if not best:
        return "", [], False, True

    sorted_pairs = sorted(best.items(), key=lambda x: x[1])[:per_pool]
    pk_order = [pk for pk, _ in sorted_pairs]
    dist_map = dict(sorted_pairs)
    chunk_map = {
        c.pk: c for c in DocumentChunk.objects.filter(pk__in=pk_order)
    }
    merged_order: list[tuple[DocumentChunk, float]] = []
    for pk in pk_order:
        ch = chunk_map.get(pk)
        if ch is None:
            continue
        d = dist_map[pk]
        merged_order.append((ch, d))

    candidate_slice = merged_order[:per_pool]
    stem_intent = bool(RAG_STEM_OR_ENGINEERING_INTENT_RE.search(composed_query))

    t_rerank = time.time()
    reranked = _rerank_items(
        candidate_slice, composed_query, raw_user_query, stem_query=stem_intent
    )
    logger.info("RAG rerank: %.2fs (%d candidates)", time.time() - t_rerank, len(candidate_slice))

    thresh_hits = [(ch, d) for ch, d in reranked if d <= RAG_MAX_DISTANCE]
    used_relaxed = False
    if len(thresh_hits) >= RAG_TOP_K:
        vector_block = thresh_hits[:RAG_TOP_K]
    elif thresh_hits:
        vector_block = thresh_hits
    elif RAG_RELAX_ON_EMPTY and has_rows:
        vector_block = reranked[:RAG_TOP_K]
        used_relaxed = bool(vector_block)
    else:
        vector_block = []

    ranked: list[tuple[DocumentChunk, float]] = []
    seen_pk: set[int] = set()

    def push_kw(ch: DocumentChunk, nominal: float) -> None:
        pk = ch.pk
        if pk not in seen_pk:
            seen_pk.add(pk)
            ranked.append((ch, nominal))

    t_kw = time.time()
    if RAG_KEYWORD_BOOST and has_rows:
        stem_terms = stem_engineering_boost_terms(composed_query)
        if stem_terms:
            q_filter = Q()
            for term in stem_terms:
                q_filter |= Q(content__icontains=term)
            for ch in DocumentChunk.objects.filter(q_filter)[:len(stem_terms) * 6]:
                push_kw(ch, 0.62)

    if RAG_KEYWORD_BOOST and has_rows and not stem_intent:
        struct_terms = structured_list_boost_terms(composed_query)
        if struct_terms:
            q_filter = Q()
            for term in struct_terms:
                q_filter |= Q(content__icontains=term)
            for ch in DocumentChunk.objects.filter(q_filter)[:len(struct_terms) * 5]:
                push_kw(ch, 0.65)

    for ch, d in vector_block:
        push_kw(ch, float(d))

    if RAG_KEYWORD_BOOST and has_rows and not stem_intent:
        kw_terms = rag_keywords_from_query(composed_query)
        if kw_terms:
            q_filter = Q()
            for term in kw_terms:
                q_filter |= Q(content__icontains=term)
            for ch in DocumentChunk.objects.filter(q_filter)[:len(kw_terms) * 4]:
                push_kw(ch, 0.72)
    logger.info("RAG keyword boost: %.2fs", time.time() - t_kw)

    primary_vec = vectors[0]
    ranked_pks = [ch.pk for ch, _ in ranked]

    t_dist = time.time()
    real_dist = _cosine_distance_by_pk(ranked_pks, primary_vec)
    logger.info("RAG cosine re-dist: %.2fs (%d pks)", time.time() - t_dist, len(ranked_pks))

    def _effective_sort_distance(ch: DocumentChunk, nominal: float) -> float:
        d = real_dist.get(ch.pk, float(nominal))
        if stem_intent:
            d += _stem_noise_penalty(
                str(ch.source_url or ""), str(ch.page_title or "")
            )
        return d

    ranked.sort(key=lambda it: (_effective_sort_distance(it[0], it[1]), it[0].pk))

    context_parts: list[str] = []
    sources: list[dict] = []
    total = 0
    url_counts: dict[str, int] = {}
    seen_chunk_ids: set[int] = set()

    def try_add_context_chunk(ch: DocumentChunk, nominal_dist: float) -> None:
        nonlocal total
        if total >= RAG_MAX_CHARS:
            return
        cid = ch.pk
        if cid in seen_chunk_ids:
            return
        u = str(ch.source_url or "")
        if url_counts.get(u, 0) >= RAG_MAX_CHUNKS_PER_URL:
            return
        snippet = str(ch.content or "")[:RAG_SNIPPET_CHARS]
        if total + len(snippet) > RAG_MAX_CHARS:
            return
        title = str(ch.page_title or ch.source_url or "")
        context_parts.append(f"[{title}]\n{snippet}")
        total += len(snippet)
        url_counts[u] = url_counts.get(u, 0) + 1
        seen_chunk_ids.add(cid)
        display_d = real_dist.get(cid, nominal_dist)
        sources.append(
            {
                "url": ch.source_url,
                "title": (title or "")[:200],
                "cosine_distance": round(float(display_d), 4),
            }
        )

    for chunk, dist_val in ranked:
        try_add_context_chunk(chunk, dist_val)

    if total < RAG_MAX_CHARS and RAG_VECTOR_FILL_EXTRA > 0:
        fill_slice = [
            (ch, d) for ch, d in reranked if ch.pk not in seen_chunk_ids
        ][:RAG_VECTOR_FILL_EXTRA]
        # Use existing dist_map distances instead of an extra DB query
        for ch, _ in fill_slice:
            if ch.pk not in real_dist:
                real_dist[ch.pk] = dist_map.get(ch.pk, 1.0)
        fill_slice.sort(
            key=lambda it: (_effective_sort_distance(it[0], it[1]), it[0].pk)
        )
        for ch, d in fill_slice:
            try_add_context_chunk(ch, float(d))
            if total >= RAG_MAX_CHARS:
                break

    logger.info("RAG total: %.2fs, chunks=%d, chars=%d", time.time() - t0, len(sources), total)
    return "\n\n".join(context_parts), sources, used_relaxed, True
