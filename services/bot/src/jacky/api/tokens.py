"""Per-user control-API tokens. Raw token only ever exists client-side;
Firestore stores sha256(token) as the document id (spec §Decisions)."""

import datetime
import hashlib
import secrets
import time
from typing import Any

CACHE_TTL_S = 300.0
TOUCH_MIN_INTERVAL_S = 60.0


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class TokenStore:
    def __init__(self, repo: Any) -> None:
        self.repo = repo
        self._cache: dict[str, tuple[str, float]] = {}  # hash -> (userId, cached_at)
        self._last_touch: dict[str, float] = {}

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    async def mint(self, user_id: str, user_name: str) -> str:
        token = secrets.token_hex(32)
        h = self._hash(token)
        now = _now_iso()
        await self.repo.save_control_token(h, {
            "userId": user_id, "userName": user_name,
            "createdAt": now, "lastUsed": now,
        })
        self._cache[h] = (user_id, time.monotonic())
        return token

    async def resolve(self, token: str) -> str | None:
        h = self._hash(token)
        hit = self._cache.get(h)
        if hit and time.monotonic() - hit[1] < CACHE_TTL_S:
            await self._maybe_touch(h)
            return hit[0]
        data = await self.repo.get_control_token(h)
        if data is None:
            self._cache.pop(h, None)
            return None
        self._cache[h] = (data["userId"], time.monotonic())
        await self._maybe_touch(h)
        return data["userId"]

    async def _maybe_touch(self, h: str) -> None:
        now = time.monotonic()
        if now - self._last_touch.get(h, 0.0) >= TOUCH_MIN_INTERVAL_S:
            self._last_touch[h] = now
            await self.repo.touch_control_token(h, _now_iso())

    async def revoke_user(self, user_id: str) -> int:
        count = await self.repo.delete_control_tokens_for_user(user_id)
        self._cache = {h: v for h, v in self._cache.items() if v[0] != user_id}
        return count
