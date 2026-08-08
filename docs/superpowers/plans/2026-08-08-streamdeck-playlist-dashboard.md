# Playlist Key, Dashboard Key & Now-Playing Thumbnail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two new Stream Deck keys — load a configured playlist and play it next; open the current session's web dashboard — plus the current track's artwork on the Now Playing key.

**Architecture:** Three new guarded routes on the bot (`GET /control/playlists`, `POST /control/playlist`, `GET /control/dashboard-url`) plus a `thumbnail` field on `now-playing`. The plugin gains two thin action classes, a bounded image-fetch helper, and a Property Inspector section mirroring the existing Summon one.

**Tech Stack:** unchanged — Python 3.11 / aiohttp / pytest; TypeScript / `@elgato/streamdeck` 1.x / vitest.

**Spec:** `docs/superpowers/specs/2026-08-08-streamdeck-playlist-dashboard-design.md` — read it first; it governs on any conflict.

**House rules (every task):** TDD — write the test, watch it fail for the right reason, implement, watch it pass. Bot gates: `cd services/bot && py -m pytest -q` and `uvx ruff@0.15.20 check src tests`. Plugin gates: `cd streamdeck-plugin && npm test && npm run build`. Commit per task with the given message. Use the `py` launcher on this machine (plain `python` lacks pytest).

**Baselines at plan time:** bot 124 tests passing; plugin 23 tests passing; plugin manifest at Version `0.2.0.0`.

---

## File Structure

**Bot (`services/bot/`):**
| File | Change | Responsibility |
|---|---|---|
| `src/jacky/api/control.py` | modify | `thumbnail` on now-playing; extracted membership gate; 3 new routes |
| `tests/conftest.py` | modify | FakeRepo playlist storage |
| `tests/test_control_api.py` | modify | all new route tests |

**Plugin (`streamdeck-plugin/`):**
| File | Change | Responsibility |
|---|---|---|
| `src/api-client.ts` | modify | `playlists()`, `playPlaylist()`, `dashboardUrl()`, `thumbnail` on NowPlaying |
| `src/thumbnail.ts` | create | bounded URL → data-URI fetch |
| `src/actions/now-playing.ts` | modify | artwork rendering |
| `src/actions/playlist.ts` | create | playlist key |
| `src/actions/dashboard.ts` | create | dashboard key |
| `src/pi-bridge.ts` | modify | `get-playlists` event |
| `src/plugin.ts` | modify | register both actions |
| `ui/settings.html` | modify | playlist config section |
| `manifest.json`, `imgs/playlist.svg`, `imgs/dashboard.svg` | modify/create | action defs, icons, version |
| `tests/api-client.test.ts`, `tests/thumbnail.test.ts` | modify/create | unit tests |

---

## Part 1 — Bot

### Task 1: FakeRepo playlists + thumbnail on now-playing

**Files:** `services/bot/tests/conftest.py`, `services/bot/src/jacky/api/control.py`, `services/bot/tests/test_control_api.py`

- [ ] **Step 0: Give FakeMember a display name.** `FakeMember` currently carries only `id` and `voice`, but the playlist route attributes queue entries via `member.display_name` (Task 4). In `services/bot/tests/conftest.py`, add the field so the fake matches the real discord.py surface:

```python
@dataclass
class FakeMember:
    id: int
    voice: FakeVoiceState | None = None
    display_name: str = "Tester"   # matches the token minted in the test fixture
```

