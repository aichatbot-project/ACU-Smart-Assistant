import json
import os
import re
import socket
import urllib.error
import urllib.request

LLM_BACKEND = os.environ.get("LLM_BACKEND", "ollama")
OLLAMA_BASE = os.environ.get("OLLAMA_BASE_URL", "http://127.0.0.1:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
# Lower defaults = faster on CPU; override via env for longer answers / more context.
OLLAMA_NUM_PREDICT = int(os.environ.get("OLLAMA_NUM_PREDICT", "256"))
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
        return None, f"Claude API error: {detail}"
    except urllib.error.URLError as e:
        return None, f"Claude API connection error: {e.reason}"

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
        return None, detail or e.reason
    except urllib.error.URLError as e:
        err = str(e.reason)
        if isinstance(e.reason, TimeoutError) or "timed out" in err.lower():
            return None, (
                "Ollama response timed out. Try increasing Docker RAM, lowering "
                "OLLAMA_NUM_PREDICT, or using a smaller model."
            )
        return None, err
    except socket.timeout:
        return None, "Ollama socket timeout. The server or model may be too slow."

    reply = (data.get("message") or {}).get("content", "").strip()
    if not reply:
        return None, "Empty model response"
    return reply, None


def call_llm(messages: list) -> tuple[str | None, str | None]:
    if LLM_BACKEND == "claude" and ANTHROPIC_API_KEY:
        reply, err = _call_claude(messages)
    else:
        reply, err = _call_ollama(messages)
    if err or not reply:
        return reply, err
    return _sanitize_assistant_reply(reply), None
