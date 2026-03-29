"""HTTP middleware: correlation IDs for tracing requests across logs."""

from __future__ import annotations

import uuid

CORRELATION_HEADER = "HTTP_X_REQUEST_ID"


class CorrelationIdMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        cid = request.META.get(CORRELATION_HEADER) or str(uuid.uuid4())
        request.correlation_id = cid
        response = self.get_response(request)
        response["X-Request-ID"] = cid
        return response
