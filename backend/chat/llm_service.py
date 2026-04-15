import json
import logging
import os
import re
import socket
import time
import urllib.error
import urllib.request

logger = logging.getLogger("chat.llm")

LLM_BACKEND = os.environ.get("LLM_BACKEND", "ollama")
OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
# Lower predict = faster replies on CPU; raise via env if answers truncate too often.
OLLAMA_NUM_PREDICT = int(os.environ.get("OLLAMA_NUM_PREDICT", "180"))
OLLAMA_NUM_CTX = int(os.environ.get("OLLAMA_NUM_CTX", "4096"))
OLLAMA_HTTP_TIMEOUT = int(os.environ.get("OLLAMA_HTTP_TIMEOUT", "240"))
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30m")
OLLAMA_TEMPERATURE = float(os.environ.get("OLLAMA_TEMPERATURE", "0.15"))
OLLAMA_TOP_P = float(os.environ.get("OLLAMA_TOP_P", "0.85"))

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-haiku-4-5-20251001")


def _sanitize_assistant_reply(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return t
    t = re.sub(
        r"(?i)according\s+to\s*,?\s*={3}\s*CONTEXT\s*={3}\s*[, ]*",
        "Based on the university website, ",
        t,
    )
    t = re.sub(
        r"(?i)based\s+on\s*,?\s*={3}\s*CONTEXT\s*={3}\s*[, ]*",
        "Based on the university website, ",
        t,
    )
    for leak in ("===CONTEXT===", "===QUESTION===", "===END_QUESTION==="):
        t = t.replace(leak, "")
    t = re.sub(r"(?i)^according\s+to\s*,\s*", "Based on the university website, ", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _call_claude(messages: list) -> tuple[str | None, str | None]:
    system_text = ""
    api_messages = []
    for m in messages:
        if m["role"] == "system":
            system_text = m["content"]
        else:
            api_messages.append({"role": m["role"], "content": m["content"]})

    payload = json.dumps(
        {
            "model": CLAUDE_MODEL,
            "max_tokens": 256,
            "system": system_text,
            "messages": api_messages,
        }
    ).encode("utf-8")
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
        logger.error("Claude API HTTP %d: %s", e.code, detail[:500])
        return None, "Claude API error. Please try again later."
    except urllib.error.URLError as e:
        logger.error("Claude API connection error: %s", e.reason)
        return None, "Claude API is unreachable. Please try again later."

    content_blocks = data.get("content", [])
    reply = ""
    for block in content_blocks:
        if block.get("type") == "text":
            reply += block.get("text", "")
    reply = reply.strip()
    if not reply:
        return None, "Claude returned an empty response"
    return reply, None


def _call_ollama(ollama_messages: list) -> tuple[str | None, str | None]:
    payload = json.dumps(
        {
            "model": OLLAMA_MODEL,
            "messages": ollama_messages,
            "stream": False,
            "keep_alive": OLLAMA_KEEP_ALIVE,
            "options": {
                "num_predict": OLLAMA_NUM_PREDICT,
                "num_ctx": OLLAMA_NUM_CTX,
                "temperature": OLLAMA_TEMPERATURE,
                "top_p": OLLAMA_TOP_P,
            },
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA_BASE}/api/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=OLLAMA_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        logger.error("Ollama HTTP %d: %s", e.code, detail[:500])
        return None, "LLM service error. Please try again later."
    except urllib.error.URLError as e:
        err = str(e.reason)
        logger.error("Ollama connection error: %s", err)
        if isinstance(e.reason, TimeoutError) or "timed out" in err.lower():
            return None, "Response timed out. Please try again."
        return None, "LLM service is unreachable. Please try again later."
    except socket.timeout:
        logger.error("Ollama socket timeout")
        return None, "Response timed out. Please try again."

    reply = (data.get("message") or {}).get("content", "").strip()
    if not reply:
        return None, "Empty model response"
    return reply, None


def call_llm(messages: list) -> tuple[str | None, str | None]:
    backend = "claude" if (LLM_BACKEND == "claude" and ANTHROPIC_API_KEY) else "ollama"
    logger.info("LLM call: backend=%s, messages=%d", backend, len(messages))
    start = time.time()

    if backend == "claude":
        reply, err = _call_claude(messages)
    else:
        reply, err = _call_ollama(messages)

    elapsed = round(time.time() - start, 2)
    if err:
        logger.warning("LLM error: backend=%s, elapsed=%.2fs, error=%s", backend, elapsed, err[:200])
        return reply, err
    if not reply:
        logger.warning("LLM empty response: backend=%s, elapsed=%.2fs", backend, elapsed)
        return reply, err

    logger.info("LLM reply: backend=%s, elapsed=%.2fs, chars=%d", backend, elapsed, len(reply))
    return _sanitize_assistant_reply(reply), None