- [ ] **Step 1: Add playlist storage to FakeRepo.** In `services/bot/tests/conftest.py`, add `self.playlists: dict[str, dict[str, dict]] = {}` (serverId → name → doc) alongside the other `__init__` fields, and these methods (mirroring `ServerRepository`'s real shapes):

```python
    async def save_playlist(self, sid, name, tracks, created_by):
        self.playlists.setdefault(sid, {})[name] = {
            "name": name, "tracks": list(tracks), "createdBy": created_by,
        }

    async def load_playlist(self, sid, name):
        return self.playlists.get(sid, {}).get(name)

    async def list_playlists(self, sid):
        return list(self.playlists.get(sid, {}).values())

    async def delete_playlist(self, sid, name):
        self.playlists.get(sid, {}).pop(name, None)
```

- [ ] **Step 2: Write the failing test.** Append to `services/bot/tests/test_control_api.py`:

```python
# ── now-playing artwork ──────────────────────────────────────────────────

async def test_now_playing_includes_thumbnail(client, service, guild_id, sid, auth):
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {
        "currentTrack": {"title": "Song", "artist": "A", "thumbnail": "https://i/t.jpg"},
    })
    body = await (await client.get("/control/now-playing", headers=auth)).json()
    assert body["thumbnail"] == "https://i/t.jpg"


async def test_now_playing_thumbnail_is_null_when_absent(
    client, service, guild_id, sid, auth
):
    """Idle session and a track with no artwork both report null, so the key
    clears its image instead of keeping the previous track's cover."""
    put_user_in_voice(service, guild_id)
    body = await (await client.get("/control/now-playing", headers=auth)).json()
    assert body["thumbnail"] is None

    await service.repo.update_state(sid, {"currentTrack": {"title": "Song"}})
    body = await (await client.get("/control/now-playing", headers=auth)).json()
    assert body["thumbnail"] is None
```

- [ ] **Step 3: Run to verify failure**

Run: `cd services/bot && py -m pytest tests/test_control_api.py -q -k thumbnail`
Expected: FAIL — `KeyError: 'thumbnail'`.

- [ ] **Step 4: Implement.** In `services/bot/src/jacky/api/control.py`, inside `now_playing`'s response dict, add after the `"author"` line:

```python
            "thumbnail": (current.get("thumbnail") or None) if current else None,
```

- [ ] **Step 5: Update the one existing test that asserts the whole response.** `test_now_playing_reports_current_track` compares the full response dict by equality, so a new field breaks it. Add `"thumbnail": None` to its expected dict — the field addition is an intentional contract change, not a regression.

- [ ] **Step 6: Verify**

Run: `py -m pytest -q` (expect 126) and `uvx ruff@0.15.20 check src tests`.

- [ ] **Step 7: Commit**

```bash
git add services/bot/tests/conftest.py services/bot/src/jacky/api/control.py services/bot/tests/test_control_api.py
git commit -m "feat(control): now-playing carries the track thumbnail"
```

### Task 2: Extract the membership gate

Pure refactor — `summon` currently inlines a 15-line membership + activation gate that the playlist route needs verbatim. Extract it first so Task 4 has one call site to use, not a copy.

**Files:** `services/bot/src/jacky/api/control.py`

- [ ] **Step 1: Add the helper.** In `register_control_routes`, directly after `member_id_of`, insert:

```python
    async def guild_for_member(user_id: str, raw_guild_id):
        """(guild, member, error_response) for routes that act on a NAMED
        guild rather than the caller's live session.

        Cache first, REST fallback: these routes must work when the caller
        isn't in voice yet, so a cache miss is legitimate. Unknown guilds
        return the same 403 as non-membership — never leak which guilds the
        bot is in.
        """
        try:
            guild_id = int(str(raw_guild_id))
        except (TypeError, ValueError):
            return None, None, web.json_response(
                {"error": "bad-request"}, status=400
            )
        guild = bot.get_guild(guild_id)
        if guild is None:
            return None, None, web.json_response(
                {"error": "not-a-member"}, status=403
            )
        member = guild.get_member(member_id_of(user_id))
        if member is None:
            try:
                member = await guild.fetch_member(member_id_of(user_id))
            except _MEMBER_LOOKUP_ERRORS:
                member = None
        if member is None:
            return None, None, web.json_response(
                {"error": "not-a-member"}, status=403
            )
        if not await service.repo.is_activated(str(guild.id)):
            return None, None, web.json_response(
                {"error": "not-activated"}, status=403
            )
        return guild, member, None
```

- [ ] **Step 2: Rewrite `summon`'s head to use it.** Replace everything in `summon` from `body = await body_of(request)` down to and including the `if not await service.repo.is_activated(...)` block with:

```python
        body = await body_of(request)
        # channelId is parsed BEFORE the membership gate on purpose: the
        # original inlined version validated both ids up front, so a malformed
        # channelId is a 400 regardless of membership. Running the gate first
        # would turn that case into a 403 and break
        # test_summon_400_for_missing_or_non_numeric_fields.
        try:
            channel_id = int(str(body["channelId"]))
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "bad-request"}, status=400)
        guild, _member, err = await guild_for_member(user_id, body.get("guildId"))
        if err:
            return err
```

Leave the rest of `summon` (the voice_client toggle, join, 502 handling) untouched. Note `guild_id` is no longer a local — the `log.exception` call near the bottom references it; change that line to use `guild.id`:

```python
            log.exception(
                "summon join failed (guild %s, channel %s)", guild.id, channel_id
            )
```

- [ ] **Step 3: Verify the refactor changed nothing**

Run: `cd services/bot && py -m pytest -q` (expect 126 — all existing summon tests still pass) and `uvx ruff@0.15.20 check src tests`.
If any summon test fails, the refactor changed behavior — fix it rather than editing the test.

- [ ] **Step 4: Commit**

```bash
git add services/bot/src/jacky/api/control.py
git commit -m "refactor(control): extract the named-guild membership gate"
```

### Task 3: `GET /control/playlists`

**Files:** `services/bot/src/jacky/api/control.py`, `services/bot/tests/test_control_api.py`

- [ ] **Step 1: Write the failing tests.** Append to `services/bot/tests/test_control_api.py`:

```python
# ── playlists listing ────────────────────────────────────────────────────

async def test_playlists_lists_activated_guilds_with_counts(
    client, service, guild_id, sid, auth
):
    guild = service.bot.get_guild(guild_id)
    guild.members_by_id[USER_ID] = FakeMember(id=USER_ID)
    await service.repo.save_playlist(sid, "Chill", [{"title": "a"}, {"title": "b"}], "me")
    await service.repo.save_playlist(sid, "Hype", [{"title": "c"}], "me")

    body = await (await client.get("/control/playlists", headers=auth)).json()
    assert body == [{
        "guildId": sid,
        "guildName": "Guild",
        "playlists": [
            {"name": "Chill", "trackCount": 2},
            {"name": "Hype", "trackCount": 1},
        ],
    }]


async def test_playlists_excludes_deactivated_and_non_member_guilds(
    client, service, guild_id, sid, auth
):
    guild = service.bot.get_guild(guild_id)
    guild.members_by_id[USER_ID] = FakeMember(id=USER_ID)
    await service.repo.save_playlist(sid, "Chill", [{"title": "a"}], "me")
    service.repo.activated_overrides[sid] = False
    assert (await (await client.get("/control/playlists", headers=auth)).json()) == []

    service.repo.activated_overrides[sid] = True
    del guild.members_by_id[USER_ID]
    assert (await (await client.get("/control/playlists", headers=auth)).json()) == []


async def test_playlists_returns_empty_list_when_none_saved(
    client, service, guild_id, auth
):
    service.bot.get_guild(guild_id).members_by_id[USER_ID] = FakeMember(id=USER_ID)
    body = await (await client.get("/control/playlists", headers=auth)).json()
    assert body == [{"guildId": str(guild_id), "guildName": "Guild", "playlists": []}]
```

- [ ] **Step 2: Run to verify failure**

Run: `py -m pytest tests/test_control_api.py -q -k playlists`
Expected: FAIL — 404 responses (route absent).

- [ ] **Step 3: Implement.** In `control.py`, add after the `channels` handler:

```python
    async def playlists(request: web.Request, user_id: str) -> web.Response:
        # Same shape and filtering as `channels`, and deliberately NOT
        # session-gated: the Property Inspector lists these while the user is
        # configuring a key, long before any session exists.
        member_id = member_id_of(user_id)
        out = []
        for guild in bot.guilds:
            if not await service.repo.is_activated(str(guild.id)):
                continue
            if not guild.get_member(member_id):
                continue
            saved = await service.repo.list_playlists(str(guild.id))
            out.append({
                "guildId": str(guild.id),
                "guildName": guild.name,
                "playlists": sorted(
                    (
                        {"name": p["name"], "trackCount": len(p.get("tracks") or [])}
                        for p in saved
                    ),
                    key=lambda p: p["name"].lower(),
                ),
            })
        return web.json_response(out)
```

and register it in the route table:

```python
        web.get("/control/playlists", guarded(playlists)),
```

- [ ] **Step 4: Verify**

Run: `py -m pytest -q` (expect 129) and `uvx ruff@0.15.20 check src tests`.

- [ ] **Step 5: Commit**

```bash
git add services/bot/src/jacky/api/control.py services/bot/tests/test_control_api.py
git commit -m "feat(control): list saved playlists for the property inspector"
```

### Task 4: `POST /control/playlist` — insert at front and play

**Files:** `services/bot/src/jacky/api/control.py`, `services/bot/tests/test_control_api.py`

- [ ] **Step 1: Write the failing tests.** Append to `services/bot/tests/test_control_api.py`:

```python
# ── playlist insert-and-play ─────────────────────────────────────────────

async def arm_playlist_guild(service, guild_id, name="Chill", tracks=None):
    """Member present, in voice, with a live session — the state a playlist
    press requires."""
    put_user_in_voice(service, guild_id)
    await service.repo.save_playlist(
        str(guild_id),
        name,
        [{"title": "P1"}, {"title": "P2"}] if tracks is None else tracks,
        "me",
    )


async def test_playlist_inserts_at_front_and_skips_when_playing(
    client, service, guild_id, sid, auth
):
    await arm_playlist_guild(service, guild_id)
    await service.repo.update_state(sid, {
        "queue": [{"title": "Old"}], "currentTrack": {"title": "Now"},
    })

    resp = await client.post(
        "/control/playlist",
        json={"guildId": sid, "playlistName": "Chill"},
        headers=auth,
    )
    assert resp.status == 200
    assert (await resp.json()) == {"inserted": 2, "playlistName": "Chill"}

    queue = (await service.repo.get_state(sid))["queue"]
    assert [t["title"] for t in queue] == ["P1", "P2", "Old"]
    assert queue[0]["requestedBy"] == "Tester"
    # Something was playing, so it advances via the proven TrackEnd path.
    assert service.node.updates[-1] == (guild_id, {"track": {"encoded": None}})


async def test_playlist_starts_playback_when_idle(client, service, guild_id, sid, auth):
    """Nothing playing: a skip would be a no-op, so play_next runs instead."""
    await arm_playlist_guild(service, guild_id)
    await service.repo.update_state(sid, {"queue": [], "currentTrack": None})

    resp = await client.post(
        "/control/playlist",
        json={"guildId": sid, "playlistName": "Chill"},
        headers=auth,
    )
    assert resp.status == 200
    state = await service.repo.get_state(sid)
    assert state["currentTrack"]["title"] == "P1"
    assert [t["title"] for t in state["queue"]] == ["P2"]


async def test_playlist_unknown_name_is_404(client, service, guild_id, sid, auth):
    await arm_playlist_guild(service, guild_id)
    resp = await client.post(
        "/control/playlist",
        json={"guildId": sid, "playlistName": "Nope"},
        headers=auth,
    )
    assert resp.status == 404
    assert (await resp.json())["error"] == "no-such-playlist"


async def test_playlist_empty_playlist_is_404(client, service, guild_id, sid, auth):
    await arm_playlist_guild(service, guild_id, name="Empty", tracks=[])
    resp = await client.post(
        "/control/playlist",
        json={"guildId": sid, "playlistName": "Empty"},
        headers=auth,
    )
    assert resp.status == 404


async def test_playlist_requires_a_live_session(client, service, guild_id, sid, auth):
    """Member of the guild, playlist exists, but the bot isn't connected."""
    service.bot.get_guild(guild_id).members_by_id[USER_ID] = FakeMember(id=USER_ID)
    service.bot.get_guild(guild_id).voice_client = None
    await service.repo.save_playlist(sid, "Chill", [{"title": "P1"}], "me")
    resp = await client.post(
        "/control/playlist",
        json={"guildId": sid, "playlistName": "Chill"},
        headers=auth,
    )
    assert resp.status == 409
    assert (await resp.json())["error"] == "no-active-session"


async def test_playlist_rejects_bad_body_and_outsiders(client, service, guild_id, auth):
    resp = await client.post("/control/playlist", json={}, headers=auth)
    assert resp.status == 400

    resp = await client.post(
        "/control/playlist",
        json={"guildId": "999999", "playlistName": "Chill"},
        headers=auth,
    )
    assert resp.status == 403
```

- [ ] **Step 2: Run to verify failure**

Run: `py -m pytest tests/test_control_api.py -q -k playlist`
Expected: the new insert tests FAIL with 404 (route absent); the Task 3 listing tests still pass.

- [ ] **Step 3: Implement.** In `control.py`, add after the `playlists` handler:

```python
    async def play_playlist(request: web.Request, user_id: str) -> web.Response:
        """Insert a saved playlist at the head of the queue and jump to it."""
        body = await body_of(request)
        guild, member, err = await guild_for_member(user_id, body.get("guildId"))
        if err:
            return err
        name = body.get("playlistName")
        if not isinstance(name, str) or not name:
            return web.json_response({"error": "bad-request"}, status=400)
        if guild.voice_client is None:
            return web.json_response({"error": "no-active-session"}, status=409)

        sid = str(guild.id)
        doc = await service.repo.load_playlist(sid, name)
        tracks = (doc or {}).get("tracks") or []
        if not tracks:
            return web.json_response({"error": "no-such-playlist"}, status=404)

        # Attribution matches commands/library.py so the leaderboard treats a
        # deck press like a j! load. display_name comes from the member we
        # already resolved for the membership gate.
        requested_by = getattr(member, "display_name", "") or "Stream Deck"
        queued = [{**t, "requestedBy": requested_by} for t in tracks]
        existing = await service.repo.get_queue(sid)
        await service.repo.update_state(sid, {"queue": [*queued, *existing]})

        state = await service.repo.get_state(sid) or {}
        if state.get("currentTrack"):
            # Reuse the TrackEnd path j!skip uses; play_next pops the new head.
            await service.skip(guild.id)
        else:
            # A skip with nothing playing is a no-op, so start explicitly.
            await service.play_next(guild.id)
        return web.json_response({"inserted": len(queued), "playlistName": name})
```

and register it:

```python
        web.post("/control/playlist", guarded(play_playlist)),
```

- [ ] **Step 4: Verify**

Run: `py -m pytest -q` (expect 135) and `uvx ruff@0.15.20 check src tests`.

- [ ] **Step 5: Commit**

```bash
git add services/bot/src/jacky/api/control.py services/bot/tests/test_control_api.py
git commit -m "feat(control): play a saved playlist next from the deck"
```

### Task 5: `GET /control/dashboard-url`

**Files:** `services/bot/src/jacky/api/control.py`, `services/bot/tests/test_control_api.py`

`FakeSettings.web_app_url` is already `"http://web.test"` in conftest, and `PlayerService` stores `settings`, so the handler reads `service.settings.web_app_url`.

- [ ] **Step 1: Write the failing tests.** Append to `services/bot/tests/test_control_api.py`:

```python
# ── dashboard url ────────────────────────────────────────────────────────

async def test_dashboard_url_points_at_the_live_session(
    client, service, guild_id, sid, auth
):
    put_user_in_voice(service, guild_id)
    await service.repo.set_session_code(sid, "ABC123")
    body = await (await client.get("/control/dashboard-url", headers=auth)).json()
    assert body == {
        "active": True,
        "url": "http://web.test/dashboard/ABC123",
        "guildName": "Guild",
    }


async def test_dashboard_url_falls_back_to_the_entry_page(client, auth):
    """No session: the key still opens something useful rather than failing."""
    body = await (await client.get("/control/dashboard-url", headers=auth)).json()
    assert body == {"active": False, "url": "http://web.test/app"}


async def test_dashboard_url_falls_back_when_session_code_missing(
    client, service, guild_id, sid, auth
):
    """Live session whose code was invalidated mid-teardown — never build
    '/dashboard/None'."""
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {"sessionCode": None})
    body = await (await client.get("/control/dashboard-url", headers=auth)).json()
    assert body["active"] is False
    assert body["url"] == "http://web.test/app"
```

- [ ] **Step 2: Run to verify failure**

Run: `py -m pytest tests/test_control_api.py -q -k dashboard`
Expected: FAIL — 404 (route absent).

- [ ] **Step 3: Implement.** In `control.py`, add after `play_playlist`:

```python
    async def dashboard_url(request: web.Request, user_id: str) -> web.Response:
        """Where to point a browser for the caller's current session.

        The code is read live, never cached client-side: begin_session mints a
        new one per session and teardown invalidates it.
        """
        web_base = service.settings.web_app_url.rstrip("/")
        guild = await resolve_guild(member_id_of(user_id))
        if guild is not None:
            state = await service.repo.get_state(str(guild.id)) or {}
            code = state.get("sessionCode")
            if code:
                return web.json_response({
                    "active": True,
                    "url": f"{web_base}/dashboard/{code}",
                    "guildName": guild.name,
                })
        return web.json_response({"active": False, "url": f"{web_base}/app"})
```

and register it:

```python
        web.get("/control/dashboard-url", guarded(dashboard_url)),
```

- [ ] **Step 4: Verify**

Run: `py -m pytest -q` (expect 138) and `uvx ruff@0.15.20 check src tests`.

- [ ] **Step 5: Commit**

```bash
git add services/bot/src/jacky/api/control.py services/bot/tests/test_control_api.py
git commit -m "feat(control): dashboard-url endpoint for the deck"
```

---

## Part 2 — Plugin

### Task 6: API client methods + thumbnail type

**Files:** `streamdeck-plugin/src/api-client.ts`, `streamdeck-plugin/tests/api-client.test.ts`

- [ ] **Step 1: Write the failing tests.** Append inside the existing `describe("JackyClient", ...)` block in `tests/api-client.test.ts`:

```ts
  it("GETs the playlist list", async () => {
    const data = [
      { guildId: "1", guildName: "G", playlists: [{ name: "Chill", trackCount: 2 }] },
    ];
    const f = fetchStub(200, data);
    const result = await new JackyClient(CONFIG, f).playlists();
    const [url, init] = (f as any).mock.calls[0];
    expect(url).toBe("https://control.example.com/control/playlists");
    expect(init.headers.Authorization).toBe("Bearer tok");
    expect(result).toEqual(data);
  });

  it("POSTs a playlist play request and returns the count", async () => {
    const f = fetchStub(200, { inserted: 2, playlistName: "Chill" });
    const result = await new JackyClient(CONFIG, f).playPlaylist("1", "Chill");
    const [url, init] = (f as any).mock.calls[0];
    expect(url).toBe("https://control.example.com/control/playlist");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ guildId: "1", playlistName: "Chill" });
    expect(result.inserted).toBe(2);
  });

  it("GETs the dashboard url", async () => {
    const f = fetchStub(200, { active: true, url: "https://web/dashboard/ABC123" });
    const result = await new JackyClient(CONFIG, f).dashboardUrl();
    expect((f as any).mock.calls[0][0]).toBe(
      "https://control.example.com/control/dashboard-url",
    );
    expect(result).toEqual({ active: true, url: "https://web/dashboard/ABC123" });
  });
```

(`CONFIG` and `fetchStub` already exist at the top of this test file — reuse them, don't add a second fixture.)

- [ ] **Step 2: Run to verify failure**

Run: `cd streamdeck-plugin && npm test`
Expected: FAIL — `client.playlists is not a function`.

- [ ] **Step 3: Implement.** In `src/api-client.ts`, add to the `NowPlaying` active variant (after `guildName`):

```ts
      thumbnail: string | null;
```

Add these exported types next to `ChannelList`:

```ts
export type PlaylistList = {
  guildId: string;
  guildName: string;
  playlists: { name: string; trackCount: number }[];
}[];
export type DashboardUrl = { active: boolean; url: string; guildName?: string };
```

Add these methods to `JackyClient` (alongside `channels()`):

```ts
  async playlists(): Promise<PlaylistList> {
    const res = await this.get("/control/playlists");
    return (await res.json()) as PlaylistList;
  }

  async playPlaylist(
    guildId: string,
    playlistName: string,
  ): Promise<{ inserted: number; playlistName: string }> {
    const res = await this.post("/control/playlist", { guildId, playlistName });
    return (await res.json()) as { inserted: number; playlistName: string };
  }

  async dashboardUrl(): Promise<DashboardUrl> {
    const res = await this.get("/control/dashboard-url");
    return (await res.json()) as DashboardUrl;
  }
```

(`private get(path)` and `private post(path, body)` both already return the raw `Response`, so these bodies drop straight in — `channels()` and `summon()` are the existing templates.)

- [ ] **Step 4: Verify**

Run: `npm test` (expect 26) and `npm run build`.

- [ ] **Step 5: Commit**

```bash
git add streamdeck-plugin/src/api-client.ts streamdeck-plugin/tests/api-client.test.ts
git commit -m "feat(deck): playlist and dashboard client methods"
```

### Task 7: Bounded thumbnail loader

**Files:** `streamdeck-plugin/src/thumbnail.ts`, `streamdeck-plugin/tests/thumbnail.test.ts`

- [ ] **Step 1: Write the failing tests.** Create `streamdeck-plugin/tests/thumbnail.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { loadThumbnail } from "../src/thumbnail";

function imageRes(bytes: number, type = "image/jpeg") {
  return {
    ok: true,
    status: 200,
    headers: { get: (h: string) => (h === "content-type" ? type : null) },
    arrayBuffer: async () => new ArrayBuffer(bytes),
  };
}

describe("loadThumbnail", () => {
  it("returns a data URI for a normal image", async () => {
    const f = vi.fn(async () => imageRes(8)) as unknown as typeof fetch;
    const uri = await loadThumbnail("https://i/t.jpg", f);
    expect(uri).toMatch(/^data:image\/jpeg;base64,/);
  });

  it("returns null on a non-2xx response", async () => {
    const f = vi.fn(async () => ({
      ok: false, status: 404,
      headers: { get: () => null },
      arrayBuffer: async () => new ArrayBuffer(0),
    })) as unknown as typeof fetch;
    expect(await loadThumbnail("https://i/missing.jpg", f)).toBeNull();
  });

  it("rejects a payload past the size ceiling", async () => {
    const f = vi.fn(async () => imageRes(3 * 1024 * 1024)) as unknown as typeof fetch;
    expect(await loadThumbnail("https://i/huge.jpg", f)).toBeNull();
  });

  it("rejects a non-image content type", async () => {
    const f = vi.fn(async () => imageRes(8, "text/html")) as unknown as typeof fetch;
    expect(await loadThumbnail("https://i/page.html", f)).toBeNull();
  });

  it("returns null when the request throws", async () => {
    const f = vi.fn(async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch;
    expect(await loadThumbnail("https://i/t.jpg", f)).toBeNull();
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `npm test`
Expected: FAIL — cannot resolve `../src/thumbnail`.

- [ ] **Step 3: Implement.** Create `streamdeck-plugin/src/thumbnail.ts`:

```ts
/** Fetch track artwork and encode it for `setImage`.
 *
 *  Bounded on purpose: a Stream Deck key is 72px, so anything past the
 *  ceiling is a wrong URL rather than album art. Every failure resolves to
 *  null so the caller can fall back to the default icon — artwork is never
 *  worth breaking the key over.
 */

const MAX_BYTES = 2 * 1024 * 1024;
const TIMEOUT_MS = 5000;

export async function loadThumbnail(
  url: string,
  fetchFn: typeof fetch = fetch,
): Promise<string | null> {
  try {
    const res = await fetchFn(url, { signal: AbortSignal.timeout(TIMEOUT_MS) });
    if (!res.ok) return null;
    const type = res.headers.get("content-type") ?? "image/jpeg";
    if (!type.startsWith("image/")) return null;
    const buf = await res.arrayBuffer();
    if (buf.byteLength === 0 || buf.byteLength > MAX_BYTES) return null;
    return `data:${type.split(";")[0]};base64,${Buffer.from(buf).toString("base64")}`;
  } catch {
    return null;
  }
}
```

- [ ] **Step 4: Verify**

Run: `npm test` (expect 31) and `npm run build`.

- [ ] **Step 5: Commit**

```bash
git add streamdeck-plugin/src/thumbnail.ts streamdeck-plugin/tests/thumbnail.test.ts
git commit -m "feat(deck): bounded thumbnail fetch to data URI"
```

### Task 8: Artwork on the Now Playing key

**Files:** `streamdeck-plugin/src/actions/now-playing.ts`, `streamdeck-plugin/com.jacobchoi.jacky-control.sdPlugin/manifest.json`

No unit test — this is SDK I/O over the tested `loadThumbnail`; Task 10 verifies it live.

- [ ] **Step 1: Track the artwork URL.** In `src/actions/now-playing.ts`, add the import and two fields:

```ts
import { loadThumbnail } from "../thumbnail";
```

```ts
  private lastThumbUrl: string | null = null;
```

- [ ] **Step 2: Render it.** Inside the existing `onPoll` handler, after the block that computes `text` and before/after the `setTitle` loop, add artwork handling:

```ts
    const thumb = s.kind === "data" && s.data.active ? s.data.thumbnail : null;
    if (thumb !== this.lastThumbUrl) {
      this.lastThumbUrl = thumb;
      // Only refetch when the track actually changes, never per poll tick.
      if (thumb) {
        void loadThumbnail(thumb).then((uri) => {
          // A slow fetch may land after another track change — drop it.
          if (uri && this.lastThumbUrl === thumb) {
            for (const a of this.actions) void a.setImage(uri).catch(() => {});
          }
        });
      } else {
        // No artwork / no session: back to the manifest icon so a stale
        // cover never outlives its track.
        for (const a of this.actions) void a.setImage().catch(() => {});
      }
    }
```

- [ ] **Step 3: Reset on disappear.** In `onWillDisappear`, inside the existing `if (--this.visible === 0)` block, add:

```ts
      this.lastThumbUrl = null;
```

- [ ] **Step 4: Move the title below the art.** In `manifest.json`, in the `com.jacobchoi.jacky-control.now-playing` action's `States[0]`, change `"TitleAlignment": "middle"` to `"TitleAlignment": "bottom"`.

- [ ] **Step 5: Verify**

Run: `npm test` (expect 31, unchanged) and `npm run build` (clean).

- [ ] **Step 6: Commit**

```bash
git add streamdeck-plugin/src/actions/now-playing.ts streamdeck-plugin/com.jacobchoi.jacky-control.sdPlugin/manifest.json
git commit -m "feat(deck): show track artwork on the Now Playing key"
```

### Task 9: Playlist + Dashboard keys, PI, manifest

**Files:** `src/actions/playlist.ts`, `src/actions/dashboard.ts` (create); `src/pi-bridge.ts`, `src/plugin.ts`, `ui/settings.html`, `manifest.json`, `imgs/playlist.svg`, `imgs/dashboard.svg`

- [ ] **Step 1: Playlist action.** Create `streamdeck-plugin/src/actions/playlist.ts`:

```ts
import {
  action,
  SingletonAction,
  type JsonValue,
  type KeyDownEvent,
  type SendToPluginEvent,
} from "@elgato/streamdeck";
import { handlePiEvent } from "../pi-bridge";
import { getClient } from "../runtime";

type PlaylistSettings = {
  guildId?: string;
  playlistName?: string;
};

@action({ UUID: "com.jacobchoi.jacky-control.playlist" })
export class Playlist extends SingletonAction<PlaylistSettings> {
  override onSendToPlugin(ev: SendToPluginEvent<JsonValue, PlaylistSettings>): Promise<void> {
    return handlePiEvent(ev.payload);
  }

  override async onKeyDown(ev: KeyDownEvent<PlaylistSettings>): Promise<void> {
    const client = getClient();
    const { guildId, playlistName } = await ev.action.getSettings<PlaylistSettings>();
    if (!client || !guildId || !playlistName) return ev.action.showAlert();
    try {
      await client.playPlaylist(guildId, playlistName);
      await ev.action.showOk();
    } catch {
      await ev.action.showAlert();
    }
  }
}
```

- [ ] **Step 2: Dashboard action.** Create `streamdeck-plugin/src/actions/dashboard.ts`:

```ts
import streamDeck, {
  action,
  SingletonAction,
  type JsonValue,
  type KeyDownEvent,
  type SendToPluginEvent,
} from "@elgato/streamdeck";
import { handlePiEvent } from "../pi-bridge";
import { getClient } from "../runtime";

@action({ UUID: "com.jacobchoi.jacky-control.dashboard" })
export class Dashboard extends SingletonAction {
  override onSendToPlugin(ev: SendToPluginEvent<JsonValue, object>): Promise<void> {
    return handlePiEvent(ev.payload);
  }

  override async onKeyDown(ev: KeyDownEvent): Promise<void> {
    const client = getClient();
    if (!client) return ev.action.showAlert();
    try {
      const { active, url } = await client.dashboardUrl();
      await streamDeck.system.openUrl(url);
      // Still opens the entry page when there's no session — the flash just
      // says "there was nothing to jump to".
      if (active) await ev.action.showOk();
      else await ev.action.showAlert();
    } catch {
      await ev.action.showAlert();
    }
  }
}
```

- [ ] **Step 3: PI bridge event.** In `src/pi-bridge.ts`, add a case alongside `"get-channels"`:

```ts
    case "get-playlists": {
      const client = getClient();
      if (!client) {
        await reply({ event: "playlists-error", error: "not signed in" });
        break;
      }
      try {
        const data = await client.playlists();
        await reply({ event: "playlists", data });
      } catch (err) {
        const error = err instanceof Error ? err.message : String(err);
        await reply({ event: "playlists-error", error });
      }
      break;
    }
```

- [ ] **Step 4: Register the actions.** In `src/plugin.ts`, add the imports and two `registerAction` calls before `streamDeck.connect()`:

```ts
import { Dashboard } from "./actions/dashboard";
import { Playlist } from "./actions/playlist";
```

```ts
streamDeck.actions.registerAction(new Playlist());
streamDeck.actions.registerAction(new Dashboard());
```

- [ ] **Step 5: Icons.** Create `com.jacobchoi.jacky-control.sdPlugin/imgs/playlist.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><rect width="72" height="72" rx="14" fill="#1a1a2e"/><rect x="14" y="20" width="30" height="5" rx="2.5" fill="#e94560"/><rect x="14" y="32" width="30" height="5" rx="2.5" fill="#e94560"/><rect x="14" y="44" width="18" height="5" rx="2.5" fill="#e94560"/><path d="M44 40 L58 47 L44 54 Z" fill="#e94560"/></svg>
```

Create `com.jacobchoi.jacky-control.sdPlugin/imgs/dashboard.svg`:

```svg
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 72 72"><rect width="72" height="72" rx="14" fill="#1a1a2e"/><rect x="14" y="16" width="44" height="34" rx="4" fill="none" stroke="#e94560" stroke-width="4"/><rect x="14" y="16" width="44" height="9" rx="4" fill="#e94560"/><rect x="21" y="32" width="12" height="11" rx="2" fill="#e94560"/><rect x="38" y="32" width="14" height="4" rx="2" fill="#e94560"/><rect x="38" y="39" width="14" height="4" rx="2" fill="#e94560"/><rect x="30" y="54" width="12" height="4" rx="2" fill="#e94560"/></svg>
```

- [ ] **Step 6: Manifest.** In `manifest.json`, bump `"Version"` to `"0.3.0.0"` and append two entries to `Actions`:

```json
    {
      "UUID": "com.jacobchoi.jacky-control.playlist",
      "Name": "Play Playlist",
      "Icon": "imgs/playlist",
      "Tooltip": "Insert a saved playlist and play it next",
      "PropertyInspectorPath": "ui/settings.html",
      "Controllers": ["Keypad"],
      "States": [{ "Image": "imgs/playlist", "TitleAlignment": "bottom" }]
    },
    {
      "UUID": "com.jacobchoi.jacky-control.dashboard",
      "Name": "Open Dashboard",
      "Icon": "imgs/dashboard",
      "Tooltip": "Open this session's web dashboard",
      "PropertyInspectorPath": "ui/settings.html",
      "Controllers": ["Keypad"],
      "States": [{ "Image": "imgs/dashboard", "TitleAlignment": "bottom" }]
    }
```

- [ ] **Step 7: Property Inspector.** In `ui/settings.html`, add a playlist section after the existing `#summon-settings` div:

```html
  <!-- Playlist key only: which saved playlist this key loads. -->
  <div id="playlist-settings" style="display: none">
    <sdpi-item label="Server">
      <sdpi-select id="pl-guild-select" setting="guildId" placeholder="Select a server"></sdpi-select>
    </sdpi-item>
    <sdpi-item label="Playlist">
      <sdpi-select id="pl-select" setting="playlistName" placeholder="Select a playlist"></sdpi-select>
    </sdpi-item>
    <div id="playlist-error" style="display: none; padding-left: 4px; font-size: 9pt; color: #e94560"></div>
  </div>
```

In the inline `<script>`, add element handles next to the existing ones:

```js
    const playlistDiv = document.getElementById("playlist-settings");
    const plGuildSelect = document.getElementById("pl-guild-select");
    const plSelect = document.getElementById("pl-select");
    const playlistError = document.getElementById("playlist-error");
    let playlistData = [];
    let isPlaylist = false;
```

Add the refill helper and change listener next to `refreshChannelOptions`:

```js
    function refreshPlaylistOptions() {
      const g = playlistData.find((x) => x.guildId === plGuildSelect.value);
      setOptions(
        plSelect,
        (g ? g.playlists : []).map((p) => ({
          value: p.name,
          label: `${p.name} (${p.trackCount})`,
        })),
      );
    }

    plGuildSelect.addEventListener("valuechange", refreshPlaylistOptions);
```

Add reply handling inside the existing `sendToPropertyInspector.subscribe` callback, alongside the `channels` branches:

```js
      } else if (p.event === "playlists") {
        playlistError.style.display = "none";
        playlistData = p.data || [];
        setOptions(
          plGuildSelect,
          playlistData.map((g) => ({ value: g.guildId, label: g.guildName })),
        );
        refreshPlaylistOptions();
      } else if (p.event === "playlists-error") {
        playlistError.textContent = "Could not load playlists: " + (p.error || "unknown error");
        playlistError.style.display = "block";
```

In the same callback's `auth-status` branch, after the existing `if (p.ok && isSummon) sendToPlugin({ event: "get-channels" });` line, add:

```js
        if (p.ok && isPlaylist) sendToPlugin({ event: "get-playlists" });
```

Finally, in the boot IIFE, alongside the existing `isSummon` branch:

```js
      isPlaylist = actionInfo.action.endsWith(".playlist");
      if (isPlaylist) {
        playlistDiv.style.display = "";
        sendToPlugin({ event: "get-playlists" });
      }
```

- [ ] **Step 8: Verify**

Run: `npm test` (expect 31), `npm run build` (clean), and
`npx @elgato/cli@latest validate com.jacobchoi.jacky-control.sdPlugin`
Expected: 0 errors (the 2 known cosmetic warnings — Category name, plugin-icon dupe — are acceptable).

- [ ] **Step 9: Commit**

```bash
git add streamdeck-plugin/
git commit -m "feat(deck): playlist and dashboard keys, v0.3.0.0"
```

### Task 10: Docs, deploy, live verification, pack (controller + user)

**Files:** `docs/streamdeck-control.md`

- [ ] **Step 1: Document the new keys.** In `docs/streamdeck-control.md`, add to the behavior notes:

```markdown
- **Play Playlist** key: configured per key with a server + saved playlist
  (create playlists with `j!playlist save`). Pressing it inserts that playlist
  at the front of the queue and jumps to it; whatever was queued stays behind
  it. Needs a live session in that server — ⚠ otherwise.
- **Open Dashboard** key: opens this session's dashboard in your browser. With
  no live session it opens the site's entry page and flashes ⚠.
- The Now Playing key shows the current track's artwork; it clears back to the
  default icon when the session ends.
```

- [ ] **Step 2: Merge and deploy.** From the repo root:

```bash
git checkout master && git merge --no-ff feat/streamdeck-playlist -m "Merge feat/streamdeck-playlist: playlist + dashboard keys, now-playing artwork" && git push origin master
```

Then on the VM (bot only — no env or compose changes in this feature):

```bash
gcloud compute ssh personal-project-machine --project=personal-server-492701 --zone=us-east1-b --command="cd ~/discord-music-bot && sudo git -c safe.directory=\$PWD pull origin master && sudo docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build bot"
```

- [ ] **Step 3: Verify the routes through the tunnel.** All three must return 401 without a bearer token (auth is enforced before anything else):

```bash
for p in playlists dashboard-url; do curl -s -o /dev/null -w "$p %{http_code}\n" "https://control.jacky-music-bot.com/control/$p"; done
curl -s -o /dev/null -w "playlist %{http_code}\n" -X POST "https://control.jacky-music-bot.com/control/playlist"
```

Expected: `401` for all three.

- [ ] **Step 4: Pack and deliver.**

```bash
cd streamdeck-plugin && npm run build && rm -f com.jacobchoi.jacky-control.streamDeckPlugin && npx @elgato/cli pack com.jacobchoi.jacky-control.sdPlugin --force
```

Send the `.streamDeckPlugin` file to the user.

- [ ] **Step 5: User walkthrough.** Install, then check:
  - Play Playlist key: settings show the server + playlist dropdowns (with track counts); pressing during playback jumps to the playlist and the old queue survives behind it; pressing with nothing playing starts it; pressing with no session flashes ⚠.
  - Open Dashboard key: opens the right session dashboard; with no session opens the entry page + ⚠.
  - Now Playing key: artwork appears, changes with the track, and clears when the session ends.
