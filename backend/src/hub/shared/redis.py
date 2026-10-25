"""The Redis adapter for :class:`hub.shared.contracts.KeyValueStore`.

One client per URL, created lazily and shared for the life of the process: ``redis.Redis``
pools its connections itself, and a client per request would leak file descriptors under
load. Values are text, so a caller reads back what it wrote.
"""

from __future__ import annotations

import threading

import redis
from django.conf import settings

from hub.shared.contracts import KeyValueStore

__all__ = ["RedisStore", "get_redis", "get_store"]

_clients: dict[str, redis.Redis] = {}
_lock = threading.Lock()


def get_redis(url: str | None = None) -> redis.Redis:
    """The process-wide client for ``url`` (default: this deployable's ``HUB_REDIS_URL``)."""
    target = url or str(settings.HUB_REDIS_URL)
    client = _clients.get(target)
    if client is None:
        with _lock:
            client = _clients.get(target)
            if client is None:
                client = redis.Redis.from_url(target, decode_responses=True)
                _clients[target] = client
    return client


class RedisStore(KeyValueStore):
    """``KeyValueStore`` over one Redis client."""

    def __init__(self, client: redis.Redis | None = None) -> None:
        self._client = client if client is not None else get_redis()

    @property
    def client(self) -> redis.Redis:
        return self._client

    def get(self, key: str) -> str | None:
        value = self._client.get(key)
        return value if isinstance(value, str) else None

    def set(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None:
        self._client.set(key, value, ex=ttl_seconds)

    def delete(self, key: str) -> None:
        self._client.delete(key)

    def incr(self, key: str, *, ttl_seconds: int | None = None) -> int:
        # A counter is created and given its window in one MULTI, so a crash between the two
        # cannot leave a counter that never expires (which would lock an account for good).
        pipe = self._client.pipeline()
        pipe.incr(key)
        if ttl_seconds is not None:
            pipe.expire(key, ttl_seconds, nx=True)
        count, _ = pipe.execute()
        return int(count)

    def add_once(self, key: str, value: str, *, ttl_seconds: int) -> bool:
        return bool(self._client.set(key, value, ex=ttl_seconds, nx=True))

    def pop(self, key: str) -> str | None:
        value = self._client.getdel(key)
        return value if isinstance(value, str) else None


def get_store() -> KeyValueStore:
    """The process-wide store adapter, for wiring."""
    return RedisStore(get_redis())
