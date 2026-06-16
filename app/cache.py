import json
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

import httpx

K = TypeVar("K")
V = TypeVar("V")


@dataclass
class CacheEntry(Generic[V]):
    value: V
    expires_at: float


class TTLCache(Generic[K, V]):
    def __init__(self, max_items: int, ttl_seconds: int) -> None:
        self.max_items = max_items
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[K, CacheEntry[V]] = OrderedDict()

    def get(self, key: K) -> V | None:
        item = self._items.get(key)
        if item is None:
            return None
        if item.expires_at < time.monotonic():
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return item.value

    def set(self, key: K, value: V) -> None:
        self._items[key] = CacheEntry(value=value, expires_at=time.monotonic() + self.ttl_seconds)
        self._items.move_to_end(key)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def clear(self) -> None:
        self._items.clear()


class UpstashCache:
    """Persistent L2 cache backed by Upstash Redis REST API."""

    def __init__(self, url: str, token: str) -> None:
        self._client = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=3.0,
        )

    async def get(self, key: str) -> Any | None:
        try:
            r = await self._client.post("/", json=["GET", key])
            result = r.json().get("result")
            if result is not None:
                return json.loads(result)
        except Exception:
            pass
        return None

    async def set(self, key: str, value: Any) -> None:
        try:
            await self._client.post(
                "/", json=["SET", key, json.dumps(value, ensure_ascii=False)]
            )
        except Exception:
            pass

    async def close(self) -> None:
        await self._client.aclose()
