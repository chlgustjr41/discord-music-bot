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
from typing import Any

import discord
from aiohttp import web

from jacky.api.dashboard_link import entry_url, session_url
from jacky.api.voice_actions import MAX_ACTIONS
from jacky.api.voice_intent import parse_fallback

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
        if not audio:
            return web.json_response({"error": "no-speech"}, status=422)

        try:
            transcript = await transcriber.transcribe(audio)
        except Exception:  # noqa: BLE001 — any STT fault is one failure mode
            log.exception("voice transcription failed")
            return web.json_response({"error": "stt-failed"}, status=502)

        try:
            actions = await interpreter.interpret(transcript) if interpreter else []
        except Exception as exc:  # noqa: BLE001 — degrade to the offline parser
            # Type AND message: a fixed string made "OpenAI rejected the key,
            # so the feature has silently degraded to the fallback forever"
            # look identical to a transient network partition, while the key
            # still appeared to work. Transcript-safe by construction —
            # LlmIntentInterpreter's messages carry a status, an aiohttp
            # transport error (host/URL, never the request body), or a fixed
            # string; test_interpreter_errors_never_carry_the_transcript pins
            # that for every one of its failure modes. No exc_info: a
            # traceback is not needed here and this reaches container stdout,
            # where the transcript must never appear (see the INVARIANT below).
            log.warning(
                "voice interpretation failed (%s: %s); using the fallback parser",
                type(exc).__name__, exc,
            )
            actions = []
        if not actions:
            actions = parse_fallback(transcript)
        # Defence in depth: LlmIntentInterpreter runs validate_actions, which
        # already truncates, but `interpreter` is an injected Any and the cap
        # is this route's own blast-radius bound — one dispatch and one history
        # row per action. It must not depend on a collaborator to enforce it.
        actions = actions[:MAX_ACTIONS]
        if not actions:
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
    ])
    log.info("control API routes registered")
