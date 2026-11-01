from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from tempfile import SpooledTemporaryFile, TemporaryDirectory
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.core.asgi import get_asgi_application
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.uploadhandler import TemporaryFileUploadHandler
from django.http import JsonResponse
from django.test import RequestFactory, SimpleTestCase, override_settings
from django.urls import path
from django.views.decorators.csrf import csrf_exempt
from rest_framework.test import APIClient

from .upload_limits import RequestBodyLimitASGI, RequestBodyLimitMiddleware, UploadLimitExceeded


@csrf_exempt
def echo_body(request):
    return JsonResponse({"body": request.body.decode("ascii")})


urlpatterns = [path("echo/", echo_body)]


class TrackingTemporaryUploadHandler(TemporaryFileUploadHandler):
    files = []
    bytes_written = 0

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self.files.append(self.file)

    def receive_data_chunk(self, raw_data, start):
        type(self).bytes_written += len(raw_data)
        return super().receive_data_chunk(raw_data, start)


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    FILE_UPLOAD_HANDLERS=[
        "catalog.upload_limits.RFQUploadLimitHandler",
        "catalog.test_upload_limits.TrackingTemporaryUploadHandler",
    ],
    MAX_UPLOAD_BYTES=16 * 1024,
    MAX_TOTAL_UPLOAD_BYTES=24 * 1024,
    MAX_UPLOAD_FILES=2,
    MAX_REQUEST_BODY_BYTES=128 * 1024,
    TURNSTILE_REQUIRED=True,
    TURNSTILE_SECRET_KEY="isolated-upload-limit-tests",
)
class RFQStreamingUploadTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        TrackingTemporaryUploadHandler.files = []
        TrackingTemporaryUploadHandler.bytes_written = 0
        self.directory = TemporaryDirectory(prefix="upload-limit-tests-")
        self.addCleanup(self.directory.cleanup)
        temporary_settings = override_settings(FILE_UPLOAD_TEMP_DIR=self.directory.name)
        temporary_settings.enable()
        self.addCleanup(temporary_settings.disable)
        verifier = patch("catalog.serializers.verify_turnstile_token")
        self.verify = verifier.start()
        self.addCleanup(verifier.stop)
        queue = patch("catalog.views.enqueue_inquiry_notification")
        self.queue = queue.start()
        self.addCleanup(queue.stop)

    def assert_cleaned(self):
        self.assertTrue(all(upload.closed for upload in TrackingTemporaryUploadHandler.files))
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])
        self.verify.assert_not_called()
        self.queue.assert_not_called()

    def post_files(self, path, files):
        return APIClient().post(path, files, format="multipart")

    def test_oversized_single_file_stops_before_complete_file_is_written(self):
        for path in ("/api/inquiries/", "/api/v1/inquiries/"):
            with self.subTest(path=path):
                cache.clear()
                TrackingTemporaryUploadHandler.bytes_written = 0
                response = self.post_files(path, {
                    "attachments": SimpleUploadedFile("large.pdf", b"x" * (96 * 1024)),
                })
                self.assertEqual(response.status_code, 413, response.content)
                self.assertLessEqual(TrackingTemporaryUploadHandler.bytes_written, 16 * 1024)
                if path.startswith("/api/v1/"):
                    self.assertEqual(response.json()["error_code"], "PAYLOAD_TOO_LARGE")
                    self.assertFalse(response.json()["success"])
                self.assert_cleaned()

    def test_total_limit_closes_previously_completed_and_current_files(self):
        response = self.post_files("/api/inquiries/", {
            "attachments": [
                SimpleUploadedFile("first.pdf", b"x" * (16 * 1024)),
                SimpleUploadedFile("second.pdf", b"y" * (16 * 1024)),
            ],
        })
        self.assertEqual(response.status_code, 413, response.content)
        self.assertEqual(len(TrackingTemporaryUploadHandler.files), 2)
        self.assertLessEqual(TrackingTemporaryUploadHandler.bytes_written, 24 * 1024)
        self.assert_cleaned()

    def test_file_count_limit_counts_unknown_field_names_too(self):
        response = self.post_files("/api/inquiries/", {
            "attachments": SimpleUploadedFile("first.pdf", b"first"),
            "ignored": SimpleUploadedFile("second.pdf", b"second"),
            "also_ignored": SimpleUploadedFile("third.pdf", b"third"),
        })
        self.assertEqual(response.status_code, 413, response.content)
        self.assertEqual(len(TrackingTemporaryUploadHandler.files), 2)
        self.assert_cleaned()

    def test_unknown_field_cannot_bypass_per_file_limit(self):
        response = self.post_files("/api/v1/inquiries/", {
            "ignored": SimpleUploadedFile("large.pdf", b"x" * (96 * 1024)),
        })
        self.assertEqual(response.status_code, 413, response.content)
        self.assert_cleaned()

    def test_multipart_parser_does_not_drain_rejected_wsgi_input(self):
        request = RequestFactory().post("/api/inquiries/", {
            "attachments": SimpleUploadedFile("large.pdf", b"x" * (96 * 1024)),
        })
        read = request._stream.read
        bytes_read = []

        def tracked_read(*args, **kwargs):
            data = read(*args, **kwargs)
            bytes_read.append(len(data))
            return data

        try:
            with patch.object(request._stream, "read", side_effect=tracked_read):
                with self.assertRaises(UploadLimitExceeded):
                    _ = request.FILES
            self.assertLess(sum(bytes_read), int(request.META["CONTENT_LENGTH"]))
        finally:
            request.close()
        self.assert_cleaned()

    def test_files_at_both_limits_continue_to_normal_form_validation(self):
        response = self.post_files("/api/inquiries/", {
            "attachments": [
                SimpleUploadedFile("first.pdf", b"x" * (16 * 1024)),
                SimpleUploadedFile("second.pdf", b"y" * (8 * 1024)),
            ],
        })
        # Missing required fields are normal 400 validation errors, not 413.
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(TrackingTemporaryUploadHandler.bytes_written, 24 * 1024)
        self.assert_cleaned()

    def test_admin_uploads_are_not_subject_to_rfq_file_limits(self):
        request = RequestFactory().post("/admin/catalog/product/add/", {
            "file": SimpleUploadedFile("admin.pdf", b"x" * (32 * 1024)),
        })
        try:
            self.assertEqual(request.FILES["file"].size, 32 * 1024)
        finally:
            request.close()
        self.assertEqual(TrackingTemporaryUploadHandler.bytes_written, 32 * 1024)
        self.assert_cleaned()


