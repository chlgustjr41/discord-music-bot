"""In-memory sliding-window limiter, keyed by opaque string (token hash)."""

import time


class SlidingWindow:
    def __init__(self, limit: int = 30, window_s: float = 10.0) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        hits = [t for t in self._hits.get(key, []) if t > now - self.window_s]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True
