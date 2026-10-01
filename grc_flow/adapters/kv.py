"""Queue, idempotency locks, rate limits and cache: Upstash Redis or in-process.

Upstash is reached over its REST API (`upstash_redis.Redis`), so it works from
serverless functions and behind Cloudflare without a TCP Redis connection.

Queue semantics: a job is popped once. If a worker dies mid-run, the run stays
"running" in the database; the checker's stale-run sweep re-enqueues it, and the
idempotency claim on every run stops two workers from processing it at once.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Any, Protocol

QUEUE = "grcflow:queue:events"
DEAD = "grcflow:queue:dead"


class KV(Protocol):
    name: str

    def ping(self) -> bool: ...
    def enqueue(self, job: dict[str, Any]) -> None: ...
    def dequeue(self) -> dict[str, Any] | None: ...
    def dead_letter(self, job: dict[str, Any], error: str) -> None: ...
    def queue_depth(self) -> int: ...
    def claim(self, key: str, ttl_s: int) -> bool: ...
    def hit_rate_limit(self, key: str, limit: int, window_s: int) -> bool: ...
    def cache_get(self, key: str) -> Any | None: ...
    def cache_set(self, key: str, value: Any, ttl_s: int) -> None: ...


class MemoryKV:
    name = "memory"

    def __init__(self) -> None:
        self._q: deque[str] = deque()
        self._dead: list[str] = []
        self._data: dict[str, tuple[Any, float | None]] = {}
        self._lock = threading.Lock()

    def _alive(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        value, expires = item
        if expires is not None and expires < time.time():
            del self._data[key]
            return None
        return value

    def ping(self) -> bool:
        return True

    def enqueue(self, job: dict[str, Any]) -> None:
        with self._lock:
            self._q.appendleft(json.dumps(job))

    def dequeue(self) -> dict[str, Any] | None:
        with self._lock:
            return json.loads(self._q.pop()) if self._q else None

    def dead_letter(self, job: dict[str, Any], error: str) -> None:
        with self._lock:
            self._dead.append(json.dumps({**job, "error": error}))

    def queue_depth(self) -> int:
        return len(self._q)

    def claim(self, key: str, ttl_s: int) -> bool:
        with self._lock:
            if self._alive(key) is not None:
                return False
            self._data[key] = ("1", time.time() + ttl_s)
            return True

    def hit_rate_limit(self, key: str, limit: int, window_s: int) -> bool:
        bucket = f"{key}:{int(time.time() // window_s)}"
        with self._lock:
            count = (self._alive(bucket) or 0) + 1
            self._data[bucket] = (count, time.time() + window_s)
        return count > limit

    def cache_get(self, key: str) -> Any | None:
        with self._lock:
            return self._alive(key)

    def cache_set(self, key: str, value: Any, ttl_s: int) -> None:
        with self._lock:
            self._data[key] = (value, time.time() + ttl_s)


class UpstashKV:
    name = "upstash"

    def __init__(self, url: str, token: str) -> None:
        from upstash_redis import Redis

        self.r = Redis(url=url, token=token)

    def ping(self) -> bool:
        return str(self.r.ping()).upper() == "PONG"

    def enqueue(self, job: dict[str, Any]) -> None:
        self.r.lpush(QUEUE, json.dumps(job))

    def dequeue(self) -> dict[str, Any] | None:
        raw = self.r.rpop(QUEUE)
        return json.loads(raw) if raw else None

    def dead_letter(self, job: dict[str, Any], error: str) -> None:
        self.r.lpush(DEAD, json.dumps({**job, "error": error}))

    def queue_depth(self) -> int:
        return int(self.r.llen(QUEUE))

    def claim(self, key: str, ttl_s: int) -> bool:
        return bool(self.r.set(f"grcflow:lock:{key}", "1", nx=True, ex=ttl_s))

    def hit_rate_limit(self, key: str, limit: int, window_s: int) -> bool:
        bucket = f"grcflow:rl:{key}:{int(time.time() // window_s)}"
        count = int(self.r.incr(bucket))
        if count == 1:
            self.r.expire(bucket, window_s)
        return count > limit

    def cache_get(self, key: str) -> Any | None:
        raw = self.r.get(f"grcflow:cache:{key}")
        return json.loads(raw) if raw else None

    def cache_set(self, key: str, value: Any, ttl_s: int) -> None:
        self.r.set(f"grcflow:cache:{key}", json.dumps(value), ex=ttl_s)


def make_kv(settings) -> KV:
    if settings.upstash_url and settings.upstash_token:
        return UpstashKV(settings.upstash_url, settings.upstash_token)
    return MemoryKV()
