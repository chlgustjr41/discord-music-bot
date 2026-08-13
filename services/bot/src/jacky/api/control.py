"""Stream Deck control API (spec: 2026-08-07-streamdeck-oauth-summon-design).

Mounted on the same aiohttp app as /health, so auth is a per-route wrapper,
NOT an app middleware — the guardian polls /health unauthenticated.

Auth: per-user bearer tokens (TokenStore, sha256 at rest). Identity derives
server-side from the token — the wire contract carries no discordUserId.
Rate limiting runs AFTER successful auth (Task 2 security review): invalid
tokens must never grow the limiter's key set.

Session resolution: the target guild is the first one where the token's user
is currently in a voice channel AND the bot holds a voice client (a live
session). Same liveness signal PlayerService.handle_summon uses.
"""

import hashlib
import logging
import time
from typing import Any

import discord
from aiohttp import web

from jacky.api.dashboard_link import entry_url, session_url
from jacky.api.transcribe import normalize_language
from jacky.api.voice_actions import MAX_ACTIONS, enforce_intent
from jacky.api.voice_grammar import parse_structured
from jacky.commands.embeds import now_playing_embed, queue_embed, session_embed
from jacky.commands.status import build_status_embed, fetch_guardian_status

# Imported rather than redeclared: the announce key's window and the voice
# announce window are independent STATE (see AnnounceCooldown), but "how long
# does 'just posted' last" is one product decision, and a single constant
# keeps the two from silently diverging.
from jacky.voice_control import ANNOUNCE_COOLDOWN_S

log = logging.getLogger("jacky.control")

# Errors that mean "member lookup came back negative", not "the API broke".
# Module-level so tests can monkeypatch it with the conftest FakeNotFound
# (constructing a real discord.NotFound requires a fake aiohttp response).
_MEMBER_LOOKUP_ERRORS: tuple = (discord.NotFound, discord.HTTPException)

# 600 KB ~= 18 s of 16 kHz mono WAV, comfortably above the client's 15 s cap.
VOICE_MAX_BYTES = 600_000

# Maps a voice verb to the j! command name the dashboard's history DISPLAYS,
# so a spoken command reads the same as the typed one it corresponds to.
# Verbs not listed here log under their own name.
#
# This is a display/reading mapping only — it does not make the row's
# retrigger button work. The listener's _handle_retrigger dispatches on the
# logged command name and only implements play/skip/pause/resume/loop/volume,
# so retriggering a "nowplaying", "session", "clear" or "playlist" row writes
# a new history row and does nothing else.
_LOG_COMMAND_FOR = {
    "play": "play",
    "playlist": "playlist",
    "volume": "volume",
    "clear_queue": "clear",
    # These name real j! commands, so history reads naturally.
    "now_playing": "nowplaying",
    "session_info": "session",
    # open_dashboard has no j! equivalent — it logs under its own name.
}


# Query values that turn the per-press debug echo ON. Anything else — absent,
# empty, "0", "false", a typo — leaves it off: the echo publishes transcribed
# speech to a channel other people can read, so the server must never enable it
# by inference. Matched case-insensitively after stripping whitespace.
_DEBUG_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _wants_debug(raw: str | None) -> bool:
    return raw is not None and raw.strip().lower() in _DEBUG_TRUTHY


def _describe_action(action: Any) -> str:
    """`playlist(chill, next)` — the verb plus whatever the route actually
    placed on it, so the echo distinguishes what was DECIDED from what was
    heard. Only fields that carry a decision are shown; the rest are noise."""
    parts: list[str] = []
    if action.query:
        parts.append(action.query)
    if action.name:
        parts.append(action.name)
    if action.action in ("play", "playlist"):
        parts.append(action.placement)
    if action.count != 1:
        parts.append(f"x{action.count}")
    if action.level is not None:
        parts.append(f"level {action.level}")
    if action.delta is not None:
        parts.append(f"delta {action.delta:+d}")
    if action.action == "loop":
        parts.append(action.mode)
    return f"{action.action}({', '.join(parts)})" if parts else action.action


