import json
import logging
import os
import re
import socket
import urllib.error
import urllib.request
import uuid
from typing import Any

from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods

from pgvector.django import CosineDistance

from .models import ChatMessage, ChatSession
from core.embeddings import embed_query
from core.models import DocumentChunk

logger = logging.getLogger(__name__)

LLM_BACKEND = os.environ.get("LLM_BACKEND", "ollama")
RAG_MAX_CHARS = 2000
RAG_TOP_K = int(os.environ.get("RAG_TOP_K", "10"))
# Cosine distance (lower = closer). 0.55 was too strict—correct pages often sat at 0.56–0.65.
RAG_MAX_DISTANCE = float(os.environ.get("RAG_MAX_DISTANCE", "0.62"))
# When no chunk passes the threshold, still send the closest K (model must follow “only if in CONTEXT”).
RAG_RELAX_ON_EMPTY = os.environ.get("RAG_RELAX_ON_EMPTY", "true").lower() in (
    "1",
    "true",
    "yes",
)
# Merge chunks whose text contains query keywords (helps Turkish/synonym misses vs BGE-en only).
RAG_KEYWORD_BOOST = os.environ.get("RAG_KEYWORD_BOOST", "true").lower() in (
    "1",
    "true",
    "yes",
)
RAG_SNIPPET_CHARS = max(400, int(os.environ.get("RAG_SNIPPET_CHARS", "900")))

# Ollama settings
OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "phi3:mini")
# Defaults must fit RAG system prompt + Context (~2k chars) + history; tiny ctx/predict
# causes truncation so the model never sees the full address and may refuse or hallucinate.
OLLAMA_NUM_PREDICT = int(os.environ.get("OLLAMA_NUM_PREDICT", "256"))
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "4096"))
OLLAMA_HTTP_TIMEOUT = int(os.environ.get("OLLAMA_HTTP_TIMEOUT", "240"))
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")
OLLAMA_TEMPERATURE = float(
    os.environ.get(
        "OLLAMA_TEMPERATURE",
        "0" if "phi" in OLLAMA_MODEL.lower() else "0.15",
    )
)
OLLAMA_TOP_P = float(os.environ.get("OLLAMA_TOP_P", "0.85"))
OLLAMA_REPEAT_PENALTY = float(os.environ.get("OLLAMA_REPEAT_PENALTY", "1.12"))
# Long threads exceed Ollama num_ctx; the model then drops early tokens (system+RAG) and
# falls back to generic "as an AI / 2023" disclaimers despite crawled Context.
CHAT_HISTORY_MAX_MESSAGES = max(1, int(os.environ.get("CHAT_HISTORY_MAX_MESSAGES", "12")))
CHAT_MESSAGE_MAX_CHARS = max(200, int(os.environ.get("CHAT_MESSAGE_MAX_CHARS", "900")))

# Claude API settings
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

RAG_FALLBACK_REPLY = (
    "I don't have that information in the crawled pages. Try running a data refresh or rephrase your question."
)

# Tiny local models often refuse or ramble on bare "hi" when RAG snippets are unrelated.
GREETING_REPLY = (
    "Hello. I'm the ACU website assistant for Acıbadem Mehmet Ali Aydınlar University. "
    "Ask me about programs, admissions, or the campus in English—I answer using the official website content."
)

_BARE_GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|hallo|yo|merhaba|selam|good\s+(morning|afternoon|evening))\b[\s!?.…]*$",
    re.I,
)


def _bare_greeting_reply(plain_user: str) -> str | None:
    s = (plain_user or "").strip()
    if not s or len(s) > 48:
        return None
    if _BARE_GREETING_RE.match(s):
        return GREETING_REPLY
    return None


def _greeting_rag_meta() -> dict:
    return {
        "embedding_ok": True,
        "chunks_used": 0,
        "relaxed_retrieval": False,
        "sources": [],
        "rag_query_preview": "",
        "skipped_llm": "bare_greeting",
        "context_chars_sent": 0,
        "llm_user_turn_chars": len(GREETING_REPLY),
        "context_block_in_llm": False,
        "indexed_chunks_in_db": DocumentChunk.objects.count(),  # pyright: ignore[reportAttributeAccessIssue]
    }


