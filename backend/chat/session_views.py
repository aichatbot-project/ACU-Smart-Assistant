from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import api_view

from .chat_logic import parse_client_id
from .models import ChatSession


@extend_schema(
    summary="List chat sessions",
    description="Returns all chat sessions for a given client UUID, ordered by most recently updated.",
    parameters=[
        OpenApiParameter(name="client_id", type=str, location="query", required=True, description="Client UUID"),
    ],
    responses={
        200: OpenApiResponse(
            description="List of sessions",
            response=inline_serializer(
                name="SessionListResponse",
                fields={
                    "sessions": serializers.ListField(
                        child=serializers.DictField(),
                        help_text="Array of {id, title, updated_at}",
                    ),
                },
            ),
        ),
        400: OpenApiResponse(description="Missing or invalid client_id"),
    },
)
@api_view(["GET"])
def list_sessions(request):
    cid = parse_client_id(
        request.GET.get("client_id") or request.headers.get("X-Client-Id")
    )
    if cid is None:
        return JsonResponse({"error": "client_id is required (UUID)"}, status=400)
    sessions = ChatSession.objects.filter(client_id=cid)[:100]
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


@extend_schema(
    methods=["GET"],
    summary="Get session detail",
    description="Returns a single chat session with all its messages.",
    parameters=[
        OpenApiParameter(name="client_id", type=str, location="query", required=True, description="Client UUID"),
    ],
    responses={
        200: OpenApiResponse(
            description="Session with messages",
            response=inline_serializer(
                name="SessionDetailResponse",
                fields={
                    "session_id": serializers.UUIDField(),
                    "title": serializers.CharField(),
                    "messages": serializers.ListField(
                        child=serializers.DictField(),
                        help_text="Array of {id, role, content, timestamp}",
                    ),
                },
            ),
        ),
        400: OpenApiResponse(description="Missing or invalid client_id"),
        404: OpenApiResponse(description="Session not found"),
    },
)
@extend_schema(
    methods=["DELETE"],
    summary="Delete a session",
    description="Deletes a chat session and all its messages.",
    parameters=[
        OpenApiParameter(name="client_id", type=str, location="query", required=True, description="Client UUID"),
    ],
    responses={
        200: OpenApiResponse(description="Session deleted", response=inline_serializer(name="DeleteResponse", fields={"ok": serializers.BooleanField()})),
        400: OpenApiResponse(description="Missing or invalid client_id"),
        404: OpenApiResponse(description="Session not found"),
    },
)
@api_view(["GET", "DELETE"])
def session_detail(request, pk):
    cid = parse_client_id(
        request.GET.get("client_id") or request.headers.get("X-Client-Id")
    )
    if cid is None:
        return JsonResponse({"error": "client_id is required (query, UUID)"}, status=400)

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
