from __future__ import annotations

import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from .models import ContactMessage, Inquiry
from .throttles import (
    AttachmentUploadIpThrottle,
    ContactEmailThrottle,
    ContactIpThrottle,
    RFQEmailThrottle,
    RFQIpThrottle,
)
from .turnstile import TurnstileVerificationError


RFQ_PATHS = ("/api/inquiries/", "/api/v1/inquiries/")
CONTACT_PATHS = (
    "/api/contact/",
    "/api/contact-messages/",
    "/api/v1/contact/",
    "/api/v1/contact-messages/",
)
FORM_PATHS = RFQ_PATHS + CONTACT_PATHS


class ValidatedEmailThrottleTests(SimpleTestCase):
    def test_email_keys_preserve_normalization_hash_and_family_scopes(self):
        digest = hashlib.sha256(b"buyer@example.com").hexdigest()
        for throttle_class, scope in (
            (RFQEmailThrottle, "rfq_email"),
            (ContactEmailThrottle, "contact_email"),
        ):
            with self.subTest(scope=scope):
                # Neither raw request data nor a request object is needed.
                throttle = throttle_class(email=" Buyer@Example.com ")
                self.assertEqual(
                    throttle.get_cache_key(None, None),
                    f"throttle_validated_{scope}_{digest}",
                )