SYSTEM_BASE = (
    "You are the official website assistant for Acıbadem Mehmet Ali Aydınlar University (ACU). "
    "LANGUAGE: English only. Never use Turkish unless the user pasted Turkish inside their message. "
    "Be direct and factual. "
    "GROUNDING: Use ONLY the Context block below when it is present. Quote or paraphrase it; "
    "do not invent addresses, policies, disclaimers, or refusals. "
    "Official campus or unit postal addresses, phone numbers, and emails printed in Context are "
    "public university contact data—state them when the user asks; do not refuse as \"private\". "
    "Do not mention OpenAI, Anthropic, Microsoft, training data, or content policies. "
    "If Context is missing or does not contain the answer, reply exactly: "
    f"\"{RAG_FALLBACK_REPLY}\" "
    "If the question is out of scope for the website content, say so in one sentence."
)

# When RAG hits, put crawled text in the *last user* turn (not only at end of system).
# Many local models truncate from the start of the prompt; system+RAG at the top was being dropped
# while long chat history remained, so the model answered from pretrained “Microsoft / 2023” habits.
SYSTEM_RAG_USER_WRAPPER = (
    "You are the official Acıbadem Mehmet Ali Aydınlar University (ACU) website assistant. "
    "LANGUAGE: English only unless the user pasted Turkish inside ===QUESTION=== below. "
    "The user message has ===CONTEXT=== (excerpts from crawled acibadem.edu.tr) and ===QUESTION===. "
    "Rules: (1) Every sentence must be directly supported by ===CONTEXT===; if you cannot, stop. "
    "(2) Do not add rankings, statistics, dates, program names, fees, or partner universities unless "
    "those exact facts appear in ===CONTEXT===. Do not generalize or fill gaps from memory. "
    "(3) 2–4 short sentences maximum. "
    "(4) Never say you are from Microsoft/OpenAI/Anthropic; never mention training data, browsing "
    "the live web, or a knowledge cutoff year. "
    "(5) If ===QUESTION=== asks for anything not clearly answered in ===CONTEXT===, reply exactly: "
    f"\"{RAG_FALLBACK_REPLY}\""
)
RAG_USER_BUBBLE_MAX_CHARS = max(2000, int(os.environ.get("RAG_USER_BUBBLE_MAX_CHARS", "4500")))

_RAG_KEYWORD_STOP = frozenset(
    """
    what when where which who how why the and for with about from that this have does did you your
    are was were please dont not tell can could would should university universite universitesi
    acibadem acıbadem mehmet ali aydinlar aydınlar tell lie know please more some any very just
    like into than then them they their there here hakkında nedir nelerdir nasıl hangi şey
    """.split()
)


def _rag_keywords_from_query(text: str, max_terms: int = 5) -> list[str]:
    raw = (text or "").lower()
    words = re.findall(r"[a-zA-ZğüşıöçĞÜŞİÖÇ]{4,}", raw)
    out: list[str] = []
    for w in words:
        if w in _RAG_KEYWORD_STOP:
            continue
        if w not in out:
            out.append(w)
        if len(out) >= max_terms:
            break
    return out


