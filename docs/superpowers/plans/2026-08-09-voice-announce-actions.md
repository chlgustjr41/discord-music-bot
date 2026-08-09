# Voice Announce Actions and Client Directive Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Three new voice verbs — announce the current track to Discord, announce the session code and dashboard link, and open the dashboard in the speaker's browser.

**Architecture:** The two announcing verbs call the *existing* embed builders and post through `ChannelNotifier`, so Discord output is identical whether triggered by voice or by `j!nowplaying` / `j!session`. `open_dashboard` has no server-side effect: it returns a **client directive** in a new top-level `client` array which the plugin executes. The vocabulary stays closed and still has no deletion verb.

**Tech Stack:** unchanged — Python 3.11 / aiohttp / pytest; TypeScript / vitest.

**Spec:** `docs/superpowers/specs/2026-08-09-voice-announce-actions-design.md` — read it first; it governs.

**House rules (every task):** TDD — write the test, run it, watch it fail *for the right reason*, implement, watch it pass. Bot gates: `cd services/bot && py -m pytest -q` and `uvx ruff@0.15.20 check src tests`. Plugin gates: `cd streamdeck-plugin && npm test && npm run build && npx tsc --noEmit`. Windows: use the `py` launcher. Commit per task. **Never run `npx @elgato/cli pack` mid-plan** — it reformats `manifest.json`.

**Baselines:** bot **248** tests; plugin **52** tests. Branch from `master` as `feat/voice-announce`.

**Mutation-testing rule:** this project proves every test non-vacuous. Each task lists mutations to apply; revert the fix, confirm the named test fails, restore. **Restore from a file backup you take first — NOT `git checkout --`**, which reverts to HEAD and wipes the uncommitted implementation along with the mutation. **If a predicted mutation does not fail, say so plainly and strengthen the test** — do not paper over it. Three implementers on the previous plan correctly caught bad predictions in my instructions; the same skepticism is expected.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/jacky/api/voice_actions.py` | modify | three verbs in `_VERBS` + schema enum |
| `src/jacky/api/dashboard_link.py` | **create** | pure URL builders shared by the route and the dispatcher |
| `src/jacky/core/bot.py` | modify | `ChannelNotifier.send` gains `embed`, returns whether it posted |
| `src/jacky/voice_control.py` | modify | `DispatchResult.client`, three handlers, announce cooldown |
| `src/jacky/api/control.py` | modify | `client` array in the response; route uses the shared helper |
| `src/jacky/api/voice_intent.py` | modify | fallback phrases |
| `streamdeck-plugin/src/url-guard.ts` | **create** | `isOpenableUrl` — the scheme check, shared |
| `streamdeck-plugin/src/api-client.ts` | modify | `VoiceResult.client` |
| `streamdeck-plugin/src/actions/voice.ts` | modify | execute directives |
| `streamdeck-plugin/src/actions/dashboard.ts` | modify | apply the same scheme check |
| `docs/streamdeck-control.md` | modify | new phrases + what they post |

---

## Task 1: Vocabulary

**Files:** `services/bot/src/jacky/api/voice_actions.py`, `services/bot/tests/test_voice_actions.py`

- [ ] **Step 1: Write the failing tests.** Append to `services/bot/tests/test_voice_actions.py`:

```python
def test_announce_and_client_verbs_validate():
    assert validate_actions([
        {"action": "now_playing"},
        {"action": "session_info"},
        {"action": "open_dashboard"},
    ]) == [Action("now_playing"), Action("session_info"), Action("open_dashboard")]


def test_new_verbs_need_no_arguments():
    """They are argument-free, so nothing may be dropped for a missing query."""
    assert validate_actions([{"action": "now_playing", "query": ""}]) == [
        Action("now_playing")
    ]
