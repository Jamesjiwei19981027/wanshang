"""Public limits cannot double-spend quota across concurrent requests."""

import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch
from uuid import uuid4

from django.core.cache import cache
from django.core.cache.backends.dummy import DummyCache
from django.core.cache.backends.locmem import LocMemCache
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework.test import APIClient

from config.cache import AtomicRedisCache
from .models import ContactMessage, Inquiry
from .rate_limits import RateLimitUnavailable
from .throttles import (
    AttachmentUploadIpThrottle, ContactEmailThrottle, ContactIpThrottle,
    RFQEmailThrottle, RFQIpThrottle,
)


FORM_THROTTLES = (
    RFQIpThrottle, ContactIpThrottle, AttachmentUploadIpThrottle,
    RFQEmailThrottle, ContactEmailThrottle,
)
REDIS_TEST_URL = os.getenv("DJANGO_SECURITY_TEST_REDIS_URL", "")


class DelayedLocalCache(LocMemCache):
    def get(self, *args, **kwargs):
        value = super().get(*args, **kwargs)
        # Model scheduling/network delay between the read and reservation.
        # Without the surrounding lock all threads can spend the same slots.
        time.sleep(0.002)
        return value


class AtomicLocalRateLimitTests(SimpleTestCase):
    request = SimpleNamespace(
        META={"REMOTE_ADDR": "198.51.100.190"}, headers={},
        FILES={"attachments": True},
    )

    @staticmethod
    def throttle(throttle_class, backend, *, key=None):
        throttle = (
            throttle_class(email="buyer@example.com")
            if throttle_class in (RFQEmailThrottle, ContactEmailThrottle)
            else throttle_class()
        )
        throttle.cache = backend
        throttle.rate = "2/min"
        throttle.num_requests, throttle.duration = 2, 60
        if key:
            throttle.get_cache_key = lambda request, view: key
        return throttle

    def test_every_form_bucket_limits_simultaneous_reservations(self):
        for throttle_class in FORM_THROTTLES:
            with self.subTest(scope=throttle_class.scope):
                backend = DelayedLocalCache(f"concurrent-limits:{uuid4().hex}", {})
                barrier = Barrier(16)

                def attempt(_):
                    throttle = self.throttle(throttle_class, backend)
                    barrier.wait(timeout=10)
                    allowed = throttle.allow_request(self.request, None)
                    if not allowed:
                        self.assertGreater(throttle.wait(), 0)
                        self.assertLessEqual(throttle.wait(), 60)
                    return allowed

                with ThreadPoolExecutor(max_workers=16) as pool:
                    results = list(pool.map(attempt, range(16)))
                self.assertEqual(sum(results), 2)

    def test_window_releases_each_reservation_at_its_own_expiry(self):
        backend = LocMemCache(f"sliding-limits:{uuid4().hex}", {})
        throttle = self.throttle(RFQIpThrottle, backend)
        for now, expected in ((100, True), (110, True), (159, False),
                              (160, True), (169, False), (170, True)):
            with self.subTest(now=now):
                throttle.timer = lambda: now
                self.assertEqual(throttle.allow_request(self.request, None), expected)
                if not expected:
                    self.assertEqual(throttle.wait(), 1)

    def test_backend_failures_and_unsupported_caches_fail_closed(self):
        backend = LocMemCache(f"failed-limits:{uuid4().hex}", {})
        throttle = self.throttle(ContactIpThrottle, backend)
        with (
            patch.object(backend, "set", side_effect=OSError("cache unavailable")),
            self.assertLogs("catalog.rate_limits", level="ERROR"),
            self.assertRaises(RateLimitUnavailable),
        ):
            throttle.allow_request(self.request, None)
        throttle.cache = DummyCache("unsupported", {})
        with (
            self.assertLogs("catalog.rate_limits", level="ERROR"),
            self.assertRaises(RateLimitUnavailable),
        ):
            throttle.allow_request(self.request, None)