def _compose_rag_search_query(
    current_message: str, prior_user_messages: list[str]
) -> str:
    """
    BGE embedding model is English-first; short Turkish questions often miss relevant chunks.
    Merge recent user turns and anchor with the university name when absent.
    """
    prior = [t.strip() for t in prior_user_messages if t.strip()][-2:]
    cur = (current_message or "").strip()
    merged = "\n".join(prior + [cur]) if (prior or cur) else ""
    if not merged:
        return ""
    if not re.search(
        r"acibadem|\bacu\b|mehmet\s+ali|açıbadem|aydınlar",
        merged,
        re.IGNORECASE,
    ):
        merged = f"{merged}\nAcıbadem Mehmet Ali Aydınlar University (ACU)"
    # BGE is English-heavy; Turkish location/contact questions often retrieve wrong pages
    # (runtime logs: "universite adresi" ranked scholarship pages; English "address" hit ACUTAB).
    if re.search(
        r"adres|address|konum|location|postal|tam\s*adres|kamp[uü]s|campus|"
        r"\bnerede\b|where\s+is|iletişim|contact\b|ulaşım|how\s+to\s+get",
        merged,
        re.IGNORECASE,
    ):
        merged = f"{merged}\npostal address campus location contact Istanbul Kerem Aydinlar"
    return merged


def _search_pages_with_meta(query: str) -> tuple[str, list[dict], bool, bool]:
    """
    Returns (context_text, sources, used_relaxed_fallback, embedding_ok).
    """
    query = (query or "").strip()
    if not query:
        return "", [], False, True

    query_vector = embed_query(query)
    if not query_vector:
        return "", [], False, False

    base_qs = (
        DocumentChunk.objects.annotate(distance=CosineDistance("embedding", query_vector))  # pyright: ignore[reportAttributeAccessIssue]
        .order_by("distance")
    )

    has_rows = DocumentChunk.objects.exists()  # pyright: ignore[reportAttributeAccessIssue]
    used_relaxed = False
    if RAG_RELAX_ON_EMPTY and has_rows:
        vector_chunks = list(base_qs.filter(distance__lte=RAG_MAX_DISTANCE)[:RAG_TOP_K])
        if not vector_chunks:
            vector_chunks = list(base_qs[:RAG_TOP_K])
            used_relaxed = bool(vector_chunks)
    else:
        vector_chunks = list(base_qs.filter(distance__lte=RAG_MAX_DISTANCE)[:RAG_TOP_K])

    # (chunk, distance) — keyword rows use a nominal distance for ordering/display only
    ranked: list[tuple] = []
    seen_pk: set[int] = set()
    for ch in vector_chunks:
        pk = ch.pk
        if pk not in seen_pk:
            seen_pk.add(pk)
            ranked.append((ch, float(ch.distance)))

    if RAG_KEYWORD_BOOST and has_rows:
        for term in _rag_keywords_from_query(query):
            for ch in DocumentChunk.objects.filter(content__icontains=term)[:3]:  # pyright: ignore[reportAttributeAccessIssue]
                pk = ch.pk
                if pk not in seen_pk:
                    seen_pk.add(pk)
                    ranked.append((ch, 0.75))

    context_parts: list[str] = []
    sources: list[dict] = []
    total = 0
    seen_urls: set[str] = set()
    seen_chunk_ids: set[int] = set()

    for chunk, dist_val in ranked:
        if chunk.source_url in seen_urls and total > int(RAG_MAX_CHARS * 0.7):
            continue
        seen_urls.add(chunk.source_url)

        snippet = (chunk.content or "")[:RAG_SNIPPET_CHARS]
        if total + len(snippet) > RAG_MAX_CHARS:
            break
        title = chunk.page_title or chunk.source_url
        context_parts.append(f"[{title}]\n{snippet}")
        total += len(snippet)
        cid = getattr(chunk, "pk", None)
        if cid is not None and cid not in seen_chunk_ids:
            seen_chunk_ids.add(cid)
            sources.append(
                {
                    "url": chunk.source_url,
                    "title": (title or "")[:200],
                    "cosine_distance": round(float(dist_val), 4),
                }
            )

    return "\n\n".join(context_parts), sources, used_relaxed, True


