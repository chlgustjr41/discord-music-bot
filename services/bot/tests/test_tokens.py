"""TokenStore (mint/resolve/revoke/touch, sha256 at rest, TTL cache) + SlidingWindow."""

import hashlib

from conftest import FakeRepo

from jacky.api import ratelimit as ratelimit_mod
from jacky.api import tokens as tokens_mod
from jacky.api.ratelimit import SlidingWindow
from jacky.api.tokens import TokenStore


class CountingRepo(FakeRepo):
    """FakeRepo that counts get_control_token / touch_control_token calls."""

    def __init__(self) -> None:
        super().__init__()
        self.get_calls = 0
        self.touch_calls = 0

    async def get_control_token(self, token_hash):
        self.get_calls += 1
        return await super().get_control_token(token_hash)

    async def touch_control_token(self, token_hash, iso_now):
        self.touch_calls += 1
        await super().touch_control_token(token_hash, iso_now)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ── mint ──


async def test_mint_returns_64_hex_token():
    store = TokenStore(FakeRepo())
    token = await store.mint("u1", "Alice")
    assert len(token) == 64
    int(token, 16)  # raises if not hex


async def test_mint_persists_only_sha256_hash_with_expected_fields():
    repo = FakeRepo()
    store = TokenStore(repo)
    token = await store.mint("u1", "Alice")
    assert token not in repo.control_tokens
    h = hashlib.sha256(token.encode()).hexdigest()
    assert h in repo.control_tokens
    doc = repo.control_tokens[h]
    assert doc["userId"] == "u1"
    assert doc["userName"] == "Alice"
    assert set(doc) == {"userId", "userName", "createdAt", "lastUsed"}
    assert doc["createdAt"] == doc["lastUsed"]


# ── resolve ──


async def test_resolve_valid_token_returns_user_id():
    repo = FakeRepo()
    store = TokenStore(repo)
    token = await store.mint("u1", "Alice")
    assert await store.resolve(token) == "u1"


async def test_resolve_unknown_token_returns_none():
    store = TokenStore(FakeRepo())
    assert await store.resolve("f" * 64) is None


async def test_resolve_uncached_token_hits_repo_then_caches():
    repo = CountingRepo()
    minter = TokenStore(repo)
    token = await minter.mint("u1", "Alice")

    store = TokenStore(repo)  # fresh store: empty cache
    assert await store.resolve(token) == "u1"
    assert repo.get_calls == 1
    assert await store.resolve(token) == "u1"
    assert repo.get_calls == 1  # served from cache, no second repo read


async def test_resolve_after_mint_is_cached_no_repo_read():
    repo = CountingRepo()
    store = TokenStore(repo)
    token = await store.mint("u1", "Alice")
    assert await store.resolve(token) == "u1"
    assert repo.get_calls == 0


async def test_resolve_cache_expires_after_ttl(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(tokens_mod.time, "monotonic", clock.monotonic)
    repo = CountingRepo()
    store = TokenStore(repo)
    token = await store.mint("u1", "Alice")

    clock.advance(tokens_mod.CACHE_TTL_S + 1)
    assert await store.resolve(token) == "u1"
    assert repo.get_calls == 1  # cache entry expired, repo consulted


# ── revoke ──


async def test_revoke_user_clears_repo_and_cache():
    repo = CountingRepo()
    store = TokenStore(repo)
    token = await store.mint("u1", "Alice")
    assert await store.resolve(token) == "u1"  # cached

    assert await store.revoke_user("u1") == 1
    assert repo.control_tokens == {}
    assert await store.resolve(token) is None


async def test_revoke_user_leaves_other_users_tokens():
    repo = FakeRepo()
    store = TokenStore(repo)
    t1 = await store.mint("u1", "Alice")
    t2 = await store.mint("u2", "Bob")
    await store.revoke_user("u1")
    assert await store.resolve(t1) is None
    assert await store.resolve(t2) == "u2"


# ── touch throttling ──


async def test_touch_throttled_within_min_interval(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(tokens_mod.time, "monotonic", clock.monotonic)
    repo = CountingRepo()
    store = TokenStore(repo)
    token = await store.mint("u1", "Alice")

    await store.resolve(token)
    assert repo.touch_calls == 1
    clock.advance(30)  # within TOUCH_MIN_INTERVAL_S
    await store.resolve(token)
    assert repo.touch_calls == 1  # throttled: still one repo write


async def test_touch_writes_again_after_interval(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(tokens_mod.time, "monotonic", clock.monotonic)
    repo = CountingRepo()
    store = TokenStore(repo)
    token = await store.mint("u1", "Alice")

    await store.resolve(token)
    clock.advance(tokens_mod.TOUCH_MIN_INTERVAL_S)
    await store.resolve(token)
    assert repo.touch_calls == 2


# ── SlidingWindow ──


def test_sliding_window_allows_up_to_limit_then_blocks():
    window = SlidingWindow(limit=3, window_s=10.0)
    assert window.allow("k")
    assert window.allow("k")
    assert window.allow("k")
    assert not window.allow("k")


def test_sliding_window_keys_are_independent():
    window = SlidingWindow(limit=1, window_s=10.0)
    assert window.allow("a")
    assert not window.allow("a")
    assert window.allow("b")


def test_sliding_window_expiry_reallows(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(ratelimit_mod.time, "monotonic", clock.monotonic)
    window = SlidingWindow(limit=2, window_s=10.0)
    assert window.allow("k")
    assert window.allow("k")
    assert not window.allow("k")
    clock.advance(10.1)
    assert window.allow("k")


def test_sliding_window_partial_expiry(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(ratelimit_mod.time, "monotonic", clock.monotonic)
    window = SlidingWindow(limit=2, window_s=10.0)
    assert window.allow("k")
    clock.advance(6)
    assert window.allow("k")
    assert not window.allow("k")
    clock.advance(5)  # first hit (t=0) expired, second (t=6) still in window
    assert window.allow("k")
    assert not window.allow("k")
