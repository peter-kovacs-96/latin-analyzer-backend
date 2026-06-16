import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Generic, TypeVar

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