@override_settings(
    TURNSTILE_REQUIRED=True,
    TURNSTILE_SECRET_KEY="isolated-form-throttle-tests",
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "public-form-throttle-tests",
        },
    },
)
class PublicFormThrottleTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.media = TemporaryDirectory(prefix="public-form-throttle-tests-")
        self.addCleanup(self.media.cleanup)
        media_settings = override_settings(
            MEDIA_ROOT=str(Path(self.media.name) / "public"),
            PRIVATE_MEDIA_ROOT=str(Path(self.media.name) / "private"),
        )
        media_settings.enable()
        self.addCleanup(media_settings.disable)
        self.verify = self.start_patch("catalog.serializers.verify_turnstile_token")
        self.rfq_queue = self.start_patch("catalog.views.enqueue_inquiry_notification")
        self.contact_queue = self.start_patch("catalog.views.enqueue_contact_notification")
        self.start_patch("catalog.tasks.send_mail", side_effect=AssertionError("No mail in tests"))
        self.start_patch("socket.create_connection", side_effect=AssertionError("No external network in tests"))
        self.start_patch("socket.socket.connect", side_effect=AssertionError("No external network in tests"))
        self.reset_attempts()

    def start_patch(self, target, **kwargs):
        patcher = patch(target, **kwargs)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        return mocked

    def reset_attempts(self):
        cache.clear()
        self.verify.reset_mock(side_effect=True)
        self.rfq_queue.reset_mock()
        self.contact_queue.reset_mock()

    @staticmethod
    def attempt_limit(path):
        throttle_class = RFQIpThrottle if path in RFQ_PATHS else ContactIpThrottle
        return throttle_class().num_requests

    @staticmethod
    def valid_payload(email="victim@example.com"):
        return {
            "name": "Legitimate Buyer",
            "email": email,
            "message": "Please quote the required components.",
            "privacy_consent": True,
            "turnstile_token": "verified-test-token",
        }

    def post(self, path, payload, *, ip, format="json"):
        # Execute on_commit callbacks so queue assertions detect side effects.
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(path, payload, format=format, REMOTE_ADDR=ip)

    @staticmethod
    def counts():
        return Inquiry.objects.count(), ContactMessage.objects.count()

    def assert_unchanged(self, before):
        self.assertEqual(self.counts(), before)
        self.rfq_queue.assert_not_called()
        self.contact_queue.assert_not_called()

    def assert_created(self, response, path, before):
        self.assertEqual(response.status_code, 201, response.content[:500])
        expected = (
            (before[0] + 1, before[1])
            if path in RFQ_PATHS
            else (before[0], before[1] + 1)
        )
        self.assertEqual(self.counts(), expected)
        expected_queue = self.rfq_queue if path in RFQ_PATHS else self.contact_queue
        other_queue = self.contact_queue if path in RFQ_PATHS else self.rfq_queue
        expected_queue.assert_called_once()
        other_queue.assert_not_called()

    def assert_throttled(self, response, path):
        self.assertEqual(response.status_code, 429, response.content[:500])
        self.assertGreater(int(response["Retry-After"]), 0)
        self.assertLessEqual(int(response["Retry-After"]), 3600)
        payload = response.json()
        if path.startswith("/api/v1/"):
            self.assertEqual(set(payload), {"success", "error_code", "message", "fields"})
            self.assertFalse(payload["success"])
            self.assertEqual(payload["error_code"], "RATE_LIMITED")
            self.assertEqual(payload["fields"], {})
        else:
            self.assertEqual(set(payload), {"detail"})

    def test_already_blocked_ip_cannot_poison_a_different_email(self):
        for path in FORM_PATHS:
            with self.subTest(path=path):
                self.reset_attempts()
                before = self.counts()
                limit = self.attempt_limit(path)
                for index in range(limit):
                    response = self.post(
                        path, {"email": f"attacker-{index}@example.com"}, ip="203.0.113.40",
                    )
                    self.assertEqual(response.status_code, 400)
                for _ in range(limit):
                    response = self.post(path, {"email": "victim@example.com"}, ip="203.0.113.40")
                    self.assert_throttled(response, path)
                self.verify.assert_not_called()
                self.assert_unchanged(before)
                victim = self.post(path, self.valid_payload(), ip="198.51.100.80")
                self.assert_created(victim, path, before)

    def test_field_validation_failures_do_not_spend_email_quota(self):
        for path in FORM_PATHS:
            with self.subTest(path=path):
                self.reset_attempts()
                before = self.counts()
                for index in range(self.attempt_limit(path)):
                    response = self.post(
                        path, {"email": "victim@example.com"}, ip=f"203.0.113.{index + 1}",
                    )
                    self.assertEqual(response.status_code, 400)
                self.verify.assert_not_called()
                self.assert_unchanged(before)
                victim = self.post(path, self.valid_payload(), ip="198.51.100.80")
                self.assert_created(victim, path, before)

    def test_captcha_failures_do_not_spend_email_quota(self):
        for path in FORM_PATHS:
            with self.subTest(path=path):
                self.reset_attempts()
                before = self.counts()
                self.verify.side_effect = TurnstileVerificationError("Rejected test token")
                limit = self.attempt_limit(path)
                for index in range(limit):
                    response = self.post(
                        path, self.valid_payload(), ip=f"203.0.113.{index + 1}",
                    )
                    self.assertEqual(response.status_code, 400)
                self.assertEqual(self.verify.call_count, limit)
                self.assert_unchanged(before)
                self.verify.side_effect = None
                victim = self.post(path, self.valid_payload(), ip="198.51.100.80")
                self.assert_created(victim, path, before)

    def test_valid_email_quota_is_shared_by_aliases_and_still_blocks_before_save(self):
        for paths, throttle_class in (
            (RFQ_PATHS, RFQEmailThrottle),
            (CONTACT_PATHS, ContactEmailThrottle),
        ):
            with self.subTest(family=throttle_class.scope):
                self.reset_attempts()
                before = self.counts()
                limit = throttle_class(email="quotabuyer@example.com").num_requests
                for index in range(limit):
                    path = paths[index % len(paths)]
                    email = " QuotaBuyer@Example.com " if index % 2 else "quotabuyer@example.com"
                    response = self.post(
                        path, self.valid_payload(email), ip=f"203.0.113.{index + 1}",
                    )
                    self.assertEqual(response.status_code, 201, response.content[:500])
                expected_counts = (
                    (before[0] + limit, before[1])
                    if paths == RFQ_PATHS
                    else (before[0], before[1] + limit)
                )
                self.assertEqual(self.counts(), expected_counts)
                expected_queue = self.rfq_queue if paths == RFQ_PATHS else self.contact_queue
                other_queue = self.contact_queue if paths == RFQ_PATHS else self.rfq_queue
                self.assertEqual(expected_queue.call_count, limit)
                other_queue.assert_not_called()
                for index, path in enumerate(paths):
                    response = self.post(
                        path, self.valid_payload("quotabuyer@example.com"), ip=f"198.51.100.{index + 1}",
                    )
                    self.assert_throttled(response, path)
                self.assertEqual(self.counts(), expected_counts)
                self.assertEqual(expected_queue.call_count, limit)
                # Valid over-quota attempts were CAPTCHA-checked before rejection.
                self.assertEqual(self.verify.call_count, limit + len(paths))

    def test_legacy_unverified_email_bucket_does_not_block_validated_submissions(self):
        for path in FORM_PATHS:
            with self.subTest(path=path):
                self.reset_attempts()
                before = self.counts()
                throttle_class = RFQEmailThrottle if path in RFQ_PATHS else ContactEmailThrottle
                throttle = throttle_class(email="victim@example.com")
                digest = hashlib.sha256(b"victim@example.com").hexdigest()
                old_key = f"throttle_{throttle.scope}_{digest}"
                poisoned_history = [throttle.timer()] * throttle.num_requests
                cache.set(old_key, poisoned_history, timeout=int(throttle.duration))
                response = self.post(path, self.valid_payload(), ip="198.51.100.80")
                self.assert_created(response, path, before)
                self.assertEqual(cache.get(old_key), poisoned_history)
                # Check the quota contract without assuming the cache's
                # storage representation (local lists versus Redis zsets).
                for _ in range(throttle.num_requests - 1):
                    self.assertTrue(throttle.allow_request(None, None))
                self.assertFalse(throttle.allow_request(None, None))

    def test_ip_limit_rejects_multipart_without_reading_data_or_files(self):
        for path in FORM_PATHS:
            with self.subTest(path=path):
                self.reset_attempts()
                before = self.counts()
                for index in range(self.attempt_limit(path)):
                    response = self.post(
                        path, {"email": f"attacker-{index}@example.com"}, ip="203.0.113.40",
                    )
                    self.assertEqual(response.status_code, 400)
                with patch(
                    "rest_framework.request.Request._load_data_and_files",
                    side_effect=AssertionError("A blocked IP must not parse multipart"),
                ) as parse:
                    response = self.post(
                        path,
                        {
                            **self.valid_payload(),
                            "attachments": SimpleUploadedFile("unused.pdf", b"never parsed", content_type="application/pdf"),
                        },
                        ip="203.0.113.40",
                        format="multipart",
                    )
                    self.assert_throttled(response, path)
                    parse.assert_not_called()
                self.verify.assert_not_called()
                self.assert_unchanged(before)

    def test_upload_attempt_limit_precedes_validation_without_spending_email_quota(self):
        for path in RFQ_PATHS:
            with self.subTest(path=path):
                self.reset_attempts()
                before = self.counts()
                limit = AttachmentUploadIpThrottle().num_requests
                for _ in range(limit):
                    response = self.post(
                        path,
                        {
                            "email": "victim@example.com",
                            "attachments": SimpleUploadedFile("unused.pdf", b"not stored", content_type="application/pdf"),
                        },
                        ip="203.0.113.40",
                        format="multipart",
                    )
                    self.assertEqual(response.status_code, 400)
                response = self.post(
                    path,
                    {
                        **self.valid_payload(),
                        "attachments": SimpleUploadedFile("unused.pdf", b"not stored", content_type="application/pdf"),
                    },
                    ip="203.0.113.40",
                    format="multipart",
                )
                self.assert_throttled(response, path)
                self.verify.assert_not_called()
                self.assert_unchanged(before)
                victim = self.post(path, self.valid_payload(), ip="198.51.100.80")
                self.assert_created(victim, path, before)
