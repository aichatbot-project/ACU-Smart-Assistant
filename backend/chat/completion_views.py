import os

from django.http import JsonResponse
from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers, status
from rest_framework.decorators import api_view, parser_classes
from rest_framework.parsers import JSONParser

from .chat_logic import run_chat_completion

MAX_BODY_SIZE = int(os.environ.get("MAX_REQUEST_BODY_BYTES", "32768"))  # 32 KB
MAX_MESSAGE_LENGTH = int(os.environ.get("MAX_MESSAGE_LENGTH", "5000"))


@extend_schema(
    summary="Send a chat message",
    description="Send a message and receive an AI-generated reply with RAG context from ACU university data.",
    request=inline_serializer(
        name="ChatCompletionRequest",
        fields={
            "message": serializers.CharField(help_text="The user's message"),
            "client_id": serializers.UUIDField(required=False, help_text="Client UUID for session persistence"),
            "session_id": serializers.UUIDField(required=False, help_text="Existing session UUID to continue"),
            "messages": serializers.ListField(
                child=serializers.DictField(),
                required=False,
                help_text="Message history for stateless mode [{role, content}]",
            ),
        },
    ),
    responses={
        200: OpenApiResponse(
            description="Successful response with AI reply and RAG metadata",
            response=inline_serializer(
                name="ChatCompletionResponse",
                fields={
                    "reply": serializers.CharField(),
                    "session_id": serializers.UUIDField(allow_null=True),
                    "title": serializers.CharField(allow_null=True),
                    "rag": serializers.DictField(),
                },
            ),
            examples=[
                OpenApiExample(
                    "Success",
                    value={
                        "reply": "The tuition fee for undergraduate programs is...",
                        "session_id": "550e8400-e29b-41d4-a716-446655440001",
                        "title": "Tuition Fee Inquiry",
                        "rag": {
                            "embedding_ok": True,
                            "chunks_used": 3,
                            "sources": [
                                {"url": "https://www.acibadem.edu.tr/en/admissions/fees", "title": "Tuition and Fees", "cosine_distance": 0.38}
                            ],
                        },
                    },
                ),
            ],
        ),
        400: OpenApiResponse(description="Invalid request body"),
        413: OpenApiResponse(description="Request body too large"),
        502: OpenApiResponse(description="LLM service unavailable"),
    },
    examples=[
        OpenApiExample(
            "Basic message",
            request_only=True,
            value={"message": "What are the computer engineering programs?", "client_id": "550e8400-e29b-41d4-a716-446655440000"},
        ),
        OpenApiExample(
            "Stateless mode",
            request_only=True,
            value={"message": "Tell me about admissions", "messages": [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi!"}]},
        ),
    ],
)
@api_view(["POST"])
@parser_classes([JSONParser])
def chat_completion(request):
    if len(request.body) > MAX_BODY_SIZE:
        return JsonResponse(
            {"error": f"Request body too large. Max {MAX_BODY_SIZE} bytes."},
            status=413,
        )

    body = request.data

    if not isinstance(body, dict):
        return JsonResponse({"error": "Request body must be a JSON object."}, status=400)

    message = body.get("message", "")
    if isinstance(message, str) and len(message) > MAX_MESSAGE_LENGTH:
        return JsonResponse(
            {"error": f"Message too long. Max {MAX_MESSAGE_LENGTH} characters."},
            status=400,
        )

    messages = body.get("messages")
    if isinstance(messages, list):
        for item in messages:
            if isinstance(item, dict):
                content = item.get("content", "")
                if isinstance(content, str) and len(content) > MAX_MESSAGE_LENGTH:
                    return JsonResponse(
                        {"error": f"Message in history too long. Max {MAX_MESSAGE_LENGTH} characters."},
                        status=400,
                    )

    return run_chat_completion(body)