def build_debug_message(transcript: str, resolved_by: str, pairs) -> str:
    """The echo itself. `pairs` is (action, result-detail-or-None).

    Three lines, because the whole point is that a reader can tell what was
    HEARD from what the bot DECIDED — collapsing them into one sentence is how
    that distinction gets lost.
    """
    if pairs:
        rendered = "; ".join(
            f"{_describe_action(a)} → {detail or '—'}" for a, detail in pairs
        )
    else:
        rendered = "(none)"
    return (
        f'🎙️ Heard: "{transcript}"\n'
        f"Resolved by: {resolved_by}\n"
        f"Actions: {rendered}"
    )


# The closed allowlist for POST /control/announce — each name is BOTH the
# wire command and the j! name the history row logs under. A command runner
# this is not: anything else is a 400, and mutations have their own routes.
_ANNOUNCE_COMMANDS = frozenset({"session", "nowplaying", "queue", "status"})


class AnnounceCooldown:
    """Per-guild window for POST /control/announce.

    Deliberately its OWN state, never VoiceIntentDispatcher's: the dispatcher
    is None when OPENAI_API_KEY is unset, and a posting key must work on a
    bot with voice off. Two independent 10 s windows on the same channel is
    accepted and documented (spec: 2026-08-11-announce-key-design).
    """

    def __init__(self) -> None:
        # Injectable clock, mirroring VoiceIntentDispatcher: tests advance
        # time rather than sleep. monotonic, not wall clock, so a system time
        # change cannot wedge the cooldown.
        self.now = time.monotonic
        self._last: dict[int, float] = {}

    def allowed(self, guild_id: int) -> bool:
        last = self._last.get(guild_id)
        return last is None or (self.now() - last) >= ANNOUNCE_COOLDOWN_S

    def stamp(self, guild_id: int) -> None:
        self._last[guild_id] = self.now()


def _is_valid_document_id(name: str) -> bool:
    """Firestore document-id rules we can violate from a request body.

    "/" is a path separator (odd segment counts raise, even counts silently
    address a DIFFERENT document); "." and ".." are path traversal; __x__ is
    reserved. All of these reach the SDK as a 500 unless rejected here.
    """
    return (
        bool(name)
        and "/" not in name
        and name not in (".", "..")
        and not (name.startswith("__") and name.endswith("__"))
    )


