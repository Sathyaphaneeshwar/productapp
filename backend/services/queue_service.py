import json
import os
import time
from typing import Any, Optional

try:
    import redis
except ImportError as e:
    redis = None

from config import REDIS_URL, QUEUE_PREFIX


class QueueService:
    def __init__(self, redis_url: Optional[str] = None):
        if redis is None:
            raise ImportError("redis package is required. Please install redis==5.x")
        self.redis_url = redis_url or REDIS_URL
        self.client = redis.Redis.from_url(self.redis_url, decode_responses=True)

    def _key(self, name: str) -> str:
        return f"{QUEUE_PREFIX}:{name}"

    def enqueue(self, queue_name: str, payload: dict) -> None:
        body = json.dumps(payload)
        self.client.lpush(self._key(queue_name), body)

    def dequeue(self, queue_name: str, timeout: int = 5) -> Optional[dict]:
        result = self.client.brpop(self._key(queue_name), timeout=timeout)
        if not result:
            return None
        _, body = result
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None

    def ping(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False

    def length(self, queue_name: str) -> int:
        try:
            return int(self.client.llen(self._key(queue_name)))
        except Exception:
            return 0
