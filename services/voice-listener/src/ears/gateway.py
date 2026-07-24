"""Jacky Ears Discord client: join/leave voice, receive audio, play earcons.

Audio path (voice_recv callback thread -> asyncio):
  AudioSink.write(user, data) -> Downsampler -> silence gate -> SpeakerEngine
  engine events -> loop.call_soon_threadsafe -> earcon + ship_intent
"""

import asyncio
import logging
from pathlib import Path

import discord
from discord.ext import voice_recv

from ears.api import ship_intent
from ears.config import Settings
from ears.engine import SpeakerEngine, VoskRecognizer
from ears.phrases import build_active_grammar, build_passive_grammar
from ears.pipeline import Downsampler, is_silence

log = logging.getLogger("ears.gateway")
ASSETS = Path(__file__).resolve().parent.parent.parent / "assets"


class EarsSink(voice_recv.AudioSink):
    """Fan out per-speaker PCM into engines. Runs on voice-recv's thread."""

    def __init__(self, client: "EarsClient", guild_id: str, wake_phrase: str):
        super().__init__()
        self.client, self.guild_id, self.wake_phrase = client, guild_id, wake_phrase
        self.engines: dict[int, tuple[Downsampler, SpeakerEngine]] = {}

    def wants_opus(self) -> bool:
        return False                      # receive decoded 48k stereo PCM

    def write(self, user, data: voice_recv.VoiceData) -> None:
        if user is None or user.bot:
            return
        if is_silence(data.pcm):
            return
        # NB: not setdefault(user.id, self._new_engine()) — that eagerly builds
        # (and discards) two Vosk recognizers on EVERY frame (~50/s/speaker).
        pair = self.engines.get(user.id)
        if pair is None:
            pair = self.engines[user.id] = self._new_engine()
        ds, eng = pair
        event = eng.feed(ds.feed(data.pcm))
        if event:
            self.client.dispatch_event(self.guild_id, event)

    def _new_engine(self) -> tuple[Downsampler, SpeakerEngine]:
        model = self.client.model
        return Downsampler(), SpeakerEngine(
            passive=VoskRecognizer(model, build_passive_grammar(self.wake_phrase)),
            active=VoskRecognizer(model, build_active_grammar()),
            wake_phrase=self.wake_phrase,
            active_window_seconds=self.client.settings.active_window_seconds,
        )

    def cleanup(self) -> None:
        self.engines.clear()


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
