"""Regression tests for request boundaries that must hold under concurrency."""

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from threading import Barrier, Event, Lock
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from .middleware import AdminLoginRateLimitMiddleware, PublicFormOriginMiddleware
from .turnstile import TurnstileVerificationError, verify_turnstile_token


@override_settings(
    ALLOWED_HOSTS=["api.example.com"],
    CSRF_TRUSTED_ORIGINS=["https://www.example.com"],
)
class StrictPublicFormOriginTests(SimpleTestCase):
    def submit(self, origin, *, secure=True):
        request = RequestFactory().post(
            "/api/v1/contact/",
            HTTP_HOST="api.example.com",
            HTTP_ORIGIN=origin,
            secure=secure,
        )
        return PublicFormOriginMiddleware(lambda request: HttpResponse())(request)

    def test_secure_request_rejects_insecure_same_host_origin(self):
        self.assertEqual(self.submit("http://api.example.com").status_code, 403)

    def test_only_complete_origins_are_accepted(self):
        for origin in (
            "https://www.example.com/path",
            "https://www.example.com/",
            "https://www.example.com?query=1",
            "https://www.example.com#fragment",
            "https://user:password@www.example.com",
            "https://api.example.com:invalid",
            "https://www.example.com\n",
            "https://www.example.com\t",
            "null",
        ):
            with self.subTest(origin=origin):
                self.assertEqual(self.submit(origin).status_code, 403)

    def test_same_origin_and_explicitly_trusted_frontend_still_work(self):
        for origin in ("https://api.example.com", "https://www.example.com"):
            with self.subTest(origin=origin):
                self.assertEqual(self.submit(origin).status_code, 200)
        self.assertEqual(
            self.submit("http://api.example.com", secure=False).status_code, 200
        )


@override_settings(
    ADMIN_LOGIN_MAX_FAILURES=2,
    ADMIN_LOGIN_FAILURE_WINDOW=60,
    CACHES={"default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "admin-login-concurrency-security",
    }},
    REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": 0},
)
class AdminLoginConcurrentAttemptTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_legacy_counter_without_expiry_does_not_permanently_lock_the_new_bucket(self):
        address = "198.51.100.91"
        digest = hashlib.sha256(address.encode()).hexdigest()
        legacy_key = f"admin-login-failures:{digest}"
        cache.set(legacy_key, settings.ADMIN_LOGIN_MAX_FAILURES, timeout=None)
        request = RequestFactory().post(
            "/admin/login/", {"username": "target-admin"}, REMOTE_ADDR=address,
        )
        middleware = AdminLoginRateLimitMiddleware(lambda request: HttpResponse())
        self.assertEqual(middleware(request).status_code, 200)
        new_key = middleware._cache_key(request)
        self.assertNotEqual(new_key, legacy_key)
        self.assertEqual(cache.get(new_key), 1)
        self.assertEqual(cache.get(legacy_key), settings.ADMIN_LOGIN_MAX_FAILURES)

    def test_parallel_attempts_reserve_quota_before_authenticating(self):
        attempts = 8
        start = Barrier(attempts + 1)
        release_authentication = Event()
        observed = Event()
        state_lock = Lock()
        admitted = []
        finished = []

        def authenticate(request):
            with state_lock:
                admitted.append(request)
                if len(admitted) == attempts:
                    observed.set()
            if not release_authentication.wait(10):
                raise AssertionError("Authentication was not released")
            return HttpResponse(status=200)

        middleware = AdminLoginRateLimitMiddleware(authenticate)

        def attempt(index):
            request = RequestFactory().post(
                "/admin/login/", {"username": "target-admin"},
                REMOTE_ADDR="198.51.100.91",
            )
            start.wait(timeout=10)
            result = middleware(request)
            with state_lock:
                finished.append(result.status_code)
                if len(finished) >= attempts - settings.ADMIN_LOGIN_MAX_FAILURES:
                    observed.set()
            return result.status_code

        # Isolate the admission check from template rendering and authentication.
        with patch.object(
            middleware, "_throttled_response", return_value=HttpResponse(status=429)
        ), ThreadPoolExecutor(max_workers=attempts) as executor:
            futures = [executor.submit(attempt, index) for index in range(attempts)]
            start.wait(timeout=10)
            try:
                self.assertTrue(observed.wait(10), "Requests did not reach admission")
            finally:
                release_authentication.set()
            statuses = [future.result(timeout=10) for future in futures]

        self.assertEqual(len(admitted), settings.ADMIN_LOGIN_MAX_FAILURES)
        self.assertEqual(statuses.count(200), settings.ADMIN_LOGIN_MAX_FAILURES)
        self.assertEqual(statuses.count(429), attempts - settings.ADMIN_LOGIN_MAX_FAILURES)


class FakeTurnstileResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        return json.dumps(self.payload).encode("utf-8")


@override_settings(
    DJANGO_ENV="production",
    TURNSTILE_REQUIRED=True,
    TURNSTILE_SECRET_KEY="isolated-security-test-key",
    ALLOWED_HOSTS=["api.example.com"],
    CORS_ALLOWED_ORIGINS=["https://www.example.com"],
)
class TurnstileHostnameSecurityTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def verify_response(self, hostname):
        request = RequestFactory().post(
            "/api/v1/inquiries/", HTTP_HOST="api.example.com",
            REMOTE_ADDR="198.51.100.92",
        )
        payload = {"success": True, "action": "rfq", "hostname": hostname}
        with patch("catalog.turnstile.urlopen", return_value=FakeTurnstileResponse(payload)):
            verify_turnstile_token(f"isolated-security-token-{hostname}", request, "rfq")

    def test_other_widget_hostnames_are_rejected_even_for_successful_tokens(self):
        for hostname in (None, "", "preview.example.com", "evil.example", ["www.example.com"]):
            with self.subTest(hostname=hostname):
                with self.assertRaises(TurnstileVerificationError):
                    self.verify_response(hostname)

    def test_trusted_frontend_and_same_origin_widgets_are_accepted(self):
        for hostname in ("www.example.com", "api.example.com"):
            with self.subTest(hostname=hostname):
                self.verify_response(hostname)