def _wrap_user_with_rag_context(context: str, user_plain: str) -> str:
    footer = (
        "\n===END_QUESTION===\n"
        "Answer in English using ONLY text from ===CONTEXT===. "
        "If the answer is not there, output only the exact fallback sentence from the system message."
    )
    body = (
        f"===CONTEXT===\n{context.strip()}\n===QUESTION===\n{user_plain.strip()}{footer}"
    )
    if len(body) > RAG_USER_BUBBLE_MAX_CHARS:
        # Keep question + footer; trim context from the end
        qpart = f"\n===QUESTION===\n{user_plain.strip()}{footer}"
        overhead = len("===CONTEXT===\n\n...(truncated)...\n")
        room = RAG_USER_BUBBLE_MAX_CHARS - overhead - len(qpart)
        ctx = context.strip()[: max(500, room)]
        body = f"===CONTEXT===\n{ctx}\n...(truncated)...{qpart}"
    return body


def _attach_llm_visibility_meta(meta: dict, user_llm: str, context_char_count: int) -> dict:
    """Prove to the client whether crawled text was actually placed in the prompt."""
    meta["indexed_chunks_in_db"] = DocumentChunk.objects.count()  # pyright: ignore[reportAttributeAccessIssue]
    meta["context_chars_sent"] = context_char_count
    meta["llm_user_turn_chars"] = len(user_llm)
    meta["context_block_in_llm"] = bool(
        context_char_count > 0 and "===CONTEXT===" in user_llm
    )
    return meta


def _prepare_chat_prompts(rag_query: str, user_plain: str) -> tuple[str, str, dict]:
    """
    Build (system_message, user_message_for_llm, rag_meta).
    When retrieval succeeds, crawled excerpts live in the user turn so they stay near the end
    of the prompt and survive context-window truncation better than system-only RAG.
    """
    context, sources, relaxed, emb_ok = _search_pages_with_meta(rag_query)
    meta: dict = {
        "embedding_ok": emb_ok,
        "chunks_used": len(sources),
        "relaxed_retrieval": relaxed,
        "sources": sources,
        "rag_query_preview": rag_query[:400],
    }
    user_plain = (user_plain or "").strip()

    if not emb_ok:
        system = (
            f"{SYSTEM_BASE}\n\nThe question could not be embedded (model error). "
            "Use the exact fallback sentence from the rules."
        )
        user_llm = _trim_message_for_llm(user_plain)
        _attach_llm_visibility_meta(meta, user_llm, 0)
        return system, user_llm, meta

    if context:
        system = SYSTEM_RAG_USER_WRAPPER
        if relaxed:
            system += (
                "\n\nNote: Strict match failed; ===CONTEXT=== is the closest crawl text—"
                "use it only if it clearly answers ===QUESTION===."
            )
        user_llm = _wrap_user_with_rag_context(context, user_plain)
        _attach_llm_visibility_meta(meta, user_llm, len(context))
        return system, user_llm, meta

    meta["reason"] = "no_matching_chunks"
    system = (
        f"{SYSTEM_BASE}\n\n"
        "No Context was retrieved for this question. Follow the fallback rule; "
        "do not guess from general knowledge."
    )
    user_llm = _trim_message_for_llm(user_plain)
    _attach_llm_visibility_meta(meta, user_llm, 0)
    return system, user_llm, meta


def _trim_last_user_for_llm(content: str) -> str:
    """Do not apply 900-char cap to RAG-wrapped user bubbles (would delete context)."""
    if "===CONTEXT===" in content:
        if len(content) > RAG_USER_BUBBLE_MAX_CHARS + 500:
            return content[: RAG_USER_BUBBLE_MAX_CHARS + 499] + "…"
        return content
    return _trim_message_for_llm(content)


def _rag_query_from_request_body(body: dict) -> str:
    """Derive retrieval text from JSON body (message and/or messages[])."""
    user_msg = (body.get("message") or "").strip()
    raw_history = body.get("messages")
    if isinstance(raw_history, list) and raw_history:
        users: list[str] = []
        for item in raw_history:
            if isinstance(item, dict) and item.get("role") == "user":
                c = (item.get("content") or "").strip()
                if c:
                    users.append(c)
        if users:
            return _compose_rag_search_query(users[-1], users[:-1])
    if user_msg:
        return _compose_rag_search_query(user_msg, [])
    return ""


