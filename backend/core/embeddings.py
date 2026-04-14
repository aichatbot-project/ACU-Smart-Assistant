import hashlib
import logging
import os
import threading
from collections import OrderedDict
from functools import lru_cache

from sentence_transformers import SentenceTransformer


logger = logging.getLogger("core.embeddings")

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
_EMBEDDING_LOAD_FAILED = False

# ── Embedding cache ──────────────────────────────────────────────────────
_EMBED_CACHE_MAX = int(os.environ.get("EMBED_CACHE_MAX", "512"))
_embed_cache: OrderedDict[str, list[float]] = OrderedDict()
_cache_lock = threading.Lock()


def _cache_key(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def chunk_text(text: str, chunk_size: int = 700, chunk_overlap: int = 120) -> list[str]:
    clean = " ".join((text or "").split())
    if not clean:
        return []
    if chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be smaller than chunk_size")

    chunks: list[str] = []
    start = 0
    step = chunk_size - chunk_overlap
    while start < len(clean):
        end = min(len(clean), start + chunk_size)
        piece = clean[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= len(clean):
            break
        start += step
    return chunks


@lru_cache(maxsize=1)
def get_embedding_model() -> SentenceTransformer:
    global _EMBEDDING_LOAD_FAILED
    if _EMBEDDING_LOAD_FAILED:
        raise RuntimeError("Embedding model is unavailable in this environment.")
    model_name = os.environ.get("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
    try:
        model = SentenceTransformer(model_name)
        logger.info("Embedding model loaded: %s", model_name)
        return model
    except Exception:
        _EMBEDDING_LOAD_FAILED = True
        raise


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    results: list[list[float] | None] = [None] * len(texts)
    to_encode: list[str] = []
    to_encode_idx: list[int] = []

    with _cache_lock:
        for i, t in enumerate(texts):
            key = _cache_key(t)
            cached = _embed_cache.get(key)
            if cached is not None:
                _embed_cache.move_to_end(key)
                results[i] = cached
            else:
                to_encode.append(t)
                to_encode_idx.append(i)

    if to_encode:
        model = get_embedding_model()
        vectors = model.encode(to_encode, normalize_embeddings=True)
        with _cache_lock:
            for j, idx in enumerate(to_encode_idx):
                vec = vectors[j].tolist()
                results[idx] = vec
                key = _cache_key(to_encode[j])
                _embed_cache[key] = vec
                if len(_embed_cache) > _EMBED_CACHE_MAX:
                    _embed_cache.popitem(last=False)

    cache_hits = len(texts) - len(to_encode)
    if cache_hits:
        logger.debug("Embedding cache: %d hits, %d misses", cache_hits, len(to_encode))

    failed = sum(1 for r in results if r is None)
    if failed:
        logger.warning("embed_texts: %d/%d texts failed to embed", failed, len(texts))
    return results  # type: ignore[return-value]


def embed_query(text: str) -> list[float]:
    vectors = embed_texts([text.strip()])
    return vectors[0] if vectors else []