```

Also update the existing `test_schema_declares_the_closed_vocabulary` expected set to include the three new verbs. Its `assert not any("delete" in v or "remove" in v ...)` line stays — the guarantee is unchanged.

- [ ] **Step 2: Run to verify failure**

Run: `cd services/bot && py -m pytest tests/test_voice_actions.py -q`
Expected: FAIL — `validate_actions` drops the unknown verbs, so the first test gets `[]`.

- [ ] **Step 3: Implement.** In `voice_actions.py`, extend `_VERBS` only:

```python
_VERBS = (
    "play", "playlist", "skip", "pause", "resume",
    "volume", "shuffle", "clear_queue", "loop",
    # Read-and-announce, plus one client-side action. None of these removes
    # anything — the no-deletion-verb guarantee is unchanged.
    "now_playing", "session_info", "open_dashboard",
)
```

`ACTION_SCHEMA` reads its enum from `_VERBS`, so it updates automatically. Confirm that by re-reading the schema literal; if it hardcodes a separate list, update that too.

- [ ] **Step 4: Verify.** `py -m pytest -q` → **250 passed**; `uvx ruff@0.15.20 check src tests` clean.

- [ ] **Step 5: Mutation-verify.** Remove `"now_playing"` from `_VERBS` → both new tests and `test_schema_declares_the_closed_vocabulary` must fail. Restore.

- [ ] **Step 6: Commit**

```bash
git add services/bot/src/jacky/api/voice_actions.py services/bot/tests/test_voice_actions.py
git commit -m "feat(voice): add announce and dashboard verbs to the vocabulary"
```

## Task 2: Shared dashboard-link helper

**Files:** `services/bot/src/jacky/api/dashboard_link.py` (create), `services/bot/tests/test_dashboard_link.py` (create), `services/bot/src/jacky/api/control.py`

The route builds the dashboard URL inline. The dispatcher needs the *same* URL — the spec requires voice and the Dashboard key to open the same page **by construction**, not by two code paths that happen to agree.

- [ ] **Step 1: Write the failing tests.** Create `services/bot/tests/test_dashboard_link.py`:

```python
"""Pure URL builders. Shared so the Dashboard key and the voice command
cannot drift apart."""

from jacky.api.dashboard_link import entry_url, session_url


def test_session_url_joins_cleanly():
    assert session_url("https://x.dev", "CODE1234") == "https://x.dev/dashboard/CODE1234"


def test_trailing_slashes_do_not_double_up():
    assert session_url("https://x.dev/", "C1") == "https://x.dev/dashboard/C1"
    assert session_url("https://x.dev///", "C1") == "https://x.dev/dashboard/C1"


def test_entry_url_is_the_no_session_destination():
    assert entry_url("https://x.dev/") == "https://x.dev/app"
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/bot && py -m pytest tests/test_dashboard_link.py -q`
Expected: FAIL — no module `jacky.api.dashboard_link`.

- [ ] **Step 3: Implement.** Create `services/bot/src/jacky/api/dashboard_link.py`:

```python
"""Where a browser should point for a session.

Shared by GET /control/dashboard-url (the Dashboard key) and the voice
`open_dashboard` action, so the two open the same page by construction rather
than by two implementations that happen to agree.
"""


def _base(web_app_url: str) -> str:
    return web_app_url.rstrip("/")


def session_url(web_app_url: str, code: str) -> str:
    return f"{_base(web_app_url)}/dashboard/{code}"


def entry_url(web_app_url: str) -> str:
    return f"{_base(web_app_url)}/app"
```

- [ ] **Step 4: Refactor the route to use it.** In `control.py`'s `dashboard_url` handler, replace the inline f-strings with `session_url(service.settings.web_app_url, code)` and `entry_url(service.settings.web_app_url)`, dropping the now-unused local `web_base`. Import at the top of the file (E402).

The existing `/control/dashboard-url` tests must pass **unchanged** — that is the proof the refactor preserved behavior. If any needs editing, stop: the refactor changed behavior and that is a bug, not a test problem.

- [ ] **Step 5: Verify.** `py -m pytest -q` → **253 passed**; ruff clean.

- [ ] **Step 6: Mutation-verify.** Change `session_url` to `/session/{code}` → `test_session_url_joins_cleanly` **and** the existing dashboard-url route test must both fail. That both fail is the point: it proves the route now genuinely goes through the helper.

- [ ] **Step 7: Commit**

```bash
git add services/bot/src/jacky/api/dashboard_link.py services/bot/tests/test_dashboard_link.py services/bot/src/jacky/api/control.py
git commit -m "refactor(control): share the dashboard URL builder"
```

## Task 3: Notifier gains `embed` and reports success

**Files:** `services/bot/src/jacky/core/bot.py`, `services/bot/tests/` (see step 1)

`ChannelNotifier.send` currently returns `None` and swallows every failure — correct for best-effort playback notifications, wrong for an action whose entire purpose is to post. The announcing verbs must be able to report failure on the key.

- [ ] **Step 1: Write the failing tests.** Find the existing `ChannelNotifier` test file (`grep -rln "ChannelNotifier" services/bot/tests/`). If none exists, create `services/bot/tests/test_notifier.py` with a fake bot exposing `repo.get_state` and `get_channel`, following `tests/conftest.py`'s fake style. Tests:

```python
async def test_send_posts_a_prebuilt_embed(notifier, fake_bot):
    import discord

    embed = discord.Embed(title="hello")
    assert await notifier.send(1, embed=embed) is True
    assert fake_bot.sent_embeds[-1] is embed