def _trim_message_for_llm(text: str, max_chars: int = CHAT_MESSAGE_MAX_CHARS) -> str:
    t = (text or "").strip()
    if len(t) <= max_chars:
        return t
    return t[: max_chars - 1] + "…"


def _strip_common_model_prefixes(text: str) -> str:
    t = (text or "").strip()
    for p in ("<|assistant|>", "<|assistant|", "<|end|>", "Assistant:", "assistant:"):
        if t.startswith(p):
            t = t[len(p) :].lstrip()
    return t


_GARBLED_LINE_PATTERNS = (
    re.compile(r"^\s*rewrite[-\s]?craft", re.I | re.M),
    re.compile(r"i'?s_assistant", re.I),
    re.compile(r"^\s*[a-z]\)\s+i'?s_", re.I | re.M),
    re.compile(r"arempact", re.I),
    re.compile(r"you\s+are\s*mpact", re.I),
    re.compile(r"you\s+diffusion", re.I),
    re.compile(r"diffusion\s*,\s*your\s+task", re.I),
    re.compile(r"patiently\s*/\s*suggest", re.I),
    re.compile(r"\bas\s+anf\b", re.I),
)


def _is_garbled_assistant_reply(text: str) -> bool:
    """Heuristic: tiny models (esp. phi3:mini) sometimes dump exam or training templates."""
    t = _strip_common_model_prefixes(text)
    if len(t) < 12:
        return True
    low = t.lower()
    needles = (
        "instruction prompting",
        "python programming",
        "documentary filmography",
        "environmental sustainability in rural",
        "promise, i's",
        "i's a)",
        "question: chat",
        "\nquestion:",
        "a) = [",
        "b) = [",
        "chat \n- promise",
        "as an ai language model",
        "based on your training",
        "rewrite-craft",
        "rewrite craft",
        "i's_assistant",
        "i apologize for your task",
        "apologize for your task",
        "craft a)",
        "mpactedd",
        "you arempact",
        "_assistant:",
        "assistant: i apologize",
        "you diffusion",
        "diffusion, your task",
        "diffusion, your",
        "patiently/suggestion",
        "patiently/suggest",
        "as anf",
        "your task:",
        "a patiently",
        "needle-ai",
        "needle-a",
    )
    if any(n in low for n in needles):
        return True
    for rx in _GARBLED_LINE_PATTERNS:
        if rx.search(t):
            return True
    head = low[:200]
    if head.startswith("input:") and "question:" in head:
        return True
    if re.match(r"^\s*rewrite[-\s]", low) or re.match(r"^\s*[a-z]\)\s+", low):
        if "acibadem" not in low and "acu" not in low and "university" not in low:
            return True
    if re.match(r"^\s*you\s+[A-Za-z]+,?\s+your\s+task\s*:", low):
        if "acibadem" not in low and "istanbul" not in low:
            return True
    return False


def _last_user_content(messages: list) -> str:
    for m in reversed(messages):
        if m.get("role") == "user":
            return (m.get("content") or "").strip()
    return ""


def _rag_user_turn_has_context(user_content: str) -> bool:
    c = user_content or ""
    if "===CONTEXT===" not in c or "===QUESTION===" not in c:
        return False
    mid = c.split("===CONTEXT===", 1)[1].split("===QUESTION===", 1)[0].strip()
    return len(mid) > 80


def _extractive_reply_from_context_user_message(user_content: str) -> str | None:
    if not _rag_user_turn_has_context(user_content):
        return None
    mid = user_content.split("===CONTEXT===", 1)[1].split("===QUESTION===", 1)[0].strip()
    if len(mid) < 40:
        return None
    clip = mid[:750].rsplit(" ", 1)[0] + ("…" if len(mid) > 750 else "")
    return (
        "Here is what the crawled ACU website pages say (verbatim excerpt): "
        + clip
    )


