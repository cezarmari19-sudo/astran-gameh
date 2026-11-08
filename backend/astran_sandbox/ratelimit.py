# backend/astran_sandbox/ratelimit.py
"""Limitator simplu "fereastra glisanta", in memorie, per proces.

Folosit pentru /auth/register, /auth/login (per IP) si /reports (per utilizator).
Limita e PER PROCES: daca rulezi mai multe workere/instante, limita efectiva e de N ori mai
mare. Blocarea pe incercari de login ESUATE (per IP + email) e separata si traieste in
MongoDB (vezi psec.ensure_rate_limit_or_raise), deci e comuna tuturor proceselor.
"""
from __future__ import annotations

import time
from collections import deque
from typing import Callable, Deque, Dict, Optional


class SlidingWindowLimiter:
    def __init__(self, max_events: int, window_seconds: float,
                 clock: Callable[[], float] = time.monotonic, max_keys: int = 20000) -> None:
        self.max_events = max_events
        self.window = window_seconds
        self._clock = clock
        self._max_keys = max_keys
        self._events: Dict[str, Deque[float]] = {}

    def hit(self, key: str) -> Optional[int]:
        """Inregistreaza o cerere. None = permisa; altfel numarul de secunde de asteptat."""
        now = self._clock()
        q = self._events.get(key)
        if q is None:
            if len(self._events) >= self._max_keys:
                self._prune(now)
            q = self._events[key] = deque()
        cutoff = now - self.window
        while q and q[0] <= cutoff:
            q.popleft()
        if len(q) >= self.max_events:
            return max(1, int(q[0] + self.window - now) + 1)
        q.append(now)
        return None

    def _prune(self, now: float) -> None:
        cutoff = now - self.window
        for k in [k for k, q in self._events.items() if not q or q[-1] <= cutoff]:
            del self._events[k]
        if len(self._events) >= self._max_keys:  # tot plin: aruncam cele mai vechi
            for k in list(self._events)[: self._max_keys // 10]:
                del self._events[k]