async def test_send_reports_false_when_there_is_no_text_channel(notifier, fake_bot):
    """An announce action must fail on the key rather than claim success."""
    fake_bot.state = {}                      # no textChannelId
    assert await notifier.send(1, text="hi") is False


async def test_send_reports_false_when_the_channel_is_unresolvable(notifier, fake_bot):
    fake_bot.channel = None
    assert await notifier.send(1, text="hi") is False


async def test_send_reports_false_when_discord_raises(notifier, fake_bot):
    """Best-effort stays best-effort: it must not raise, only report."""
    async def boom(**_kw):
        raise RuntimeError("discord down")

    fake_bot.channel.send = boom
    assert await notifier.send(1, text="hi") is False


async def test_send_still_returns_true_for_the_existing_track_path(notifier):
    assert await notifier.send(1, track={"title": "Song"}) is True
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/bot && py -m pytest tests/test_notifier.py -q`
Expected: FAIL — `send()` returns `None`, so `is True` fails.

- [ ] **Step 3: Implement.** In `services/bot/src/jacky/core/bot.py`, change `ChannelNotifier.send` to accept `embed` and return `bool`:

```python
    async def send(
        self,
        guild_id: int,
        *,
        text: str | None = None,
        track: dict | None = None,
        embed: "discord.Embed | None" = None,
        error: bool = False,
        text_channel_id: str | None = None,
    ) -> bool:
        """Post to the guild's session text channel. Returns whether it
        actually posted — callers that exist only to announce need to know,
        and every existing caller is free to ignore it."""
        try:
            if text_channel_id is None:
                state = await self.bot.repo.get_state(str(guild_id)) or {}
                text_channel_id = state.get("textChannelId")
            if not text_channel_id:
                return False
            channel = self.bot.get_channel(int(text_channel_id))
            if not channel:
                return False
            if embed is None:
                if track is not None:
                    embed = now_playing_embed(track)
                elif error:
                    embed = error_embed(text or "")
                else:
                    embed = success_embed(text or "")
            await channel.send(embed=embed)
            return True
        except Exception as exc:  # noqa: BLE001 — notifications are best-effort
            log.debug("notify failed for guild %s: %s", guild_id, exc)
            return False
