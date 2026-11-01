from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.core.cache import cache
from django.core.files.storage import FileSystemStorage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from PIL import Image
from rest_framework.test import APIClient

from .models import ContactMessage, Inquiry, InquiryAttachment


class SubmissionIdempotencyTests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()
        self.key = str(uuid4())
        self.payload = {"name": "Retry buyer", "email": "retry@example.com", "message": "Please quote this configuration.", "privacy_consent": True}
        self.verify = patch("catalog.serializers.verify_turnstile_token").start()
        self.addCleanup(patch.stopall)
        self.inquiry_queue = patch("catalog.views.enqueue_inquiry_notification").start()
        self.contact_queue = patch("catalog.views.enqueue_contact_notification").start()

    def post(self, path, payload=None, key=None, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(path, self.payload if payload is None else payload, format="json", HTTP_IDEMPOTENCY_KEY=key or self.key, **kwargs)

    def test_retry_returns_original_reference_and_queues_once_for_both_forms(self):
        for path, model, queue in [("/api/inquiries/", Inquiry, self.inquiry_queue), ("/api/contact/", ContactMessage, self.contact_queue)]:
            with self.subTest(path=path):
                cache.clear()
                first = self.post(path)
                second = self.post(path)
                self.assertEqual((first.status_code, second.status_code), (201, 201))
                self.assertEqual(first.data, second.data)
                self.assertEqual(model.objects.count(), 1)
                queue.assert_called_once()
                saved = model.objects.get()
                self.assertEqual(len(saved.submission_key_hash), 64)
                self.assertNotIn(self.key, saved.submission_key_hash)
                self.assertEqual(len(saved.submission_payload_hash), 64)

    def test_changed_content_cannot_reuse_an_accepted_key(self):
        self.assertEqual(self.post("/api/inquiries/").status_code, 201)
        response = self.post("/api/inquiries/", self.payload | {"message": "A different requirement."})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Inquiry.objects.count(), 1)
        self.inquiry_queue.assert_called_once()

    def test_changed_content_with_new_key_is_a_new_submission(self):
        self.assertEqual(self.post("/api/contact/").status_code, 201)
        self.assertEqual(self.post("/api/contact/", self.payload | {"message": "New requirement."}, key=str(uuid4())).status_code, 201)
        self.assertEqual(ContactMessage.objects.count(), 2)

    def test_refreshed_captcha_and_tracking_context_can_replay_without_reverification(self):
        first = self.post("/api/inquiries/", self.payload | {"turnstile_token": "first-token", "utm_source": "trade-show"})
        second = self.post("/api/inquiries/", self.payload | {"turnstile_token": "fresh-token", "utm_source": "return-visit"})
        self.assertEqual(second.status_code, 201)
        self.assertEqual(first.data["reference"], second.data["reference"])
        self.verify.assert_called_once()

    def test_validation_failure_does_not_reserve_the_key(self):
        self.assertEqual(self.post("/api/contact/", self.payload | {"privacy_consent": False}).status_code, 400)
        self.assertEqual(self.post("/api/contact/").status_code, 201)
        self.assertEqual(ContactMessage.objects.count(), 1)

    def test_invalid_keys_and_non_object_payload_are_rejected(self):
        for key in ("short", "00000000-0000-0000-0000-000000000000", "x" * 100):
            with self.subTest(key=key):
                cache.clear()
                self.assertEqual(self.post("/api/inquiries/", key=key).status_code, 400)
        cache.clear()
        self.assertEqual(self.post("/api/inquiries/", payload=[]).status_code, 400)
        self.assertFalse(Inquiry.objects.exists())

    def test_versioned_api_preserves_its_response_envelope_on_retry(self):
        first = self.post("/api/v1/contact/")
        second = self.post("/api/v1/contact/")
        self.assertEqual(second.status_code, 201)
        self.assertTrue(second.json()["success"])
        self.assertEqual(first.json()["data"]["reference"], second.json()["data"]["reference"])

    def test_old_clients_without_a_key_retain_the_existing_contract(self):
        for _ in range(2):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post("/api/contact/", self.payload, format="json")
            self.assertEqual(response.status_code, 201)
        self.assertEqual(ContactMessage.objects.count(), 2)
        self.assertFalse(ContactMessage.objects.exclude(submission_key_hash=None).exists())

    @override_settings(CORS_ALLOWED_ORIGINS=["https://frontend.example.com"])
    def test_cross_origin_clients_can_send_the_idempotency_header(self):
        response = self.client.options("/api/inquiries/", HTTP_ORIGIN="https://frontend.example.com", HTTP_ACCESS_CONTROL_REQUEST_METHOD="POST", HTTP_ACCESS_CONTROL_REQUEST_HEADERS="idempotency-key")
        self.assertEqual(response.status_code, 200)
        self.assertIn("idempotency-key", response["Access-Control-Allow-Headers"])

    def test_multipart_retry_fingerprints_file_bytes_and_preserves_upload_position(self):
        field = InquiryAttachment._meta.get_field("file")
        original_storage = field.storage
        with TemporaryDirectory() as directory, patch("catalog.serializers.schedule_inquiry_attachment_scan"):
            field.storage = FileSystemStorage(location=directory)
            try:
                def upload(colour):
                    data = BytesIO()
                    Image.new("RGB", (2, 2), colour).save(data, format="PNG")
                    return SimpleUploadedFile("drawing.png", data.getvalue(), content_type="image/png")

                responses = []
                for colour in ("red", "red", "blue"):
                    with self.captureOnCommitCallbacks(execute=True):
                        responses.append(self.client.post("/api/inquiries/", self.payload | {"attachments": upload(colour)}, format="multipart", HTTP_IDEMPOTENCY_KEY=self.key))
                self.assertEqual([r.status_code for r in responses], [201, 201, 409])
                self.assertEqual(Inquiry.objects.count(), 1)
                self.assertEqual(InquiryAttachment.objects.count(), 1)
                with InquiryAttachment.objects.get().file.open("rb") as saved:
                    self.assertEqual(saved.read(), upload("red").read())
            finally:
                field.storage = original_storage


@skipUnless(connection.vendor == "postgresql", "Concurrent uniqueness requires PostgreSQL")
class ConcurrentSubmissionTests(TransactionTestCase):
    def test_concurrent_requests_only_create_and_notify_once(self):
        cache.clear()
        barrier = Barrier(2)
        key = str(uuid4())

        def send(_number):
            close_old_connections()
            try:
                return APIClient().post("/api/contact/", {"name": "Concurrent buyer", "email": "parallel@example.com", "message": "Same request.", "privacy_consent": True}, format="json", HTTP_IDEMPOTENCY_KEY=key)
            finally:
                close_old_connections()

        with patch("catalog.serializers.verify_turnstile_token", side_effect=lambda *_args, **_kwargs: barrier.wait(timeout=15)), patch("catalog.views.enqueue_contact_notification") as queue:
            with ThreadPoolExecutor(max_workers=2) as pool:
                responses = list(pool.map(send, range(2)))
        self.assertEqual([r.status_code for r in responses], [201, 201])
        self.assertEqual(responses[0].data["reference"], responses[1].data["reference"])
        self.assertEqual(ContactMessage.objects.count(), 1)
        queue.assert_called_once()