def _phi3_slim_messages(ollama_messages: list) -> list:
    """Drop chat history; Phi-3 often derails with long multi-turn + RAG."""
    sys_m = next((m for m in ollama_messages if m.get("role") == "system"), None)
    user_m = next((m for m in reversed(ollama_messages) if m.get("role") == "user"), None)
    if not sys_m or not user_m:
        return ollama_messages
    return [dict(sys_m), dict(user_m)]


def _parse_client_id(raw: str | None):
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except (ValueError, TypeError):
        return None


def _call_claude(messages: list) -> tuple[str | None, str | None]:
    system_text = ""
    api_messages = []
    for m in messages:
        if m["role"] == "system":
            system_text = m["content"]
        else:
            api_messages.append({"role": m["role"], "content": m["content"]})

    payload = json.dumps({
        "model": CLAUDE_MODEL,
        "max_tokens": 256,
        "system": system_text,
        "messages": api_messages,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        return None, f"Claude API hatası: {detail}"
    except urllib.error.URLError as e:
        return None, f"Claude API bağlantı hatası: {e.reason}"

    content_blocks = data.get("content", [])
    reply = ""
    for block in content_blocks:
        if block.get("type") == "text":
            reply += block.get("text", "")
    reply = reply.strip()
    if not reply:
        return None, "Claude boş yanıt döndü"
    return reply, None


def _call_ollama(
    ollama_messages: list,
    option_overrides: dict[str, Any] | None = None,
    *,
    _attempt: int = 0,
) -> tuple[str | None, str | None]:
    opts: dict[str, Any] = {
        "num_predict": OLLAMA_NUM_PREDICT,
        "num_ctx": OLLAMA_NUM_CTX,
        "temperature": OLLAMA_TEMPERATURE,
        "top_p": OLLAMA_TOP_P,
        "repeat_penalty": OLLAMA_REPEAT_PENALTY,
    }
    if option_overrides:
        opts.update(option_overrides)

    payload = json.dumps(
        {
            "model": OLLAMA_MODEL,
            "messages": ollama_messages,
            "stream": False,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "options": opts,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    raw_body = ""
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_HTTP_TIMEOUT) as resp:
            raw_body = resp.read().decode()
            data = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning(
            "Ollama returned non-JSON body (first 300 chars): %r", raw_body[:300]
        )
        return None, (
            "Ollama geçersiz yanıt döndü (JSON değil). OLLAMA_MODEL ve Ollama günlüğünü kontrol edin."
        )
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        return None, detail or e.reason
    except urllib.error.URLError as e:
        err = str(e.reason)
        if isinstance(e.reason, TimeoutError) or "timed out" in err.lower():
            return None, (
                "Ollama yanıtı zaman aşımına uğradı. Docker RAM artırın, "
                "OLLAMA_NUM_PREDICT azaltın veya modeli küçültün."
            )
        return None, err
    except socket.timeout:
        return None, "Ollama zaman aşımı (socket). Sunucu veya model çok yavaş."

    reply = _strip_common_model_prefixes(
        (data.get("message") or {}).get("content", "")
    )
    if not reply:
        return None, "Empty model response"

    if _is_garbled_assistant_reply(reply):
        if _attempt == 0:
            logger.warning("Ollama reply looks corrupted; retry with temperature=0")
            return _call_ollama(
                ollama_messages,
                {"temperature": 0.0, "top_p": 0.85},
                _attempt=1,
            )
        if _attempt == 1 and "phi" in OLLAMA_MODEL.lower():
            slim = _phi3_slim_messages(ollama_messages)
            if slim != ollama_messages:
                logger.warning(
                    "Ollama still garbled; retry with slim context (no chat history)"
                )
                return _call_ollama(
                    slim,
                    {
                        "temperature": 0.0,
                        "top_p": 0.75,
                        "repeat_penalty": min(1.25, OLLAMA_REPEAT_PENALTY + 0.06),
                    },
                    _attempt=2,
                )
        logger.warning("Ollama reply still corrupted; trying extractive RAG reply")
        ext = _extractive_reply_from_context_user_message(
            _last_user_content(ollama_messages)
        )
        if ext:
            return ext, None
        return RAG_FALLBACK_REPLY, None

    return reply, None


def _call_llm(messages: list) -> tuple[str | None, str | None]:
    if LLM_BACKEND == "claude" and ANTHROPIC_API_KEY:
        return _call_claude(messages)
    u = _last_user_content(messages)
    if u and "phi" in OLLAMA_MODEL.lower() and not _rag_user_turn_has_context(u):
        return RAG_FALLBACK_REPLY, None
    return _call_ollama(messages)


@csrf_exempt
@require_GET
def list_sessions(request):
    cid = _parse_client_id(
        request.GET.get("client_id") or request.headers.get("X-Client-Id")
    )
    if cid is None:
        return JsonResponse({"error": "client_id gerekli (UUID)"}, status=400)
    sessions = ChatSession.objects.filter(client_id=cid)[:100]  # pyright: ignore[reportAttributeAccessIssue]
    return JsonResponse(
        {
            "sessions": [
                {
                    "id": str(s.id),
                    "title": s.title,
                    "updated_at": s.updated_at.isoformat(),
                }
                for s in sessions
            ]
        }
    )


@csrf_exempt
@require_http_methods(["GET", "DELETE"])
def session_detail(request, pk):
    cid = _parse_client_id(
        request.GET.get("client_id") or request.headers.get("X-Client-Id")
    )
    if cid is None:
        return JsonResponse({"error": "client_id gerekli (query, UUID)"}, status=400)

    session = get_object_or_404(ChatSession, pk=pk, client_id=cid)

    if request.method == "DELETE":
        session.delete()
        return JsonResponse({"ok": True})

    msgs = [
        {
            "id": str(m.id),
            "role": m.role,
            "content": m.content,
            "timestamp": m.created_at.isoformat(),
        }
        for m in session.messages.all()
    ]
    return JsonResponse({"session_id": str(session.id), "title": session.title, "messages": msgs})


@csrf_exempt
@require_http_methods(["POST"])
def chat_completion(request):
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    client_uuid = _parse_client_id(body.get("client_id"))

    if client_uuid is not None:
        return _chat_with_db(request, body, client_uuid)

    raw_history = body.get("messages")
    user_msg = (body.get("message") or "").strip()
    rag_q = _rag_query_from_request_body(body)

    if isinstance(raw_history, list) and len(raw_history) > 0:
        parsed: list[dict] = []
        for item in raw_history[-CHAT_HISTORY_MAX_MESSAGES:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = (item.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                parsed.append({"role": role, "content": _trim_message_for_llm(content)})

        last_user_idx: int | None = None
        plain_for_rag = user_msg
        for i in range(len(parsed) - 1, -1, -1):
            if parsed[i]["role"] == "user":
                last_user_idx = i
                plain_for_rag = parsed[i]["content"]
                break

        if last_user_idx is None and not user_msg:
            return JsonResponse(
                {"error": "messages must include at least one user turn"},
                status=400,
            )
        if last_user_idx is None:
            plain_for_rag = user_msg

        gr = _bare_greeting_reply(plain_for_rag)
        if gr:
            return JsonResponse({"reply": gr, "rag": _greeting_rag_meta()})

        system_text, user_llm, rag_meta = _prepare_chat_prompts(rag_q, plain_for_rag)
        ollama_messages: list = [{"role": "system", "content": system_text}]
        if last_user_idx is None:
            for m in parsed:
                ollama_messages.append(m)
            ollama_messages.append(
                {"role": "user", "content": _trim_last_user_for_llm(user_llm)}
            )
        else:
            for i, m in enumerate(parsed):
                if i == last_user_idx:
                    ollama_messages.append(
                        {
                            "role": "user",
                            "content": _trim_last_user_for_llm(user_llm),
                        }
                    )
                else:
                    ollama_messages.append(m)
        if len(ollama_messages) < 2:
            return JsonResponse(
                {"error": "messages must include at least one user/assistant turn"},
                status=400,
            )
    else:
        message = (body.get("message") or "").strip()
        if not message:
            return JsonResponse({"error": "message or messages is required"}, status=400)
        gr = _bare_greeting_reply(message)
        if gr:
            return JsonResponse({"reply": gr, "rag": _greeting_rag_meta()})
        system_text, user_llm, rag_meta = _prepare_chat_prompts(rag_q, message)
        ollama_messages = [
            {"role": "system", "content": system_text},
            {"role": "user", "content": _trim_last_user_for_llm(user_llm)},
        ]

    reply_text, err = _call_llm(ollama_messages)
    if err:
        status = 504 if "zaman aşımı" in err.lower() or "timeout" in err.lower() else 502
        return JsonResponse({"error": err, "rag": rag_meta}, status=status)
    return JsonResponse({"reply": reply_text, "rag": rag_meta})


def _chat_with_db(request, body: dict, client_uuid: uuid.UUID) -> JsonResponse:
    message = (body.get("message") or "").strip()
    if not message:
        return JsonResponse({"error": "message is required"}, status=400)

    session_id_raw = body.get("session_id")
    if session_id_raw:
        try:
            sid = uuid.UUID(str(session_id_raw))
        except (ValueError, TypeError):
            return JsonResponse({"error": "session_id geçersiz UUID"}, status=400)
        session = get_object_or_404(ChatSession, pk=sid, client_id=client_uuid)
    else:
        session = ChatSession.objects.create(client_id=client_uuid, title="Yeni sohbet")  # pyright: ignore[reportAttributeAccessIssue]

    prior = list(session.messages.all())
    gr = _bare_greeting_reply(message)
    if gr:
        ChatMessage.objects.create(session=session, role="user", content=message)  # pyright: ignore[reportAttributeAccessIssue]
        if session.title == "Yeni sohbet" and len(prior) == 0:
            session.title = message[:197] + ("…" if len(message) > 200 else "")
            session.save(update_fields=["title"])
        ChatMessage.objects.create(session=session, role="assistant", content=gr)  # pyright: ignore[reportAttributeAccessIssue]
        session.save()
        return JsonResponse(
            {
                "reply": gr,
                "session_id": str(session.id),
                "title": session.title,
                "rag": _greeting_rag_meta(),
            }
        )

    prior_user_texts = [m.content for m in prior if m.role == "user"]
    rag_q = _compose_rag_search_query(message, prior_user_texts)
    system_text, user_llm, rag_meta = _prepare_chat_prompts(rag_q, message)
    ollama_messages: list = [{"role": "system", "content": system_text}]
    prior_window = prior[-CHAT_HISTORY_MAX_MESSAGES:]
    for m in prior_window:
        if m.role in ("user", "assistant") and m.content.strip():
            ollama_messages.append(
                {
                    "role": m.role,
                    "content": _trim_message_for_llm(m.content),
                }
            )
    ollama_messages.append(
        {"role": "user", "content": _trim_last_user_for_llm(user_llm)}
    )

    user_row = ChatMessage.objects.create(  # pyright: ignore[reportAttributeAccessIssue]
        session=session, role="user", content=message
    )
    title_changed = False
    if session.title == "Yeni sohbet" and len(prior) == 0:
        session.title = message[:197] + ("…" if len(message) > 200 else "")
        session.save(update_fields=["title"])
        title_changed = True

    reply_text, err = _call_llm(ollama_messages)
    if err:
        user_row.delete()
        if title_changed:
            session.title = "Yeni sohbet"
            session.save(update_fields=["title"])
        status = 504 if "zaman aşımı" in err.lower() or "timeout" in err.lower() else 502
        return JsonResponse(
            {"error": err, "session_id": str(session.id), "rag": rag_meta},
            status=status,
        )

    ChatMessage.objects.create(session=session, role="assistant", content=reply_text)  # pyright: ignore[reportAttributeAccessIssue]
    session.save()

    return JsonResponse(
        {
            "reply": reply_text,
            "session_id": str(session.id),
            "title": session.title,
            "rag": rag_meta,
        }
    )