@override_settings(
    SECURE_SSL_REDIRECT=False,
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache",
                        "LOCATION": "api-rate-limit-outage-tests"}},
)
class RateLimitOutageEndpointTests(TestCase):
    def test_cache_outage_rejects_before_captcha_database_writes_or_notifications(self):
        for path in ("/api/inquiries/", "/api/v1/inquiries/",
                     "/api/contact/", "/api/v1/contact-messages/"):
            with self.subTest(path=path):
                cache.clear()
                with (
                    patch.object(cache, "set", side_effect=OSError("cache unavailable")),
                    patch("catalog.serializers.verify_turnstile_token") as verify,
                    patch("catalog.views.enqueue_inquiry_notification") as rfq_queue,
                    patch("catalog.views.enqueue_contact_notification") as contact_queue,
                    self.assertLogs("catalog.rate_limits", level="ERROR"),
                ):
                    response = APIClient().post(path, {
                        "name": "Outage buyer", "email": "outage@example.com",
                        "message": "Please contact me.", "privacy_consent": True,
                    }, format="json")
                self.assertEqual(response.status_code, 503)
                self.assertIn("temporarily unavailable", str(response.json()))
                verify.assert_not_called()
                rfq_queue.assert_not_called()
                contact_queue.assert_not_called()
                self.assertFalse(Inquiry.objects.exists())
                self.assertFalse(ContactMessage.objects.exists())


@skipUnless(REDIS_TEST_URL, "Set DJANGO_SECURITY_TEST_REDIS_URL for isolated Redis tests")
class ActualRedisRateLimitTests(SimpleTestCase):
    request = AtomicLocalRateLimitTests.request

    def setUp(self):
        self.backend = AtomicRedisCache(REDIS_TEST_URL, {})
        self.connection = self.backend._cache.get_client(write=True)
        self.key = f"public-window-regression:{uuid4().hex}"
        self.raw_key = self.backend.make_key(self.key + RFQIpThrottle.redis_key_suffix)
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.delete, self.raw_key)

    def throttle(self, throttle_class=RFQIpThrottle):
        return AtomicLocalRateLimitTests.throttle(throttle_class, self.backend, key=self.key)

    def test_all_form_buckets_are_atomic_with_real_redis(self):
        for throttle_class in FORM_THROTTLES:
            with self.subTest(scope=throttle_class.scope):
                self.connection.delete(self.raw_key)
                barrier = Barrier(24)

                def attempt(_):
                    throttle = self.throttle(throttle_class)
                    barrier.wait(timeout=10)
                    allowed = throttle.allow_request(self.request, None)
                    if not allowed:
                        self.assertGreater(throttle.wait(), 0)
                        self.assertLessEqual(throttle.wait(), 60)
                    return allowed

                with ThreadPoolExecutor(max_workers=24) as pool:
                    results = list(pool.map(attempt, range(24)))
                self.assertEqual(sum(results), 2)
                self.assertEqual(self.connection.zcard(self.raw_key), 2)
                self.assertGreater(self.connection.pttl(self.raw_key), 0)
                self.assertLessEqual(self.connection.pttl(self.raw_key), 60_000)

    def test_sliding_expiry_admits_only_the_newly_available_slot(self):
        seconds, microseconds = self.connection.time()
        now = seconds * 1000 + microseconds // 1000
        self.connection.zadd(self.raw_key, {"expired": now - 60_001, "active": now - 5_000})
        self.connection.pexpire(self.raw_key, 55_000)
        throttle = self.throttle()
        self.assertTrue(throttle.allow_request(self.request, None))
        self.assertIsNone(self.connection.zscore(self.raw_key, "expired"))
        self.assertFalse(throttle.allow_request(self.request, None))
        self.assertGreater(throttle.wait(), 53)
        self.assertLessEqual(throttle.wait(), 55)
        self.assertEqual(self.connection.zcard(self.raw_key), 2)

    def test_denials_do_not_extend_expiry_or_accumulate_more_entries(self):
        throttle = self.throttle()
        self.assertTrue(throttle.allow_request(self.request, None))
        self.assertTrue(throttle.allow_request(self.request, None))
        self.connection.pexpire(self.raw_key, 5_000)
        for _ in range(10):
            self.assertFalse(throttle.allow_request(self.request, None))
        self.assertEqual(self.connection.zcard(self.raw_key), 2)
        self.assertGreater(self.connection.pttl(self.raw_key), 0)
        self.assertLessEqual(self.connection.pttl(self.raw_key), 5_000)

    def test_redis_error_does_not_admit_request(self):
        with (
            patch.object(self.backend._cache, "get_client", side_effect=OSError("Redis unavailable")),
            self.assertLogs("catalog.rate_limits", level="ERROR"),
            self.assertRaises(RateLimitUnavailable),
        ):
            self.throttle().allow_request(self.request, None)
