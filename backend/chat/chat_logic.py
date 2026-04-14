import os
import uuid

from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from .models import ChatMessage, ChatSession
from .llm_service import call_llm
from .message_utils import parse_client_id, trim_last_user_for_llm, trim_message_for_llm
from .rag_service import (
    RAG_META_REASON_SKIPPED_SMALLTALK,
    RAG_USER_BUBBLE_MAX_CHARS,
    compose_rag_search_query,
    prepare_chat_prompts,
    rag_query_from_request_body,
)

CHAT_HISTORY_MAX_MESSAGES = max(1, int(os.environ.get("CHAT_HISTORY_MAX_MESSAGES", "12")))


def run_chat_completion(body: dict) -> JsonResponse:
    client_uuid = parse_client_id(body.get("client_id"))
    if client_uuid is not None:
        return _chat_with_db(body, client_uuid)

    raw_history = body.get("messages")
    user_msg = (body.get("message") or "").strip()
    rag_q = rag_query_from_request_body(body)
    if isinstance(raw_history, list) and len(raw_history) > 0:
        parsed: list[dict] = []
        for item in raw_history[-CHAT_HISTORY_MAX_MESSAGES:]:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = (item.get("content") or "").strip()
            if role in ("user", "assistant") and content:
                parsed.append({"role": role, "content": trim_message_for_llm(content)})

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

        system_text, user_llm, rag_meta = prepare_chat_prompts(rag_q, plain_for_rag)
        ollama_messages: list = [{"role": "system", "content": system_text}]
        if rag_meta.get("reason") == RAG_META_REASON_SKIPPED_SMALLTALK:
            ollama_messages.append({"role": "user", "content": trim_last_user_for_llm(user_llm, RAG_USER_BUBBLE_MAX_CHARS)})
        elif last_user_idx is None:
            for m in parsed:
                ollama_messages.append(m)
            ollama_messages.append({"role": "user", "content": trim_last_user_for_llm(user_llm, RAG_USER_BUBBLE_MAX_CHARS)})
        else:
            for i, m in enumerate(parsed):
                if i == last_user_idx:
                    ollama_messages.append({"role": "user", "content": trim_last_user_for_llm(user_llm, RAG_USER_BUBBLE_MAX_CHARS)})
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
        system_text, user_llm, rag_meta = prepare_chat_prompts(rag_q, message)
        ollama_messages = [
            {"role": "system", "content": system_text},
            {"role": "user", "content": trim_last_user_for_llm(user_llm, RAG_USER_BUBBLE_MAX_CHARS)},
        ]

    reply_text, err = call_llm(ollama_messages)
    if err:
        status = 504 if "timeout" in err.lower() or "timed out" in err.lower() else 502
        return JsonResponse({"error": err, "rag": rag_meta}, status=status)
    return JsonResponse({"reply": reply_text, "rag": rag_meta})


def _chat_with_db(body: dict, client_uuid: uuid.UUID) -> JsonResponse:
    message = (body.get("message") or "").strip()
    if not message:
        return JsonResponse({"error": "message is required"}, status=400)

    session_id_raw = body.get("session_id")
    if session_id_raw:
        try:
            sid = uuid.UUID(str(session_id_raw))
        except (ValueError, TypeError):
            return JsonResponse({"error": "session_id is not a valid UUID"}, status=400)
        session = get_object_or_404(ChatSession, pk=sid, client_id=client_uuid)
    else:
        session = ChatSession.objects.create(client_id=client_uuid, title="New chat")

    prior = list(session.messages.all())
    prior_user_texts = [m.content for m in prior if m.role == "user"]
    rag_q = compose_rag_search_query(message, prior_user_texts)
    system_text, user_llm, rag_meta = prepare_chat_prompts(rag_q, message)
    ollama_messages: list = [{"role": "system", "content": system_text}]
    if rag_meta.get("reason") != RAG_META_REASON_SKIPPED_SMALLTALK:
        prior_window = prior[-CHAT_HISTORY_MAX_MESSAGES:]
        for m in prior_window:
            if m.role in ("user", "assistant") and m.content.strip():
                ollama_messages.append({"role": m.role, "content": trim_message_for_llm(m.content)})
    ollama_messages.append({"role": "user", "content": trim_last_user_for_llm(user_llm, RAG_USER_BUBBLE_MAX_CHARS)})

    user_row = ChatMessage.objects.create(session=session, role="user", content=message)
    title_changed = False
    if session.title == "New chat" and len(prior) == 0:
        session.title = message[:197] + ("…" if len(message) > 200 else "")
        session.save(update_fields=["title"])
        title_changed = True

    reply_text, err = call_llm(ollama_messages)
    if err:
        user_row.delete()
        if title_changed:
            session.title = "New chat"
            session.save(update_fields=["title"])
        status = 504 if "timeout" in err.lower() or "timed out" in err.lower() else 502
        return JsonResponse(
            {"error": err, "session_id": str(session.id), "rag": rag_meta},
            status=status,
        )

    ChatMessage.objects.create(session=session, role="assistant", content=reply_text)
    session.save()
    return JsonResponse(
        {
            "reply": reply_text,
            "session_id": str(session.id),
            "title": session.title,
            "rag": rag_meta,
        }
    )


