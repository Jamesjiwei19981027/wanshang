"""Atomic sliding-window reservations for public API rate limits."""

import logging
from threading import RLock
from uuid import uuid4

from django.core.cache import cache as default_cache, caches
from django.core.cache.backends.locmem import LocMemCache
from django.core.cache.backends.redis import RedisCache
from rest_framework.exceptions import APIException
from rest_framework.throttling import SimpleRateThrottle


logger = logging.getLogger(__name__)
_local_reservation_lock = RLock()


class RateLimitUnavailable(APIException):
    status_code = 503
    default_detail = "Request rate limits are temporarily unavailable. Please try again."
    default_code = "rate_limit_unavailable"


class AtomicSlidingWindowThrottle(SimpleRateThrottle):
    """Reserve before work, without losing concurrent updates to one bucket.

    Production uses one Redis script and the server's clock across workers.
    LocMemCache is only used by development/tests and shares a process lock.
    Other cache backends fail closed instead of silently weakening the limit.
    """

    # Keep a distinct Redis key: Django's older throttle values were pickled
    # lists, whereas the atomic reservation uses a bounded sorted set.
    redis_key_suffix = ":atomic-window:v1"
    _reserve_redis_window = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
local duration = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - duration)
if redis.call('ZCARD', KEYS[1]) >= limit then
    local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    return {0, math.max(1, tonumber(oldest[2]) + duration - now)}
end
redis.call('ZADD', KEYS[1], now, ARGV[3])
redis.call('PEXPIRE', KEYS[1], duration)
return {1, 0}
"""

    def allow_request(self, request, view):
        if self.rate is None:
            return True
        self.key = self.get_cache_key(request, view)
        if self.key is None:
            return True
        backend = caches["default"] if self.cache is default_cache else self.cache
        try:
            if isinstance(backend, RedisCache):
                key = backend.make_and_validate_key(self.key + self.redis_key_suffix)
                client = backend._cache.get_client(key, write=True)
                allowed, wait_ms = client.eval(
                    self._reserve_redis_window,
                    1,
                    key,
                    int(self.duration * 1000),
                    self.num_requests,
                    uuid4().hex,
                )
                self._wait_seconds = wait_ms / 1000
                return bool(allowed)
            if isinstance(backend, LocMemCache):
                with _local_reservation_lock:
                    # Retain DRF's sliding-window and local cache format. The
                    # lock covers get/check/set, not just either cache call.
                    return super().allow_request(request, view)
            raise TypeError("An atomic Redis or local-memory cache is required.")
        except Exception as exc:
            logger.exception("Public API rate-limit reservation failed")
            raise RateLimitUnavailable() from exc

    def wait(self):
        if hasattr(self, "_wait_seconds"):
            return self._wait_seconds
        return super().wait()
