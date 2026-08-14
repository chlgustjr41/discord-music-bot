"""One shared posting path for "announce a j!-style embed to the channel".

Both the Stream Deck's Post to Discord key (POST /control/announce) and the
voice inquiries (session_info / now_playing / queue_info / status_info) post
through this class, so the pinned invariants live in exactly one place
(spec: 2026-08-14-voice-announce-unification-design):

- content checks come BEFORE the cooldown check — a call that had nothing to
  post is told why, never blamed on a window it did not even try to use;
- the cooldown is stamped ONLY on a successful post — a failed send must not
  burn the next 10 s of legitimate posts;
- ONE per-guild window is shared across every command and every caller. Two
  features posting into one channel share one spam bound.

The Announcer does NOT log command history. Each caller keeps its own
attribution convention — the announce endpoint logs a `streamdeck` row, the
voice route logs a `voice` row carrying the transcript. One posting path,
two attribution conventions; logging here would double the rows.

Destination resolution (stored text channel, else the bot's voice-channel
chat) is the notifier's job, not this class's.
"""

import time
from dataclasses import dataclass
from typing import Any

from jacky.commands.embeds import now_playing_embed, queue_embed, session_embed
from jacky.commands.status import build_status_embed, fetch_guardian_status

# How long "just posted" lasts. One product decision AND — since the windows
# merged — one piece of state, so this is the single definition.
ANNOUNCE_COOLDOWN_S = 10.0


@dataclass(frozen=True)
class AnnounceOutcome:
    """What a post attempt came to.

    `cooldown` is its own flag rather than a detail-string convention: the
    announce route maps it to 429 {"error": "just-posted"} while the voice
    key renders the human sentence, and a route that string-matched the
    sentence to pick a status code would be exactly the coupling this
    extraction exists to remove.
    """

    ok: bool
    detail: str
    cooldown: bool = False


class Announcer:
    """command -> content check -> embed -> cooldown -> notifier post."""

    def __init__(self, service: Any, bot: Any) -> None:
        self.service, self.bot = service, bot
        # Injectable clock: tests advance time rather than sleep. monotonic,
        # not wall clock, so a system time change cannot wedge the cooldown.
        self.now = time.monotonic
        self._last: dict[int, float] = {}

    def _allowed(self, guild_id: int) -> bool:
        last = self._last.get(guild_id)
        return last is None or (self.now() - last) >= ANNOUNCE_COOLDOWN_S

    async def post(self, guild_id: int, command: str) -> AnnounceOutcome:
        """Post one j!-style embed to the session's channel.

        `detail` on success is what the caller shows/logs (the track title,
        the session code); on failure it is the specific reason.
        """
        sid = str(guild_id)
        state = await self.service.repo.get_state(sid) or {}
        if command == "session":
            code = state.get("sessionCode")
            if not code:
                return AnnounceOutcome(False, "No session code")
            embed = session_embed(code, self.service.settings.web_app_url)
            detail = code
        elif command == "nowplaying":
            current = state.get("currentTrack")
            if not current:
                return AnnounceOutcome(False, "Nothing is playing")
            embed = now_playing_embed(current)
            detail = current.get("title", "Now playing")
        elif command == "queue":
            queue = state.get("queue") or []
            # Checked BEFORE building: queue_embed happily renders "Queue is
            # empty.", but the j! command answers a person in the channel
            # while an announce answers a person at the deck or microphone —
            # an empty queue fails on the caller and posts nothing.
            if not queue:
                return AnnounceOutcome(False, "Queue is empty")
            embed = queue_embed(queue, state.get("currentTrack"), page=0)
            detail = f"Queue ({len(queue)})"
        elif command == "status":
            # Always has content, always posts.
            cog = self.bot.get_cog("Status")
            node = getattr(self.bot, "node", None)
            embed = build_status_embed(
                # started_at lives on the cog; without it the uptime line is
                # omitted rather than failing the post — a missing cosmetic
                # line must not break a health report.
                uptime_s=(
                    time.monotonic() - cog.started_at if cog is not None else None
                ),
                gateway_ms=int(self.bot.latency * 1000),
                guild_count=len(self.bot.guilds),
                node_connected=bool(node and node.connected),
                node_session_id=node.session_id if node else None,
                state=state,
                position=self.service.positions.get(guild_id) or {},
                guardian=await fetch_guardian_status(self.bot),
            )
            detail = "Status posted"
        else:
            # Callers allowlist before calling; this is the last line, and an
            # unknown command must be inert, never an exception.
            return AnnounceOutcome(False, "Unknown command")

        # AFTER the content checks — see the module docstring.
        if not self._allowed(guild_id):
            return AnnounceOutcome(
                False, "Just posted — try again shortly", cooldown=True
            )
        if not await self.service.notifier.send(guild_id, embed=embed):
            # The channel never received it; saying "posted" would be a lie —
            # and the cooldown stamp must not happen either.
            return AnnounceOutcome(False, "Could not post to Discord")
        # Stamped only on SUCCESS.
        self._last[guild_id] = self.now()
        return AnnounceOutcome(True, detail)
