"""Jacky Ears Discord client: join/leave voice, receive audio, play earcons.

Audio path (voice_recv callback thread -> asyncio):
  AudioSink.write(user, data) -> opus decode -> Downsampler -> silence gate
  -> SpeakerEngine -> loop.call_soon_threadsafe -> earcon + ship_intent

We set wants_opus() = True and decode packets OURSELVES: voice_recv's own
decoder runs inside the packet-router loop with no per-packet error handling,
so a single "corrupted stream" OpusError there kills ALL listening (prod
outage 2026-07-26). Decoding in write() lets us skip a bad packet instead.
"""

import asyncio
import logging
from pathlib import Path

import discord
from discord.ext import voice_recv
from discord.opus import Decoder, OpusError

from ears.api import ship_intent
from ears.config import Settings
from ears.engine import SpeakerEngine, VoskRecognizer
from ears.phrases import build_active_grammar, build_passive_grammar
from ears.pipeline import Downsampler, is_silence

log = logging.getLogger("ears.gateway")
ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"


class EarsSink(voice_recv.AudioSink):
    """Fan out per-speaker PCM into engines. Runs on voice-recv's thread."""

    def __init__(self, ears: "EarsClient", guild_id: str, wake_phrase: str):
        super().__init__()
        # NB: attribute is `ears`, NOT `client` — voice_recv.AudioSink defines
        # `client` as a read-only property; assigning self.client raises
        # AttributeError and silently breaks sink attachment (prod outage 07-26).
        self.ears, self.guild_id, self.wake_phrase = ears, guild_id, wake_phrase
        # Per-speaker (opus decoder, downsampler, engine) — all stateful across
        # frames, so built once per user and reused.
        self.streams: dict[int, tuple[Decoder, Downsampler, SpeakerEngine]] = {}

    def wants_opus(self) -> bool:
        return True     # decode ourselves; see module docstring (crash-safety)

    def write(self, user, data: voice_recv.VoiceData) -> None:
        # Skip bots BEFORE decoding: the music bot's Lavalink stream is both
        # irrelevant (we only act on human speech) and the likely source of the
        # "corrupted stream" packets — not decoding it avoids the error entirely
        # and saves the CPU of decoding+STT on the music.
        if user is None or user.bot:
            return
        opus = data.opus
        if not opus:
            return
        dec, ds, eng = self._stream_for(user.id)
        try:
            pcm = dec.decode(opus, fec=False)
        except OpusError:
            return          # skip this packet; a bad decode must never be fatal
        if is_silence(pcm):
            return
        event = eng.feed(ds.feed(pcm))
        if event:
            self.ears.dispatch_event(self.guild_id, event)

    def _stream_for(self, user_id: int) -> tuple[Decoder, Downsampler, SpeakerEngine]:
        st = self.streams.get(user_id)
        if st is None:
            model = self.ears.model
            st = self.streams[user_id] = (
                Decoder(),
                Downsampler(),
                SpeakerEngine(
                    passive=VoskRecognizer(model, build_passive_grammar(self.wake_phrase)),
                    active=VoskRecognizer(model, build_active_grammar()),
                    wake_phrase=self.wake_phrase,
                    active_window_seconds=self.ears.settings.active_window_seconds,
                ),
            )
        return st

    def cleanup(self) -> None:
        # AudioSink.__del__ calls this; guard so a partially-constructed sink
        # never raises during garbage collection.
        getattr(self, "streams", {}).clear()


class EarsClient(discord.Client):
    def __init__(self, settings: Settings):
        super().__init__(intents=discord.Intents(guilds=True, voice_states=True))
        self.settings = settings
        self.model = None                 # vosk.Model, loaded in setup_hook
        self.http_session = None          # aiohttp.ClientSession
        self._vocab: set[str] = set()

    async def setup_hook(self) -> None:
        import aiohttp
        from vosk import Model
        self.model = await asyncio.to_thread(Model, self.settings.model_path)
        words = Path(self.settings.model_path, "graph", "words.txt")
        if words.exists():
            self._vocab = {ln.split()[0] for ln in words.read_text().splitlines() if ln}
        self.http_session = aiohttp.ClientSession()

    def knows_word(self, word: str) -> bool:
        return not self._vocab or word in self._vocab

    async def join(self, guild_id: str, channel_id: str, wake_phrase: str) -> None:
        await self.leave(guild_id)
        channel = self.get_channel(int(channel_id))
        if channel is None:
            log.warning("join ignored: channel %s not visible", channel_id)
            return
        vc = await channel.connect(cls=voice_recv.VoiceRecvClient)
        vc.listen(EarsSink(self, guild_id, wake_phrase))
        log.info("listening in guild %s channel %s (wake=%r)",
                 guild_id, channel_id, wake_phrase)

    async def leave(self, guild_id: str) -> None:
        guild = self.get_guild(int(guild_id))
        if guild and guild.voice_client:
            await guild.voice_client.disconnect(force=True)

    # -- engine events (called from sink thread) ------------------------------
    def dispatch_event(self, guild_id: str, event) -> None:
        self.loop.call_soon_threadsafe(
            lambda: asyncio.ensure_future(self._handle_event(guild_id, event))
        )

    async def _handle_event(self, guild_id: str, event) -> None:
        kind, intent = event
        if kind == "wake":
            self._play_earcon(guild_id, "ack.wav")
        elif kind == "error":
            self._play_earcon(guild_id, "error.wav")
        elif kind == "intent":
            ok = await ship_intent(self.http_session, self.settings.bot_intent_url,
                                   self.settings.internal_token, guild_id, intent)
            self._play_earcon(guild_id, "confirm.wav" if ok else "error.wav")

    def _play_earcon(self, guild_id: str, name: str) -> None:
        guild = self.get_guild(int(guild_id))
        vc = guild.voice_client if guild else None
        if vc and not vc.is_playing():
            vc.play(discord.FFmpegPCMAudio(str(ASSETS / name)))
