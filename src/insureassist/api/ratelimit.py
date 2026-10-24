"""A small in-memory rate limiter (sliding window): at most `limit` hits per `window`
seconds per key (a user, or an IP + e-mail for logins).

In-memory = per API process, reset on restart. That fits the single-process v1
(ADR-0009); a multi-server deployment would use the gateway or a shared store instead.
"""

import time
from collections import deque
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self, limit: int, window_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.limit, self.window, self.clock = limit, window_seconds, clock
        self._hits: dict[str, deque[float]] = {}

    def hit(self, key: str) -> float | None:
        """Record one hit. Returns None if allowed, else the seconds to wait."""
        now = self.clock()
        hits = self._hits.setdefault(key, deque())
        while hits and hits[0] <= now - self.window:
            hits.popleft()  # forget hits that left the window
        if len(hits) >= self.limit:
            return round(hits[0] + self.window - now, 1)
        hits.append(now)
        return None