@override_settings(MAX_REQUEST_BODY_BYTES=16)
class RequestBodyLimitTests(SimpleTestCase):
    def test_declared_size_is_rejected_without_reading_body_or_calling_view(self):
        request = RequestFactory().post("/api/v1/inquiries/", data=b"", content_type="application/octet-stream")
        request.META["CONTENT_LENGTH"] = "17"
        request._stream = io.BytesIO(b"do not read")
        with patch.object(request._stream, "read", side_effect=AssertionError("Body was read")):
            response = RequestBodyLimitMiddleware(
                lambda request: self.fail("View was called")
            )(request)
        self.assertEqual(response.status_code, 413)
        self.assertEqual(json.loads(response.content)["error_code"], "PAYLOAD_TOO_LARGE")

    def test_declared_size_at_limit_is_allowed(self):
        request = RequestFactory().post("/any/path/", data=b"x" * 16, content_type="application/octet-stream")
        response = RequestBodyLimitMiddleware(lambda request: JsonResponse({"ok": True}))(request)
        self.assertEqual(response.status_code, 200)


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    ROOT_URLCONF=__name__,
    MAX_REQUEST_BODY_BYTES=16,
    FILE_UPLOAD_MAX_MEMORY_SIZE=1,
    CORS_ALLOWED_ORIGINS=["https://frontend.example.com"],
)
class ASGIRequestBodyLimitTests(SimpleTestCase):
    def run_request(self, chunks, headers=(), path="/api/v1/inquiries/"):
        scope = {
            "type": "http", "http_version": "1.1", "method": "POST",
            "path": path, "query_string": b"", "scheme": "http",
            "headers": [(b"host", b"testserver"), *headers],
            "server": ("testserver", 80), "client": ("127.0.0.1", 1234),
        }
        spools = []
        written_bytes = []
        consumed = []
        sent = []

        def track_spool(*args, **kwargs):
            spool = SpooledTemporaryFile(*args, **kwargs)
            write = spool.write
            spool.write = lambda value: (written_bytes.append(len(value)), write(value))[1]
            spools.append(spool)
            return spool

        async def exchange():
            remaining = iter(chunks)

            async def receive():
                try:
                    body, more = next(remaining)
                except StopIteration:
                    await asyncio.Event().wait()  # Cancelled after the response.
                consumed.append(len(body))
                return {"type": "http.request", "body": body, "more_body": more}

            async def send(message):
                sent.append(message)

            app = RequestBodyLimitASGI(get_asgi_application())
            await asyncio.wait_for(app(scope, receive, send), timeout=5)

        with patch("django.core.handlers.asgi.tempfile.SpooledTemporaryFile", side_effect=track_spool):
            async_to_sync(exchange)()
        self.assertTrue(all(spool.closed for spool in spools))
        return sent, consumed, sum(written_bytes)

    def test_large_declared_size_is_rejected_before_django_creates_spool(self):
        sent, consumed, written = self.run_request([(b"unused", False)], [(b"content-length", b"17")])
        self.assertEqual(sent[0]["status"], 413)
        self.assertEqual(consumed, [])
        self.assertEqual(written, 0)

    def test_actual_stream_is_limited_without_trusting_content_length(self):
        for headers in ([], [(b"content-length", b"1")]):
            with self.subTest(headers=headers):
                sent, consumed, written = self.run_request(
                    [(b"x" * 10, True), (b"x" * 10, True), (b"unread", False)], headers,
                )
                self.assertEqual(sent[0]["status"], 413)
                self.assertEqual(len(consumed), 2)
                self.assertEqual(written, 10)
                self.assertEqual(json.loads(sent[1]["body"])["error_code"], "PAYLOAD_TOO_LARGE")

    def test_early_rejection_allows_only_approved_cors_origins(self):
        for origin, approved in ((b"https://frontend.example.com", True), (b"https://attacker.example.com", False)):
            with self.subTest(origin=origin):
                sent, _, _ = self.run_request([], [(b"content-length", b"17"), (b"origin", origin)])
                headers = dict(sent[0]["headers"])
                self.assertEqual(headers.get(b"access-control-allow-origin"), origin if approved else None)

    def test_limit_also_applies_to_unknown_paths(self):
        sent, consumed, _ = self.run_request([(b"x" * 17, False)], path="/unknown/")
        self.assertEqual(sent[0]["status"], 413)
        self.assertEqual(consumed, [17])

    def test_body_at_limit_reaches_django_unchanged(self):
        sent, consumed, written = self.run_request(
            [(b"x" * 8, True), (b"y" * 8, False)],
            [(b"content-length", b"16")], path="/echo/",
        )
        self.assertEqual(sent[0]["status"], 200)
        content = b"".join(message.get("body", b"") for message in sent[1:])
        self.assertEqual(json.loads(content)["body"], "xxxxxxxxyyyyyyyy")
        self.assertEqual(consumed, [8, 8])
        self.assertEqual(written, 16)
