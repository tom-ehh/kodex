"""In-memory cache: form id -> extracted Document (+ the form made from it), with a TTL.

Kept for replay (P2 wake) and to resolve a form id in `POST /forms/{id}/corrections`.
Never written to disk (design.md, decision (a)); lost on restart by design.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class CachedForm:
    doc: Any
    form: dict | None
    expires: float


class DocCache:
    def __init__(self, ttl_s: float, clock: Callable[[], float] = time.monotonic, max_items: int = 500):
        self.ttl_s, self._clock, self._max = ttl_s, clock, max_items
        self._items: dict[str, CachedForm] = {}
        self._lock = threading.Lock()

    def put(self, form_id: str, doc: Any, form: dict | None = None) -> None:
        with self._lock:
            self._purge()
            if len(self._items) >= self._max:  # drop the oldest insert
                self._items.pop(next(iter(self._items)))
            self._items[form_id] = CachedForm(doc, form, self._clock() + self.ttl_s)

    def get(self, form_id: str) -> CachedForm | None:
        with self._lock:
            self._purge()
            return self._items.get(form_id)

    def __len__(self) -> int:
        with self._lock:
            self._purge()
            return len(self._items)

    def _purge(self) -> None:
        now = self._clock()
        for k in [k for k, v in self._items.items() if v.expires <= now]:
            del self._items[k]
