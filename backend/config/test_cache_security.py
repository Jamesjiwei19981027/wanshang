"""Counter expiry regressions; optional Redis tests never flush shared data."""

import os
from concurrent.futures import ThreadPoolExecutor
from unittest import skipUnless
from unittest.mock import Mock, patch
from uuid import uuid4

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from catalog.middleware import AdminLoginRateLimitMiddleware

from .cache import AtomicRedisCache, AtomicRedisCacheClient


class ExpiringCounter:
    """Model expiry at the network boundary immediately before mutation."""

    def __init__(self):
        self.value = 1
        self.ttl = 60

    def exists(self, key):
        existed = self.value is not None
        self.value = None
        self.ttl = None
        return existed

    def incr(self, key, delta):
        self.value = (self.value or 0) + delta
        return self.value

    def eval(self, script, number_of_keys, key, delta):
        # Expiry precedes the atomic script, so it must report a missing key.
        self.value = None
        self.ttl = None
        return None


class AtomicRedisCounterTests(SimpleTestCase):
    def test_expired_counter_is_reported_missing_without_recreation(self):
        connection = ExpiringCounter()
        client = AtomicRedisCacheClient(["redis://unused.invalid:6379/0"])
        with patch.object(client, "get_client", return_value=connection):
            with self.assertRaises(ValueError):
                client.incr("test-counter", 1)
        self.assertIsNone(connection.value)

    def test_increment_uses_one_write_command_and_accepts_zero(self):
        connection = Mock()
        connection.eval.return_value = 0
        client = AtomicRedisCacheClient(["redis://unused.invalid:6379/0"])
        with patch.object(client, "get_client", return_value=connection) as get_client:
            self.assertEqual(client.incr("test-counter", -1), 0)
        get_client.assert_called_once_with("test-counter", write=True)
        connection.eval.assert_called_once_with(
            client._increment_existing, 1, "test-counter", -1
        )
        connection.exists.assert_not_called()
        connection.incr.assert_not_called()


REDIS_TEST_URL = os.getenv("DJANGO_SECURITY_TEST_REDIS_URL", "")


@skipUnless(REDIS_TEST_URL, "Set DJANGO_SECURITY_TEST_REDIS_URL for isolated Redis tests")
class ActualRedisCounterSecurityTests(SimpleTestCase):
    def setUp(self):
        self.key = f"admin-counter-regression:{uuid4().hex}"
        self.backend = AtomicRedisCache(REDIS_TEST_URL, {})
        self.connection = self.backend._cache.get_client(write=True)
        self.raw_key = self.backend.make_key(self.key)
        self.addCleanup(self.connection.close)
        self.addCleanup(self.connection.delete, self.raw_key)

    def test_expiry_at_increment_does_not_create_a_persistent_counter(self):
        self.backend.set(self.key, 1, timeout=60)
        original_eval = self.connection.eval

        def expire_then_increment(script, number_of_keys, key, delta):
            # A past absolute expiry is deterministic and requires no sleeps.
            self.connection.pexpireat(key, 1)
            return original_eval(script, number_of_keys, key, delta)

        with (
            patch.object(self.backend._cache, "get_client", return_value=self.connection),
            patch.object(self.connection, "eval", side_effect=expire_then_increment),
        ):
            with self.assertRaises(ValueError):
                self.backend.incr(self.key)
        self.assertEqual(self.connection.pttl(self.raw_key), -2)

    def test_increment_and_decrement_preserve_the_original_expiry(self):
        self.backend.set(self.key, 1, timeout=60)
        initial_ttl = self.connection.pttl(self.raw_key)
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: self.backend.incr(self.key), range(8)))
        self.assertEqual(sorted(results), list(range(2, 10)))
        self.assertEqual(self.backend.decr(self.key, 9), 0)
        remaining_ttl = self.connection.pttl(self.raw_key)
        self.assertGreater(remaining_ttl, 0)
        self.assertLessEqual(remaining_ttl, initial_ttl)

    def test_admin_reservation_restarts_an_expired_bucket_with_a_finite_window(self):
        with override_settings(
            CACHES={"default": {
                "BACKEND": "config.cache.AtomicRedisCache", "LOCATION": REDIS_TEST_URL,
            }},
            ADMIN_LOGIN_MAX_FAILURES=2,
            ADMIN_LOGIN_FAILURE_WINDOW=60,
        ):
            cache.set(self.key, 1, timeout=60)
            atomic_client = cache._cache
            original_get_client = atomic_client.get_client
            redis_connection = original_get_client(write=True)
            original_eval = redis_connection.eval

            def expire_then_increment(script, number_of_keys, key, delta):
                redis_connection.pexpireat(key, 1)
                return original_eval(script, number_of_keys, key, delta)

            with (
                patch.object(atomic_client, "get_client", return_value=redis_connection),
                patch.object(redis_connection, "eval", side_effect=expire_then_increment),
            ):
                self.assertTrue(AdminLoginRateLimitMiddleware._reserve_attempt(self.key))
            self.assertEqual(cache.get(self.key), 1)
            self.assertGreater(redis_connection.pttl(cache.make_key(self.key)), 0)
            redis_connection.close()
