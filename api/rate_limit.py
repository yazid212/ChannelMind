import hashlib
import hmac
import threading
import time
from collections import defaultdict

from fastapi import HTTPException, status

from .settings import get_settings


class RateLimiter:
    """Fixed-window limiter backed by Redis in production and memory locally."""

    def __init__(self):
        settings = get_settings()
        self._secret = settings.app_secret.encode("utf-8")
        self._redis = None
        self._lock = threading.Lock()
        self._local: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))
        if settings.redis_url:
            from redis import Redis

            self._redis = Redis.from_url(settings.redis_url, decode_responses=True)

    def _key(self, bucket: str, identity: str, window: int) -> tuple[str, int]:
        digest = hmac.new(self._secret, identity.encode("utf-8"), hashlib.sha256).hexdigest()[:24]
        current_window = int(time.time()) // window
        return f"rate:{bucket}:{digest}:{current_window}", current_window

    def check(self, bucket: str, identity: str, limit: int, window: int) -> None:
        key, current_window = self._key(bucket, identity, window)
        retry_after = window - (int(time.time()) % window)

        if self._redis is not None:
            pipeline = self._redis.pipeline(transaction=True)
            pipeline.incr(key)
            pipeline.expire(key, window + 1)
            count, _ = pipeline.execute()
        else:
            with self._lock:
                count, saved_window = self._local[key]
                count = count + 1 if saved_window == current_window else 1
                self._local[key] = (count, current_window)

        if int(count) > limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )
