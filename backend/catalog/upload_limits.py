"""Bound request buffering and RFQ file uploads before storing excess bytes."""

from django.conf import settings
from django.core.files.uploadhandler import FileUploadHandler, StopUpload
from django.http import JsonResponse
from django.utils.cache import patch_vary_headers
from rest_framework.exceptions import APIException


RFQ_UPLOAD_PATHS = frozenset({"/api/inquiries", "/api/v1/inquiries"})


class UploadLimitExceeded(APIException):
    status_code = 413
    default_detail = "The upload exceeds the allowed size or file count."
    default_code = "payload_too_large"


def body_exceeds_limit(content_length) -> bool:
    try:
        return int(content_length) > settings.MAX_REQUEST_BODY_BYTES
    except (TypeError, ValueError):
        # Actual ASGI bytes are counted independently; WSGI's LimitedStream
        # also enforces the length provided by its HTTP server.
        return False


def body_limit_response(path: str, origin: str = "") -> JsonResponse:
    detail = "The request body exceeds the allowed size."
    if path.startswith("/api/v1/"):
        payload = {
            "success": False,
            "error_code": "PAYLOAD_TOO_LARGE",
            "message": detail,
            "fields": {},
        }
    else:
        payload = {"detail": detail}
    response = JsonResponse(payload, status=413)
    # ASGI rejections happen outside CorsMiddleware. Preserve access for the
    # same explicitly approved frontend origins without reflecting others.
    if origin in settings.CORS_ALLOWED_ORIGINS:
        response["Access-Control-Allow-Origin"] = origin
    patch_vary_headers(response, ("Origin",))
    return response


class RequestBodyLimitMiddleware:
    """Reject oversized declared bodies before CSRF or multipart parsing."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if body_exceeds_limit(request.META.get("CONTENT_LENGTH")):
            return body_limit_response(request.path_info)
        return self.get_response(request)


class RFQUploadLimitHandler(FileUploadHandler):
    """Count every RFQ file part before memory/disk upload handlers see it."""

    def __init__(self, request=None):
        super().__init__(request)
        self.enabled = bool(
            request is not None
            and request.method == "POST"
            and request.path_info.rstrip("/") in RFQ_UPLOAD_PATHS
        )
        self.file_count = 0
        self.total_bytes = 0
        self.error = None

    def reject(self, detail):
        self.error = detail
        # The multipart parser closes the current partial file without
        # draining the remaining input. upload_complete then raises, causing
        # its error cleanup to close all previously completed files too.
        raise StopUpload(connection_reset=True)

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        if self.enabled:
            self.file_count += 1
            if self.file_count > settings.MAX_UPLOAD_FILES:
                self.reject(f"Upload no more than {settings.MAX_UPLOAD_FILES} files.")

    def receive_data_chunk(self, raw_data, start):
        if self.enabled:
            if start + len(raw_data) > settings.MAX_UPLOAD_BYTES:
                self.reject("An attachment exceeds the per-file upload limit.")
            self.total_bytes += len(raw_data)
            if self.total_bytes > settings.MAX_TOTAL_UPLOAD_BYTES:
                self.reject("Combined attachments exceed the total upload limit.")
        return raw_data

    def file_complete(self, file_size):
        # The next handler owns the UploadedFile object.
        return None

    def upload_complete(self):
        if self.error:
            raise UploadLimitExceeded(self.error)


class RequestBodyLimitASGI:
    """Bound actual body bytes before Django spools an ASGI request to disk."""

    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.application(scope, receive, send)

        if any(
            name.lower() == b"content-length" and body_exceeds_limit(value)
            for name, value in scope.get("headers", [])
        ):
            await self.send_rejection(scope, send)
            return

        received_bytes = 0
        rejected = False
        body_complete = False

        async def limited_receive():
            nonlocal received_bytes, rejected, body_complete
            message = await receive()
            if not body_complete and message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > settings.MAX_REQUEST_BODY_BYTES:
                    rejected = True
                    # Django closes its body spool on disconnect. Raising an
                    # arbitrary exception here would bypass that cleanup.
                    return {"type": "http.disconnect"}
                body_complete = not message.get("more_body", False)
            return message

        await self.application(scope, limited_receive, send)
        if rejected:
            await self.send_rejection(scope, send)

    @staticmethod
    async def send_rejection(scope, send):
        origins = [value.decode("latin1") for name, value in scope.get("headers", [])
                   if name.lower() == b"origin"]
        origin = origins[0] if len(origins) == 1 else ""
        response = body_limit_response(scope.get("path", ""), origin)
        await send({
            "type": "http.response.start",
            "status": response.status_code,
            "headers": [(key.lower().encode("ascii"), value.encode("latin1"))
                        for key, value in response.items()],
        })
        await send({"type": "http.response.body", "body": response.content})
