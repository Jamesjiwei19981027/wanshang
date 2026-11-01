from __future__ import annotations

import math

from django.conf import settings
from rest_framework.renderers import JSONRenderer


ERROR_CODES = {
    400: "VALIDATION_ERROR",
    401: "AUTHENTICATION_REQUIRED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "RATE_LIMITED",
}


class VersionedJSONRenderer(JSONRenderer):
    """Apply the documented envelope only to the public versioned API.

    The unversioned ``/api/`` surface remains backward compatible with the
    existing website while external consumers get the stable v1 contract.
    """

    def render(self, data, accepted_media_type=None, renderer_context=None):
        context = renderer_context or {}
        request = context.get("request")
        response = context.get("response")
        path = getattr(request, "path", "")
        if not path.startswith("/api/v1/") or response is None:
            return super().render(data, accepted_media_type, renderer_context)

        if response.status_code >= 400:
            detail = data.get("detail") if isinstance(data, dict) else None
            fields = (
                {key: value for key, value in data.items() if key != "detail"}
                if isinstance(data, dict)
                else {}
            )
            payload = {
                "success": False,
                "error_code": ERROR_CODES.get(response.status_code, "API_ERROR"),
                "message": str(detail or "The request could not be completed."),
                "fields": fields,
            }
            return super().render(payload, accepted_media_type, renderer_context)

        message = ""
        content = data
        if isinstance(data, dict) and "message" in data:
            content = dict(data)
            message = str(content.pop("message") or "")

        if isinstance(content, dict) and {"count", "results"}.issubset(content):
            page_size = int(settings.REST_FRAMEWORK.get("PAGE_SIZE", 12))
            paginator = getattr(context.get("view"), "paginator", None)
            paginator_page = getattr(paginator, "page", None)
            if paginator_page is not None:
                page = paginator_page.number
                page_size = paginator_page.paginator.per_page
            else:
                try:
                    page = max(1, int(request.query_params.get("page", 1)))
                except (TypeError, ValueError):
                    page = 1
            total = int(content.get("count", 0))
            content = {
                "results": content.get("results", []),
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": math.ceil(total / page_size) if total else 0,
            }

        payload = {"success": True, "data": content, "message": message}
        return super().render(payload, accepted_media_type, renderer_context)
