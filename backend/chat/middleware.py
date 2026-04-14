"""Simple in-memory rate limiter for chat API endpoints."""

import os
import time
from collections import defaultdict

from django.http import JsonResponse

# Trusted proxy IPs that are allowed to set X-Forwarded-For.
# In Docker, the Nginx container forwards requests to Django.
_TRUSTED_PROXIES = frozenset(
    p.strip()
    for p in os.environ.get("TRUSTED_PROXY_IPS", "127.0.0.1,172.16.0.0/12,10.0.0.0/8,192.168.0.0/16").split(",")
    if p.strip()
)


def _ip_in_trusted_range(ip: str) -> bool:
    """Check if IP is in a trusted proxy range (simple prefix match for private ranges)."""
    if ip in _TRUSTED_PROXIES:
        return True
    for proxy in _TRUSTED_PROXIES:
        if "/" in proxy:
            prefix = proxy.split("/")[0]
            # Simple prefix check for common private ranges
            parts = prefix.split(".")
            if ip.startswith(".".join(parts[:2]) + "."):
                return True
    return False


class RateLimitMiddleware:
    """
    Limits requests per IP to chat API endpoints.

    Configurable via Django settings:
        RATE_LIMIT_REQUESTS: max requests per window (default 30)
        RATE_LIMIT_WINDOW: window size in seconds (default 60)
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.requests = defaultdict(list)

    def __call__(self, request):
        if not request.path.startswith("/api/chat"):
            return self.get_response(request)

        from django.conf import settings

        max_requests = getattr(settings, "RATE_LIMIT_REQUESTS", 30)
        window = getattr(settings, "RATE_LIMIT_WINDOW", 60)

        ip = self._get_client_ip(request)
        now = time.time()

        # Clean old entries
        self.requests[ip] = [t for t in self.requests[ip] if now - t < window]

        if len(self.requests[ip]) >= max_requests:
            return JsonResponse(
                {"error": f"Rate limit exceeded. Max {max_requests} requests per {window}s."},
                status=429,
            )

        self.requests[ip].append(now)
        return self.get_response(request)

    def _get_client_ip(self, request):
        remote_addr = request.META.get("REMOTE_ADDR", "unknown")
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
        if forwarded and _ip_in_trusted_range(remote_addr):
            return forwarded.split(",")[0].strip()
        return remote_addr