def register_control_routes(
    app: web.Application, *, bot: Any, service: Any, token_store: Any, limiter: Any,
    transcriber: Any = None, voice_dispatcher: Any = None,
    interpreter: Any = None,
) -> None:
    def guarded(handler):
        async def wrapper(request: web.Request) -> web.Response:
            supplied = request.headers.get("Authorization", "")
            if not supplied.startswith("Bearer "):
                return web.json_response({"error": "unauthorized"}, status=401)
            token = supplied[len("Bearer "):]
            user_id = await token_store.resolve(token)
            if user_id is None:
                return web.json_response({"error": "unauthorized"}, status=401)
            key = hashlib.sha256(token.encode()).hexdigest()
            if not limiter.allow(key):
                return web.json_response({"error": "rate-limited"}, status=429)
            return await handler(request, user_id)
        return wrapper

    async def resolve_guild(user_id: int):
        # Relies on discord.py's member cache, which is populated for
        # voice-connected members via the voice_states intent (core/bot.py).
        # A user not in voice is simply absent -> resolves to no session.
        for guild in bot.guilds:
            member = guild.get_member(user_id)
            voice = getattr(member, "voice", None)
            if not (member and voice and voice.channel and guild.voice_client):
                continue
            # Deactivation must cut off EVERY control path, not just j!
            # commands (commands/activation.py). Without this an already-live
            # session stays remotely controllable after the owner switches the
            # server off — the token was minted before, and nothing else here
            # re-checks. Keep scanning: the caller may be live elsewhere.
            if not await service.repo.is_activated(str(guild.id)):
                continue
            return guild
        return None

    def member_id_of(user_id: str) -> int:
        # Discord ids are strings in JSON/Firestore (TokenStore stores them
        # as strings) but ints in discord.py's caches — convert at the edge.
        return int(user_id)

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
        member = await member_of(guild, user_id)
        if member is None:
            return None, None, web.json_response(
                {"error": "not-a-member"}, status=403
            )
        if not await service.repo.is_activated(str(guild.id)):
            return None, None, web.json_response(
                {"error": "not-activated"}, status=403
            )
        return guild, member, None

    async def member_of(guild, user_id: str):
        """Resolve a guild member: cache first, REST fallback.

        The REST call is not optional. The bot runs without the privileged
        members intent (core/bot.py), so discord.py's cache holds roughly the
        bot plus whoever is connected to voice. Anyone configuring a key from
        their desk is a cache miss, and a cache-only check would silently
        report them as "not a member" — which is what made the Property
        Inspector dropdowns come back empty.
        """
        member = guild.get_member(member_id_of(user_id))
        if member is not None:
            return member
        try:
            return await guild.fetch_member(member_id_of(user_id))
        except _MEMBER_LOOKUP_ERRORS:
            return None

    async def body_of(request: web.Request) -> dict:
        try:
            return await request.json()
        except Exception:  # noqa: BLE001 — malformed body == empty body
            return {}

    def volume_of(state: dict) -> int:
        # None-based default: 0 is a legal volume (j!volume 0 mutes) and must
        # not be conflated with "unset" (web app can write volume: null).
        vol = state.get("volume")
        return 80 if vol is None else int(vol)

    async def now_playing(request: web.Request, user_id: str) -> web.Response:
        guild = await resolve_guild(member_id_of(user_id))
        if guild is None:
            return web.json_response({"active": False})
        state = await service.repo.get_state(str(guild.id)) or {}
        current = state.get("currentTrack")
        return web.json_response({
            "active": True,
            "title": current.get("title") if current else None,
            "author": current.get("artist", "") if current else "",
            "thumbnail": (current.get("thumbnail") or None) if current else None,
            "paused": bool(state.get("isPaused", False)),
            "volume": volume_of(state),
            "guildName": guild.name,
        })

    async def action_target(request: web.Request, user_id: str):
        """(guild, body, error_response) triple for POST action routes."""
        body = await body_of(request)
        guild = await resolve_guild(member_id_of(user_id))
        if guild is None:
            return None, body, web.json_response(
                {"error": "no-active-session"}, status=409
            )
        return guild, body, None

    async def play_pause(request: web.Request, user_id: str) -> web.Response:
        guild, _body, err = await action_target(request, user_id)
        if err:
            return err
        # Read-then-write toggle: two overlapping presses can collapse into
        # one. Acceptable for a single-user personal API.
        state = await service.repo.get_state(str(guild.id)) or {}
        new_paused = not state.get("isPaused", False)
        await service.pause(guild.id, new_paused)
        return web.json_response({"paused": new_paused})

    async def skip(request: web.Request, user_id: str) -> web.Response:
        guild, _body, err = await action_target(request, user_id)
        if err:
            return err
        await service.skip(guild.id)
        return web.json_response({"ok": True})

    async def stop(request: web.Request, user_id: str) -> web.Response:
        guild, _body, err = await action_target(request, user_id)
        if err:
            return err
        await service.teardown_session(guild.id, clear_queue=True)
        return web.json_response({"ok": True})

    async def shuffle(request: web.Request, user_id: str) -> web.Response:
        guild, _body, err = await action_target(request, user_id)
        if err:
            return err
        # repo.shuffle_queue, not a local reorder: the voice dispatcher and
        # j!shuffle already go through it, and a second implementation here
        # is a second thing to keep correct.
        count = await service.repo.shuffle_queue(str(guild.id))
        # An empty queue is a successful shuffle of nothing (count 0), so the
        # row is logged unconditionally — the press happened either way.
        # "shuffle" is the j! command name, so history reads like a typed one;
        # source keeps deck presses from merging into the j!shuffle row (see
        # ServerRepository._log_command's per-source dedupe).
        await service.repo.log_command(
            str(guild.id), "shuffle", "", "Stream Deck", user_id,
            source="streamdeck",
        )
        return web.json_response({"ok": True, "count": count})

    async def volume(request: web.Request, user_id: str) -> web.Response:
        guild, body, err = await action_target(request, user_id)
        if err:
            return err
        try:
            delta = int(body["delta"])
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "bad-delta"}, status=400)
        state = await service.repo.get_state(str(guild.id)) or {}
        new = await service.set_volume(guild.id, volume_of(state) + delta)
        return web.json_response({"volume": new})

    async def channels(request: web.Request, user_id: str) -> web.Response:
        # Membership via member_of (cache -> REST): the PI lists these while
        # the user is configuring a key, typically while NOT in voice, which
        # is precisely when the member cache misses.
        out = []
        for guild in bot.guilds:
            if not await service.repo.is_activated(str(guild.id)):
                continue
            if not await member_of(guild, user_id):
                continue
            out.append({
                "guildId": str(guild.id),
                "guildName": guild.name,
                "channels": [
                    {"id": str(c.id), "name": c.name}
                    for c in guild.voice_channels
                ],
            })
        return web.json_response(out)

    async def playlists(request: web.Request, user_id: str) -> web.Response:
        # Same shape and filtering as `channels`, and deliberately NOT
        # session-gated: the Property Inspector lists these while the user is
        # configuring a key, long before any session exists.
        out = []
        for guild in bot.guilds:
            if not await service.repo.is_activated(str(guild.id)):
                continue
            if not await member_of(guild, user_id):
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

    async def play_playlist(request: web.Request, user_id: str) -> web.Response:
        """Insert a saved playlist at the head of the queue and jump to it."""
        body = await body_of(request)
        guild, member, err = await guild_for_member(user_id, body.get("guildId"))
        if err:
            return err
        name = body.get("playlistName")
        if not isinstance(name, str) or not _is_valid_document_id(name):
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
        # Decide BEFORE the write: the queue write is what wakes the Firestore
        # listener, and listener.py auto-starts playback when it sees the queue
        # grow while idle. Any await between the write and the start call is a
        # window for it to fire first and pop the track we just inserted.
        was_playing = bool((await service.repo.get_state(sid) or {}).get("currentTrack"))
        # Read-modify-write of the whole queue: a concurrent web-app append
        # between the read above and this write is lost. Same single-user
        # tradeoff the play_pause toggle documents.
        await service.repo.update_state(sid, {"queue": [*queued, *existing]})
        if was_playing:
            # Reuse the TrackEnd path j!skip uses; play_next pops the new head.
            await service.skip(guild.id)
        else:
            # A skip with nothing playing is a no-op, so start explicitly.
            await service.play_next(guild.id)
        return web.json_response({"inserted": len(queued), "playlistName": name})

    async def dashboard_url(request: web.Request, user_id: str) -> web.Response:
        """Where to point a browser for the caller's current session.

        The code is read live, never cached client-side: begin_session mints a
        new one per session and teardown invalidates it.
        """
        guild = await resolve_guild(member_id_of(user_id))
        if guild is not None:
            state = await service.repo.get_state(str(guild.id)) or {}
            code = state.get("sessionCode")
            if code:
                return web.json_response({
                    "active": True,
                    "url": session_url(service.settings.web_app_url, code),
                    "guildName": guild.name,
                })
        return web.json_response({
            "active": False,
            "url": entry_url(service.settings.web_app_url),
        })

    announce_cooldown = AnnounceCooldown()
    # On the app so tests can reach the injectable clock.
    app["announce_cooldown"] = announce_cooldown

    async def announce(request: web.Request, user_id: str) -> web.Response:
        """Post one j!-style embed to the session's text channel — the deck
        equivalent of typing j!session (spec: 2026-08-11-announce-key-design).

        Response contract: 4xx is reserved for request-level faults (no
        session, unknown command, cooldown). Empty-content failures — nothing
        playing, empty queue, no session code — answer 200 {"ok": false,
        "detail": ...}, following the voice route's per-action convention, so
        the key can render the detail rather than a generic alert.
        """
        guild, body, err = await action_target(request, user_id)
        if err:
            return err
        command = body.get("command")
        # A malformed / non-JSON body parses to {} (body_of), so it lands on
        # this same 400 rather than a 500.
        if not isinstance(command, str) or command not in _ANNOUNCE_COMMANDS:
            return web.json_response({"error": "unknown-command"}, status=400)
        if not announce_cooldown.allowed(guild.id):
            return web.json_response({"error": "just-posted"}, status=429)

        sid = str(guild.id)
        state = await service.repo.get_state(sid) or {}
        if command == "session":
            code = state.get("sessionCode")
            if not code:
                return web.json_response({"ok": False, "detail": "No session code"})
            embed = session_embed(code, service.settings.web_app_url)
        elif command == "nowplaying":
            current = state.get("currentTrack")
            if not current:
                return web.json_response(
                    {"ok": False, "detail": "Nothing is playing"}
                )
            embed = now_playing_embed(current)
        elif command == "queue":
            queue = state.get("queue") or []
            # Checked BEFORE building: queue_embed happily renders "Queue is
            # empty.", but the j! command answers a person in the channel
            # while the key answers a person at the deck — an empty queue
            # fails on the key and posts nothing.
            if not queue:
                return web.json_response({"ok": False, "detail": "Queue is empty"})
            embed = queue_embed(queue, state.get("currentTrack"), page=0)
        else:  # status — always has content, always posts.
            cog = bot.get_cog("Status")
            node = getattr(bot, "node", None)
            embed = build_status_embed(
                # started_at lives on the cog; without it the uptime line is
                # omitted rather than failing the post — a missing cosmetic
                # line must not break a health report.
                uptime_s=(
                    time.monotonic() - cog.started_at if cog is not None else None
                ),
                gateway_ms=int(bot.latency * 1000),
                guild_count=len(bot.guilds),
                node_connected=bool(node and node.connected),
                node_session_id=node.session_id if node else None,
                state=state,
                position=service.positions.get(guild.id) or {},
                guardian=await fetch_guardian_status(bot),
            )

        if not await service.notifier.send(guild.id, embed=embed):
            # The channel never received it; saying "posted" would be a lie —
            # and neither the history row nor the cooldown stamp happens.
            return web.json_response(
                {"ok": False, "detail": "Could not post to Discord"}
            )
        # One row under the j! name, exactly the convention the shuffle route
        # established: history renders the row like a typed command, and the
        # streamdeck source keeps deck presses out of the typed rows' dedupe.
        await service.repo.log_command(
            sid, command, "", "Stream Deck", user_id, source="streamdeck"
        )
        # Stamped only on SUCCESS (mirrors VoiceIntentDispatcher._announce):
        # a failed send must not burn the next 10 s of legitimate posts.
        announce_cooldown.stamp(guild.id)
        return web.json_response({"ok": True, "command": command})

    async def post_debug(guild_id: int, message: str) -> None:
        """Echo one press to the session's text channel. Best-effort.

        Deliberately NOT routed through VoiceIntentDispatcher._announce: that
        applies a 10 s per-guild cooldown, which exists to stop a
        misrecognition spamming an embed. A debug echo is explicitly requested
        per press, so silently dropping it is the one failure it cannot have.

        INVARIANT: nothing on this path logs the message or the transcript.
        The echo publishes transcribed speech to Discord by explicit per-key
        opt-in; container stdout has different retention and readers, and the
        transcript must never reach it.
        """
        try:
            await service.notifier.send(guild_id, text=message)
        except Exception:  # noqa: BLE001 — the actions already ran
            log.warning("voice debug echo failed to post")

    async def voice(request: web.Request, user_id: str) -> web.Response:
        """Transcribe a push-to-talk clip and run the recognized command."""
        if transcriber is None or voice_dispatcher is None:
            return web.json_response({"error": "voice-disabled"}, status=503)
        guild = await resolve_guild(member_id_of(user_id))
        if guild is None:
            # Before transcription: never pay for a request that cannot succeed.
            return web.json_response({"error": "no-active-session"}, status=409)
        audio = await request.read()
        if len(audio) > VOICE_MAX_BYTES:
            return web.json_response({"error": "too-large"}, status=413)
        # A zero-byte upload cannot contain speech; don't pay OpenAI to say so.
        #
        # Its OWN code, not the 422 the unrecognised case uses. Sharing one was
        # the second half of a reproduced bug: the plugin's microphone capture
        # was spawning ffmpeg against a device that cannot exist, uploading
        # nothing, and the key rendered "Didn't catch that" — blaming the
        # user's speech for a request that never reached transcription and had
        # no debug echo to explain itself. 400 rather than 422: an empty body
        # is a malformed request, not content the server understood and could
        # not act on.
        if not audio:
            return web.json_response({"error": "no-audio"}, status=400)

        # An unknown or absent code degrades to English rather than erroring:
        # a stale key setting must not break the key.
        language = normalize_language(request.query.get("language"))
        # Opt-in, per press, off by default — see _DEBUG_TRUTHY.
        debug = _wants_debug(request.query.get("debug"))
        try:
            transcript = await transcriber.transcribe(audio, language)
        except Exception:  # noqa: BLE001 — any STT fault is one failure mode
            log.exception("voice transcription failed")
            return web.json_response({"error": "stt-failed"}, status=502)

        # STRUCTURE FIRST. What the grammar understood is final: no LLM call,
        # no latency, no cost, and no chance of a model overriding a command
        # that was already unambiguous.
        parsed = parse_structured(transcript)
        if parsed.resolved:
            actions = parsed.actions
            # Verbs only — the closed vocabulary is safe to log, the
            # transcript is not (see the INVARIANT below).
            log.info(
                "voice resolved by grammar (%s); interpreter not called",
                ",".join(a.action for a in actions),
            )
        else:
            try:
                actions = await interpreter.interpret(
                    transcript, keywords=parsed.keywords
                ) if interpreter else []
            except Exception as exc:  # noqa: BLE001 — degrade to NO actions
                # Type AND message: a fixed string made "OpenAI rejected the
                # key, so the feature has silently degraded forever" look
                # identical to a transient network partition, while the key
                # still appeared to work. Transcript-safe by construction —
                # LlmIntentInterpreter's messages carry a status, an aiohttp
                # transport error (host/URL, never the request body), or a
                # fixed string; test_interpreter_errors_never_carry_the_
                # transcript pins that for every one of its failure modes. No
                # exc_info: a traceback is not needed here and this reaches
                # container stdout, where the transcript must never appear.
                #
                # There is nothing to fall back TO: the grammar already ran
                # and declined. An outage therefore costs the reasoning
                # layer, not the closed vocabulary.
                log.warning(
                    "voice interpretation failed (%s: %s); nothing will run",
                    type(exc).__name__, exc,
                )
                actions = []
        # Applied to BOTH paths for defence in depth. The grammar is already
        # self-consistent, so this is a no-op for everything it resolves —
        # except "add my playlist chill", which it reads as a search because
        # only the literal "add playlist X" form is in its table. Running the
        # rule uniformly makes "the word 'playlist' never reaches a YouTube
        # search" a property of the ROUTE rather than of whichever component
        # happened to produce the actions.
        # Same `language` that was sent to the transcriber, so the play-verb
        # check reads the transcript in the language it was produced in.
        actions = enforce_intent(actions, transcript, language)
        # Defence in depth: LlmIntentInterpreter runs validate_actions, which
        # already truncates, but `interpreter` is an injected Any and the cap
        # is this route's own blast-radius bound — one dispatch and one history
        # row per action. It must not depend on a collaborator to enforce it.
        actions = actions[:MAX_ACTIONS]
        # The same fact the INFO log above reports, as a VALUE the debug echo
        # can carry: which layer produced what is about to run. Read after the
        # enforcement passes, so an action list emptied there reads "nothing"
        # rather than claiming a layer resolved something that no longer exists.
        resolved_by = (
            "nothing" if not actions
            else "grammar" if parsed.resolved
            else "reasoning"
        )
        if not actions:
            # The echo posts BEFORE the early return: "I heard X and resolved
            # nothing" is the most useful thing this instrument says, and it is
            # exactly the case someone turns it on to diagnose.
            if debug:
                await post_debug(
                    guild.id, build_debug_message(transcript, resolved_by, [])
                )
            # "Didn't catch that": neither layer recognized a command, so
            # NOTHING runs — there is no longer any path from an unrecognised
            # utterance to a search. Distinct from the empty upload above,
            # which answers 400/"no-audio": this one really did hear something
            # and really did fail to resolve it, and only this one has a
            # transcript to echo.
            return web.json_response({"error": "no-speech"}, status=422)

        try:
            results = await voice_dispatcher.dispatch_all(guild.id, actions)
        except Exception:  # noqa: BLE001 — a failed command must not 500
            # INVARIANT: nothing raised out of dispatch_all may carry the
            # transcript. log.exception emits a traceback, so an exception
            # message holding the spoken text would reach container stdout —
            # which has different retention and readers than the command
            # history transcripts are deliberately persisted to. dispatch_all
            # already contains per-action failures and logs the verb only,
            # so this is a last-resort guard; keep it transcript-free when
            # adding actions.
            log.exception("voice dispatch failed")
            return web.json_response({"error": "dispatch-failed"}, status=502)

        for action, result in zip(actions, results, strict=False):
            # Logged as the EXECUTED action so the dashboard's retrigger
            # works, with the transcript alongside. One row per action, all
            # sharing the utterance that produced them. log_arg wins where the
            # executed value differs from what was said — a volume row logs
            # the resulting level, so up/down stay distinct rows and
            # listener.py's `command == "volume" and args` retrigger fires.
            log_args = result.log_arg if result.log_arg is not None else (
                action.query or action.name
            )
            await service.repo.log_command(
                str(guild.id), _LOG_COMMAND_FOR.get(action.action, action.action),
                log_args, "Voice", user_id,
                source="voice", transcript=transcript,
            )
        if debug:
            await post_debug(guild.id, build_debug_message(
                transcript, resolved_by,
                list(zip(actions, (r.detail for r in results), strict=False)),
            ))
        done = sum(1 for r in results if r.ok)
        return web.json_response({
            "transcript": transcript,
            "actions": [
                {"action": a.action, "ok": r.ok, "detail": r.detail}
                for a, r in zip(actions, results, strict=False)
            ],
            "ok": done == len(results),
            "detail": results[0].detail if len(results) == 1
                      else f"{done} of {len(results)} done",
            # Directives the SERVER cannot perform — currently only opening a
            # browser, which lives on the user's machine. Filtered, so a
            # response with no directives carries an empty list rather than a
            # list of nulls the plugin would have to skip.
            "client": [r.client for r in results if r.client is not None],
        })

    async def summon(request: web.Request, user_id: str) -> web.Response:
        """Toggle: join the requested voice channel, or leave it if the bot
        is already there (queue preserved, current track requeued)."""
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

        voice = guild.voice_client
        if voice is not None:
            if getattr(getattr(voice, "channel", None), "id", None) == channel_id:
                await service.teardown_session(guild.id, requeue_current=True)
                return web.json_response({"action": "left"})
            return web.json_response({"error": "active-elsewhere"}, status=409)

        channel = guild.get_channel(channel_id)
        if channel is None or not hasattr(channel, "connect"):
            return web.json_response({"error": "bad-channel"}, status=400)
        try:
            from jacky.audio.voice import LavalinkVoiceClient

            await channel.connect(cls=LavalinkVoiceClient)
            code = await service.begin_session(guild, channel)
        except Exception:  # noqa: BLE001 — any join failure surfaces as 502
            log.exception(
                "summon join failed (guild %s, channel %s)", guild.id, channel_id
            )
            return web.json_response({"error": "join-failed"}, status=502)
        return web.json_response({"action": "joined", "sessionCode": code})

    app.add_routes([
        web.get("/control/now-playing", guarded(now_playing)),
        web.post("/control/play-pause", guarded(play_pause)),
        web.post("/control/skip", guarded(skip)),
        web.post("/control/stop", guarded(stop)),
        web.post("/control/shuffle", guarded(shuffle)),
        web.post("/control/volume", guarded(volume)),
        web.get("/control/channels", guarded(channels)),
        web.get("/control/playlists", guarded(playlists)),
        web.post("/control/playlist", guarded(play_playlist)),
        web.get("/control/dashboard-url", guarded(dashboard_url)),
        web.post("/control/summon", guarded(summon)),
        web.post("/control/voice", guarded(voice)),
        web.post("/control/announce", guarded(announce)),
    ])
    log.info("control API routes registered")
