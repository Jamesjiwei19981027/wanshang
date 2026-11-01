"""Redis cache counters that cannot recreate an expired key without a TTL."""

from django.core.cache.backends.redis import RedisCache, RedisCacheClient


class AtomicRedisCacheClient(RedisCacheClient):
    # Django's Redis client checks EXISTS and then sends INCR separately. An
    # intervening expiry/deletion makes INCR create an immortal counter. Keep
    # both operations in one server-side script, preserving the existing TTL.
    _increment_existing = """
if redis.call('EXISTS', KEYS[1]) == 0 then
    return false
end
return redis.call('INCRBY', KEYS[1], ARGV[1])
"""

    def incr(self, key, delta):
        client = self.get_client(key, write=True)
        result = client.eval(self._increment_existing, 1, key, delta)
        if result is None:
            raise ValueError("Key '%s' not found." % key)
        return result


class AtomicRedisCache(RedisCache):
    def __init__(self, server, params):
        super().__init__(server, params)
        self._class = AtomicRedisCacheClient