```

Note `embed` takes precedence over `track`/`text`. Existing callers pass neither `embed` nor read the return value, so they are unaffected — verify that by grepping `notifier.send(` across `services/bot/src`.

- [ ] **Step 4: Verify.** `py -m pytest -q` — report the count; ruff clean.

- [ ] **Step 5: Mutation-verify.** Make the no-channel branch `return True` → `test_send_reports_false_when_there_is_no_text_channel` must fail. Then make the `except` branch `return True` → `test_send_reports_false_when_discord_raises` must fail. Restore.

- [ ] **Step 6: Commit**

```bash
git add services/bot/src/jacky/core/bot.py services/bot/tests/
git commit -m "feat(notifier): accept a prebuilt embed and report whether it posted"
```

## Task 4: Dispatcher — three verbs, client directive, cooldown

**Files:** `services/bot/src/jacky/voice_control.py`, `services/bot/tests/conftest.py`, `services/bot/tests/test_voice_control.py`

- [ ] **Step 1: Give the test fakes what they need.** In `services/bot/tests/conftest.py`:

`FakeNotifier` must record embeds and allow forcing failure. Extend it (match its existing style):

```python
    async def send(self, guild_id, *, text=None, track=None, embed=None,
                   error=False, text_channel_id=None):
        if self.fail:                       # default False
            return False
        self.sent.append({"guild_id": guild_id, "text": text,
                          "track": track, "embed": embed})
        return True
```

Keep every attribute existing tests already read. If `FakeNotifier` has no `fail` attribute, add `self.fail = False` to `__init__`.

`FakeSettings` needs `web_app_url` — check whether it already has one; if not add `web_app_url = "http://web.test"`.

- [ ] **Step 2: Write the failing tests.** Append to `services/bot/tests/test_voice_control.py`:

```python
# ── announce + client-directive actions ──────────────────────────────────


async def test_now_playing_posts_the_track_embed(dispatcher, service, guild_id, sid):
    await service.repo.update_state(sid, {"currentTrack": {"title": "Song"}})
    result = (await dispatcher.dispatch_all(guild_id, [Action("now_playing")]))[0]
    assert result.ok
    assert "Song" in result.detail
    assert service.fake_notifier.sent[-1]["embed"] is not None


async def test_now_playing_with_nothing_playing_posts_nothing(
    dispatcher, service, guild_id, sid
):
    """The asker is at the Stream Deck, not reading Discord — so this fails on
    the key rather than announcing 'nothing is playing' to the channel."""
    before = len(service.fake_notifier.sent)
    result = (await dispatcher.dispatch_all(guild_id, [Action("now_playing")]))[0]
    assert result.ok is False
    assert len(service.fake_notifier.sent) == before


async def test_session_info_posts_the_code(dispatcher, service, guild_id, sid):
    await service.repo.update_state(sid, {"sessionCode": "CODE1234"})
    result = (await dispatcher.dispatch_all(guild_id, [Action("session_info")]))[0]
    assert result.ok
    assert "CODE1234" in result.detail
    assert service.fake_notifier.sent[-1]["embed"] is not None


async def test_session_info_without_a_code_posts_nothing(
    dispatcher, service, guild_id
):
    before = len(service.fake_notifier.sent)
    result = (await dispatcher.dispatch_all(guild_id, [Action("session_info")]))[0]
    assert result.ok is False
    assert len(service.fake_notifier.sent) == before


async def test_a_notifier_that_cannot_post_is_reported_as_failure(
    dispatcher, service, guild_id, sid
):
    """Silently reporting success for a message nobody received is the worst
    outcome: the user assumes the channel saw it."""
    await service.repo.update_state(sid, {"currentTrack": {"title": "Song"}})
    service.fake_notifier.fail = True
    result = (await dispatcher.dispatch_all(guild_id, [Action("now_playing")]))[0]
    assert result.ok is False


async def test_open_dashboard_returns_a_directive_and_touches_nothing(
    dispatcher, service, guild_id, sid
):
    await service.repo.update_state(sid, {"sessionCode": "CODE1234"})
    before_updates = len(service.node.updates)
    before_sent = len(service.fake_notifier.sent)
    result = (await dispatcher.dispatch_all(guild_id, [Action("open_dashboard")]))[0]
    assert result.ok
    assert result.client == {
        "type": "open_url", "url": "http://web.test/dashboard/CODE1234",
    }
    assert len(service.node.updates) == before_updates, "no playback effect"
    assert len(service.fake_notifier.sent) == before_sent, "posts nothing"


async def test_open_dashboard_without_a_session_uses_the_entry_url(
    dispatcher, service, guild_id
):
    result = (await dispatcher.dispatch_all(guild_id, [Action("open_dashboard")]))[0]
    assert result.client == {"type": "open_url", "url": "http://web.test/app"}


async def test_existing_actions_carry_no_directive(dispatcher, service, guild_id):
    result = (await dispatcher.dispatch_all(guild_id, [Action("pause")]))[0]
    assert result.client is None


async def test_announce_cooldown_blocks_the_second_post_only(
    dispatcher, service, guild_id, sid
):
    """A misrecognition can now produce a publicly visible message, so the two
    announcing verbs share a short per-guild window."""
    await service.repo.update_state(
        sid, {"currentTrack": {"title": "Song"}, "sessionCode": "CODE1234"}
    )
    results = await dispatcher.dispatch_all(
        guild_id, [Action("now_playing"), Action("session_info")]
    )
    assert [r.ok for r in results] == [True, False]
    assert len(service.fake_notifier.sent) == 1


async def test_the_cooldown_expires(dispatcher, service, guild_id, sid):
    await service.repo.update_state(sid, {"currentTrack": {"title": "Song"}})
    assert (await dispatcher.dispatch_all(guild_id, [Action("now_playing")]))[0].ok
    later = dispatcher.now() + ANNOUNCE_COOLDOWN_S + 1
    dispatcher.now = lambda: later
    assert (await dispatcher.dispatch_all(guild_id, [Action("now_playing")]))[0].ok


async def test_the_cooldown_does_not_block_playback_actions(
    dispatcher, service, guild_id, sid
):
    await service.repo.update_state(sid, {"currentTrack": {"title": "Song"}})
    results = await dispatcher.dispatch_all(
        guild_id, [Action("now_playing"), Action("now_playing"), Action("pause")]
    )
    assert [r.ok for r in results] == [True, False, True]
```

Import `ANNOUNCE_COOLDOWN_S` alongside the dispatcher in the test file, so the expiry test stays correct if the window is ever retuned. For `test_the_cooldown_expires`, the dispatcher exposes an injectable clock (step 4). If you implement the clock differently, adjust this test to drive *your* seam — but it must genuinely advance time rather than reach into private state to clear the record. Say which you chose.

- [ ] **Step 3: Run to verify failure**

Run: `cd services/bot && py -m pytest tests/test_voice_control.py -q`
Expected: FAIL — `Action("now_playing")` falls through to `DispatchResult(False, "Unknown command")`.

- [ ] **Step 4: Implement.** In `services/bot/src/jacky/voice_control.py`:

Add imports at the top (E402):

```python
import time

from jacky.api.dashboard_link import entry_url, session_url
from jacky.commands.embeds import now_playing_embed, session_embed
```

Add the field to `DispatchResult`:

```python
    # Set only by actions the SERVER cannot perform — currently just
    # open_dashboard. The route collects these into the response's `client`
    # array and the plugin executes them. None for every server-side action.
    client: dict | None = None
```

Add the cooldown constant and clock, and extend `__init__`:

```python
ANNOUNCE_COOLDOWN_S = 10.0
```

```python
    def __init__(self, service: Any, repo: Any) -> None:
        self.service, self.repo = service, repo
        # Injectable clock: tests advance time rather than sleep. monotonic,
        # not wall clock, so a system time change cannot wedge the cooldown.
        self.now = time.monotonic
        self._last_announce: dict[int, float] = {}
```

Add the three handlers to `_dispatch_action`, before the final `return DispatchResult(False, "Unknown command")`:

```python
        if kind in ("now_playing", "session_info"):
            return await self._announce(guild_id, sid, kind)
        if kind == "open_dashboard":
            return await self._open_dashboard(sid)
```

And the methods:

```python
    def _announce_allowed(self, guild_id: int) -> bool:
        last = self._last_announce.get(guild_id)
        return last is None or (self.now() - last) >= ANNOUNCE_COOLDOWN_S

    async def _announce(self, guild_id: int, sid: str, kind: str) -> DispatchResult:
        state = await self.repo.get_state(sid) or {}
        if kind == "now_playing":
            current = state.get("currentTrack")
            if not current:
                return DispatchResult(False, "Nothing is playing")
            embed = now_playing_embed(current)
            detail = current.get("title", "Now playing")
        else:
            code = state.get("sessionCode")
            if not code:
                return DispatchResult(False, "No session code")
            embed = session_embed(code, self.service.settings.web_app_url)
            detail = code
        # Checked AFTER the content checks so a cooldown is never reported for
        # an utterance that had nothing to post anyway.
        if not self._announce_allowed(guild_id):
            return DispatchResult(False, "Just posted — try again shortly")
        if not await self.service.notifier.send(guild_id, embed=embed):
            # The channel never received it; saying "posted" would be a lie.
            return DispatchResult(False, "Could not post to Discord")
        self._last_announce[guild_id] = self.now()
        return DispatchResult(True, detail, log_arg=detail)

    async def _open_dashboard(self, sid: str) -> DispatchResult:
        """No server-side effect at all: the browser lives on the client, so
        this only hands the plugin a URL to open."""
        web = self.service.settings.web_app_url
        code = (await self.repo.get_state(sid) or {}).get("sessionCode")
        url = session_url(web, code) if code else entry_url(web)
        return DispatchResult(
            True, "Opening dashboard",
            client={"type": "open_url", "url": url},
        )
```

Note the cooldown is only stamped on a *successful* post — a failed send must not start the window, or one Discord hiccup silently blocks the next 10 seconds of legitimate announcements.

- [ ] **Step 5: Verify.** `py -m pytest -q` — report the count; ruff clean.

- [ ] **Step 6: Mutation-verify.**
1. Stamp `_last_announce` before the `send` call instead of after → `test_a_notifier_that_cannot_post_is_reported_as_failure` still passes, so **add** a test proving a failed post leaves the cooldown clear (two consecutive attempts, first with `fail=True`, second succeeding). Report what you added.
2. Ignore `send`'s return value and always return ok → `test_a_notifier_that_cannot_post_is_reported_as_failure` must fail.
3. Remove the `_announce_allowed` check → `test_announce_cooldown_blocks_the_second_post_only` must fail.
4. Have `_open_dashboard` also call `self.service.skip(guild_id)` → `test_open_dashboard_returns_a_directive_and_touches_nothing` must fail.
5. Move the cooldown check *above* the content checks → the cooldown test still passes, so confirm whether any test distinguishes the ordering; if not, add one (two `now_playing` calls with nothing playing must both report "Nothing is playing", not a cooldown message).

- [ ] **Step 7: Commit**

```bash
git add services/bot/src/jacky/voice_control.py services/bot/tests/conftest.py services/bot/tests/test_voice_control.py
git commit -m "feat(voice): announce now-playing and session info, open the dashboard"
```

## Task 5: Route — the `client` array

**Files:** `services/bot/src/jacky/api/control.py`, `services/bot/tests/test_control_api.py`

- [ ] **Step 1: Write the failing tests.** Append to `services/bot/tests/test_control_api.py`:

```python
async def test_voice_response_carries_client_directives_in_order(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(sid, {"sessionCode": "CODE1234"})
    interpreter.actions = [Action("pause"), Action("open_dashboard")]
    body = await (await client.post("/control/voice", data=WAV, headers=auth)).json()
    assert body["client"] == [
        {"type": "open_url", "url": "http://web.test/dashboard/CODE1234"}
    ]


async def test_voice_response_has_an_empty_client_list_when_there_are_none(
    client, service, guild_id, auth, transcriber, interpreter
):
    put_user_in_voice(service, guild_id)
    interpreter.actions = [Action("pause")]
    body = await (await client.post("/control/voice", data=WAV, headers=auth)).json()
    assert body["client"] == []


async def test_announce_actions_log_under_their_j_command_names(
    client, service, guild_id, sid, auth, transcriber, interpreter
):
    """So the dashboard's history retrigger reaches the matching j! command."""
    put_user_in_voice(service, guild_id)
    await service.repo.update_state(
        sid, {"currentTrack": {"title": "Song"}, "sessionCode": "CODE1234"}
    )
    interpreter.actions = [Action("now_playing")]
    await client.post("/control/voice", data=WAV, headers=auth)
    assert service.repo.command_log[-1][1] == "nowplaying"
```

Check `FakeSettings.web_app_url` matches what Task 4 set (`http://web.test`); if the control-api fixtures use a different settings fake, use its value.

- [ ] **Step 2: Run to verify failure**

Run: `cd services/bot && py -m pytest tests/test_control_api.py -q -k client_directives`
Expected: FAIL — `KeyError: 'client'`.

- [ ] **Step 3: Implement.** In `control.py`, extend `_LOG_COMMAND_FOR`:

```python
_LOG_COMMAND_FOR = {
    "play": "play",
    "playlist": "playlist",
    "volume": "volume",
    "clear_queue": "clear",
    # Retrigger targets: these map onto real j! commands.
    "now_playing": "nowplaying",
    "session_info": "session",
    # open_dashboard has no j! equivalent — it logs under its own name.
}
```

And add `client` to the response dict, alongside the existing keys:

```python
            "client": [r.client for r in results if r.client is not None],
```

- [ ] **Step 4: Verify.** `py -m pytest -q` — report the count; ruff clean. The auth sweep count is unchanged (no new route).

- [ ] **Step 5: Mutation-verify.**
1. Emit `client` unfiltered (`[r.client for r in results]`) → `test_voice_response_has_an_empty_client_list_when_there_are_none` must fail.
2. Drop the `now_playing` entry from `_LOG_COMMAND_FOR` → `test_announce_actions_log_under_their_j_command_names` must fail.

- [ ] **Step 6: Commit**

```bash
git add services/bot/src/jacky/api/control.py services/bot/tests/test_control_api.py
git commit -m "feat(voice): return client directives and log announces as j! commands"
```

## Task 6: Fallback parser phrases

**Files:** `services/bot/src/jacky/api/voice_intent.py`, `services/bot/tests/test_voice_intent.py`

- [ ] **Step 1: Write the failing tests.** Add these parametrize cases to the existing `test_fallback_exact_commands` in `services/bot/tests/test_voice_intent.py`:

```python
        ("what's playing", Action("now_playing")),
        ("now playing", Action("now_playing")),
        ("post the session code", Action("session_info")),
        ("session code", Action("session_info")),
        ("open the dashboard", Action("open_dashboard")),
        ("open dashboard", Action("open_dashboard")),
```

- [ ] **Step 2: Run to verify failure**

Run: `cd services/bot && py -m pytest tests/test_voice_intent.py -q`
Expected: FAIL — each falls through to `Action("play", query=<the phrase>)`.

- [ ] **Step 3: Implement.** Add to `_FALLBACK_EXACT` in `voice_intent.py`:

```python
    "whats playing": Action("now_playing"),
    "what is playing": Action("now_playing"),
    "now playing": Action("now_playing"),
    "post the session code": Action("session_info"),
    "session code": Action("session_info"),
    "post the session": Action("session_info"),
    "open the dashboard": Action("open_dashboard"),
    "open dashboard": Action("open_dashboard"),
```

Note the key is `"whats playing"` without the apostrophe: `_FALLBACK_EXACT` is matched against the **normalized** transcript, which strips punctuation. Verify that by reading the normalization above the table — if it does not strip apostrophes, use the spelling it actually produces.

- [ ] **Step 4: Verify.** `py -m pytest -q` — report the count; ruff clean.

- [ ] **Step 5: Mutation-verify.** Remove the `"open dashboard"` key → that parametrized case must fail. Restore.

- [ ] **Step 6: Commit**

```bash
git add services/bot/src/jacky/api/voice_intent.py services/bot/tests/test_voice_intent.py
git commit -m "feat(voice): fallback phrases for the announce and dashboard verbs"
```

## Task 7: Plugin — the URL guard and directive execution

**Files:** `streamdeck-plugin/src/url-guard.ts` (create), `tests/url-guard.test.ts` (create), `src/api-client.ts`, `src/actions/voice.ts`, `src/actions/dashboard.ts`

- [ ] **Step 1: Write the failing tests.** Create `streamdeck-plugin/tests/url-guard.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { isOpenableUrl } from "../src/url-guard";

describe("isOpenableUrl", () => {
  it("allows https", () => {
    expect(isOpenableUrl("http://web.test/dashboard/CODE1")).toBe(true);
  });

  // The real escalation this guard exists to stop: these do not merely
  // navigate, they execute locally or hand off to another installed app.
  it.each([
    "javascript:alert(1)",
    "file:///C:/Windows/System32/calc.exe",
    "data:text/html,<script>alert(1)</script>",
    "steam://run/1",
    "http://web.test/dashboard/CODE1",
  ])("rejects %s", (url) => {
    expect(isOpenableUrl(url)).toBe(false);
  });

  it.each(["", "not a url", "  ", "https://"])("rejects junk %s", (url) => {
    expect(isOpenableUrl(url)).toBe(false);
  });

  it("is not fooled by a scheme appearing later in the string", () => {
    expect(isOpenableUrl("javascript:void('http://web.test')")).toBe(false);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd streamdeck-plugin && npm test`
Expected: FAIL — cannot resolve `../src/url-guard`.

- [ ] **Step 3: Implement.** Create `streamdeck-plugin/src/url-guard.ts`:

```ts
/**
 * Whether a URL is safe to hand to streamDeck.system.openUrl.
 *
 * The server supplies these URLs — both in a voice `client` directive and in
 * the Dashboard key's response — and `apiUrl` is user-overridable, so the
 * responding server is not guaranteed to be the real one.
 *
 * Origin pinning is deliberately NOT attempted: the plugin has no trusted
 * web-app origin to pin against (only DEFAULT_API_URL, which the user can
 * override), so any such check would be theatre. What this DOES stop is the
 * real escalation — javascript:, file:, data: and custom-scheme URLs that
 * execute locally or hand off to another installed application, rather than
 * merely navigating to an unexpected page.
 */
export function isOpenableUrl(url: string): boolean {
  try {
    return new URL(url).protocol === "https:";
  } catch {
    return false;
  }
}
```

`new URL()` parses the scheme from the start of the string, so the "scheme appearing later" case is handled by construction rather than by string matching — confirm that test passes rather than assuming.

- [ ] **Step 4: Wire it in.**

`src/api-client.ts` — extend the type:

```ts
export type VoiceResult = {
  transcript: string;
  actions: { action: string; ok: boolean; detail: string }[];
  ok: boolean;
  detail: string | null;
  client: { type: string; url?: string }[];
};
```

`src/actions/voice.ts` — after the existing `setTitle` / `showOk` / `showAlert` block inside the `try`, and before the `catch`:

```ts
      // Directives execute AFTER the key has rendered, so opening a browser
      // never delays the feedback the user is waiting on. Unknown types are
      // ignored rather than dispatched generically — the directive vocabulary
      // is closed, exactly like the action vocabulary.
      for (const directive of result.client ?? []) {
        if (directive.type === "open_url" && directive.url
            && isOpenableUrl(directive.url)) {
          await streamDeck.system.openUrl(directive.url);
        }
      }
```

Import `isOpenableUrl` from `../url-guard`, and `streamDeck` if `voice.ts` does not already import it (check first).

`src/actions/dashboard.ts` — apply the same guard to the existing key, which has none today:

```ts
      const { active, url } = await client.dashboardUrl();
      if (!isOpenableUrl(url)) return ev.action.showAlert();
      await streamDeck.system.openUrl(url);
```

Guarding only the new path while the old one stays open would advertise a protection that isn't there.

- [ ] **Step 5: Update the existing voice mock.** `tests/voice.test.ts` mocks `voiceCommand`; add `client: []` to its return shape so the type checks and the loop has something to iterate.

- [ ] **Step 6: Verify.** `cd streamdeck-plugin && npm test && npm run build && npx tsc --noEmit` — all clean; report the test count.

- [ ] **Step 7: Mutation-verify.**
1. Make `isOpenableUrl` return `true` unconditionally → every rejection case must fail.
2. Drop the `isOpenableUrl` call from `voice.ts` → **this is likely invisible** unless a test drives the directive loop. Write one: a fake client returning `client: [{type:"open_url", url:"javascript:alert(1)"}]`, asserting `openUrl` was **not** called, plus a matching https case asserting it **was**. Report whether the mutation was visible before you added it.
3. Change the directive type check to accept any type → add/confirm a test with `{type: "run_command", url: "https://x"}` asserting `openUrl` is not called.

- [ ] **Step 8: Commit**

```bash
git add streamdeck-plugin/src streamdeck-plugin/tests
git commit -m "feat(plugin): execute client directives behind a URL scheme guard"
```

## Task 8: Docs, deploy, verify, pack

- [ ] **Step 1: Runbook.** In `docs/streamdeck-control.md`, add to the Voice Command phrase table:

```markdown
  | "what's playing" | Posts the current track to the Discord channel |
  | "post the session code" | Posts the code + dashboard link to Discord |
  | "open the dashboard" | Opens the dashboard in your browser |
```

And after the table:

```markdown
  The two posting commands write to the session's own text channel — the same
  place `j!nowplaying` and `j!session` post, using the same embeds. They share
  a 10-second per-guild cooldown, so a misrecognition cannot spam the channel.
  "Open the dashboard" opens exactly what the Open Dashboard key opens.
```

- [ ] **Step 2: Merge and deploy.** No new env var and no new secret.

```bash
git checkout master && git merge --no-ff feat/voice-announce -m "Merge feat/voice-announce: announce actions and client directives" && git push origin master
```

```bash
gcloud compute ssh personal-project-machine --project=personal-server-492701 --zone=us-east1-b --command="cd ~/discord-music-bot && sudo git -c safe.directory=\$PWD pull origin master && sudo docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build bot"
```

**Watch the deploy output.** `up -d --build bot` also brings up dependencies and recreates them when their config hash changed — that is how Lavalink got recreated on 2026-08-09. If Lavalink restarts, confirm it returns healthy before declaring success (`docker inspect -f '{{.State.Health.Status}}' jacky-music-lavalink-1`), and see playbook **F4a** in `docs/operations/RUNBOOK.md` if it boot-loops.

- [ ] **Step 3: Verify live.**

```bash
curl -s -o /dev/null -w "%{http_code}\n" -X POST "https://control.jacky-music-bot.com/control/voice"
```

Expected: `401`.

- [ ] **Step 4: Pack.**

```bash
cd streamdeck-plugin && npm run fetch-ffmpeg && npm run build \
  && rm -f com.jacobchoi.jacky-control.streamDeckPlugin \
  && npx @elgato/cli pack com.jacobchoi.jacky-control.sdPlugin --force
```

Then `git checkout -- streamdeck-plugin/com.jacobchoi.jacky-control.sdPlugin/manifest.json` (pack reformats it) and deliver the file.

- [ ] **Step 5: User walkthrough.** "what's playing" posts the track embed; "post the session code" posts code + link; "open the dashboard" opens the same page as the key; two announces in a row — the second reports the cooldown; an utterance mixing an announce with a playback action ("what's playing and pause") does both; and the Command History shows `nowplaying` / `session` rows with the Voice badge.
