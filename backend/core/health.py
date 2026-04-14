"""Health check endpoint for monitoring and Docker health checks."""

import time

from django.db import connection
from django.http import JsonResponse
from drf_spectacular.utils import OpenApiResponse, extend_schema, inline_serializer
from rest_framework import serializers
from rest_framework.decorators import api_view


@extend_schema(
    summary="Health check",
    description="Returns service health status including database connectivity and latency.",
    responses={
        200: OpenApiResponse(
            description="Service is healthy",
            response=inline_serializer(
                name="HealthResponse",
                fields={
                    "status": serializers.CharField(),
                    "checks": serializers.DictField(),
                },
            ),
        ),
        503: OpenApiResponse(description="Service is unhealthy"),
    },
)
@api_view(["GET"])
def health_check(request):
    """Returns service health status including DB connectivity."""
    checks = {}

    # Database check
    try:
        start = time.time()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = {"status": "ok", "latency_ms": round((time.time() - start) * 1000, 1)}
    except Exception as e:
        checks["database"] = {"status": "error", "detail": str(e)}

    all_ok = all(c["status"] == "ok" for c in checks.values())
    return JsonResponse(
        {"status": "healthy" if all_ok else "unhealthy", "checks": checks},
        status=200 if all_ok else 503,
    )
