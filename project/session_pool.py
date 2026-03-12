from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PoolItem:
    name: str
    path: Path
    cooldown_until: float = 0.0


class RoundRobinPool:
    """
    Generic round-robin pool with cooldown support.

    - Loads up to 100 files from `sessions/` (any extension).
    - Does NOT use these files to access Telegram private messages.
    """

    def __init__(self, sessions_dir: str, max_items: int = 100, cooldown_sec: int = 20) -> None:
        self._dir = Path(sessions_dir)
        self._max_items = max_items
        self._cooldown_sec = cooldown_sec
        self._items: deque[PoolItem] = deque()

    def load(self) -> int:
        self._dir.mkdir(parents=True, exist_ok=True)
        files = sorted([p for p in self._dir.iterdir() if p.is_file()])[: self._max_items]
        self._items = deque([PoolItem(name=p.name, path=p) for p in files])
        return len(self._items)

    def mark_cooldown(self, name: str) -> None:
        now = time.time()
        for it in self._items:
            if it.name == name:
                it.cooldown_until = now + self._cooldown_sec
                return

    def next_available(self) -> PoolItem | None:
        if not self._items:
            return None
        now = time.time()
        for _ in range(len(self._items)):
            it = self._items[0]
            self._items.rotate(-1)
            if it.cooldown_until <= now:
                return it
        return None

    def list_items(self) -> list[str]:
        return [it.name for it in list(self._items)]

