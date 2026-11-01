"""One-time Turnstile acceptance even when the upstream accepts replay."""
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Barrier
from unittest.mock import patch

from django.core.cache import cache
from django.test import RequestFactory, SimpleTestCase, override_settings

from .turnstile import TurnstileVerificationError, verify_turnstile_token


class Response:
    def __init__(self, **changes):
        self.payload = {"success": True, "action": "contact", "hostname": "www.example.com"} | changes

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _size):
        return json.dumps(self.payload).encode()


@override_settings(
    DJANGO_ENV="production",
    TURNSTILE_REQUIRED=True,
    TURNSTILE_SECRET_KEY="synthetic-replay-test-key",
    ALLOWED_HOSTS=["www.example.com"],
    CORS_ALLOWED_ORIGINS=["https://www.example.com"],
    CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "turnstile-replay-tests"}},
)
class TurnstileReplayTests(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.request = RequestFactory().post("/api/contact/", REMOTE_ADDR="198.51.100.90")

    @patch("catalog.turnstile.urlopen", side_effect=lambda *_args, **_kwargs: Response())
    def test_replay_rejected_when_upstream_accepts_both_requests(self, upstream):
        verify_turnstile_token("same-synthetic-token", self.request, "contact")
        with self.assertRaisesRegex(TurnstileVerificationError, "already been used"):
            verify_turnstile_token("same-synthetic-token", self.request, "contact")
        self.assertEqual(upstream.call_count, 2)

    @patch("catalog.turnstile.urlopen", side_effect=lambda *_args, **_kwargs: Response())
    def test_independent_tokens_remain_usable(self, _upstream):
        verify_turnstile_token("first-synthetic-token", self.request, "contact")
        verify_turnstile_token("second-synthetic-token", self.request, "contact")

    def test_concurrent_upstream_success_only_accepts_once(self):
        barrier = Barrier(2)

        def upstream(*_args, **_kwargs):
            barrier.wait(timeout=5)
            return Response()

        def attempt(_number):
            try:
                verify_turnstile_token("concurrent-synthetic-token", self.request, "contact")
                return True
            except TurnstileVerificationError:
                return False

        with patch("catalog.turnstile.urlopen", side_effect=upstream):
            with ThreadPoolExecutor(max_workers=2) as executor:
                self.assertEqual(sorted(executor.map(attempt, range(2))), [False, True])

    @patch("catalog.turnstile.urlopen", return_value=Response())
    def test_cache_failure_rejects_an_otherwise_valid_token(self, _upstream):
        with patch("catalog.turnstile.cache.add", side_effect=ConnectionError("synthetic cache outage")):
            with self.assertRaisesRegex(TurnstileVerificationError, "temporarily unavailable"):
                verify_turnstile_token("cache-outage-token", self.request, "contact")

    @patch("catalog.turnstile.urlopen", return_value=Response())
    def test_cache_only_retains_a_fingerprint_for_the_token_lifetime(self, _upstream):
        token = "synthetic-raw-token-must-not-be-stored"
        with patch("catalog.turnstile.cache.add", return_value=True) as claim:
            verify_turnstile_token(token, self.request, "contact")
        key, value = claim.call_args.args
        self.assertNotIn(token, key)
        self.assertEqual(len(key.rsplit(":", 1)[1]), 64)
        self.assertIs(value, True)
        self.assertEqual(claim.call_args.kwargs["timeout"], 300)

    def test_invalid_action_or_hostname_does_not_consume_token(self):
        for changes in ({"action": "rfq"}, {"hostname": "untrusted.example.com"}):
            with self.subTest(changes=changes):
                with patch("catalog.turnstile.urlopen", return_value=Response(**changes)):
                    with patch("catalog.turnstile.cache.add") as claim:
                        with self.assertRaises(TurnstileVerificationError):
                            verify_turnstile_token("untrusted-synthetic-token", self.request, "contact")
                        claim.assert_not_called()
