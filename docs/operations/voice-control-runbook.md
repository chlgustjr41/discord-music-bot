# Voice control runbook ("Jacky Ears")

Operations guide for the optional wake-word voice-control companion. The
feature is **opt-in and revertible**: the base music stack (`lavalink`, `bot`,
`guardian`, `cipher`, `pot-provider`, `token-minter`) boots and runs exactly the
same whether or not voice control is enabled.

## What it is

`services/voice-listener/` ("Jacky Ears") is a second Discord application that
joins the voice channel alongside the music bot, listens for a wake word
("hey jacky"), transcribes the following phrase locally with Vosk, and POSTs the
recognized intent to the bot's `/voice-intent` endpoint. The bot maps the intent
to the same actions as `j!` commands (skip, pause, resume, volume, stop, play).

Two independent switches must both be on for anything to happen:

- **`COMPOSE_PROFILES=voice`** — starts the `voice-listener` container. The
  compose service is behind `profiles: ["voice"]`, so it does not exist in the
  default stack at all.
- **`VOICE_CONTROL_ENABLED=true`** — tells the bot to construct its voice
  dispatcher and accept intents. Without it the bot ignores the listener.

## One-time setup: the "Jacky Ears" Discord application

The listener needs its **own** bot token, separate from the music bot.

1. Discord Developer Portal → **New Application** → name it e.g. "Jacky Ears".
2. **Bot** tab → **Add Bot** → **Reset Token** → copy it into
   `DISCORD_EARS_TOKEN` in `deploy/.env`.
3. **Bot** tab → **Privileged Gateway Intents** → enable **Server Members** is
   not required, but enable **Voice states** access (the listener tracks who is
   in the voice channel). Message Content is not needed.
4. **OAuth2 → URL Generator** → scopes `bot`; bot permissions **Connect** and
   **Speak** (it plays short earcon chimes) plus **View Channels**. Open the
   generated URL and invite Jacky Ears to each guild where you want voice
   control. It must be in the same voice channel as the music bot.
5. Generate the shared secret used for the bot ↔ listener HTTP on the internal
   docker network: `openssl rand -hex 16` → set the **same** value as
   `VOICE_INTERNAL_TOKEN` in `deploy/.env` (both services read it).

## Enable

In `deploy/.env`:

```bash
VOICE_CONTROL_ENABLED=true
DISCORD_EARS_TOKEN=<jacky-ears-bot-token>
VOICE_INTERNAL_TOKEN=<openssl rand -hex 16>
```

Then start the stack with the voice profile:

```bash
COMPOSE_PROFILES=voice make up
# equivalently: COMPOSE_PROFILES=voice docker compose \
#   -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

Verify: `docker compose ps` shows `voice-listener` `Up (healthy)`; the bot log
notes voice control active; `j!wake` reports the listener online.

> Note: `make up` / `make deploy` do **not** set the profile. To keep voice
> control running across deploys, export `COMPOSE_PROFILES=voice` in the shell
> or the VM environment, or prefix each stack command with it.

## Change the wake word at runtime

`j!wake "okay jacky"` updates the wake phrase live (persisted in Firestore, read
by the listener) — no restart needed. Invalid/empty phrases are rejected. If the
listener container is down, `j!wake` reports it offline.

## Disable (make it dormant)

Unset both switches and bring the stack up without the profile:

```bash
# in deploy/.env
VOICE_CONTROL_ENABLED=false
```
```bash
make up            # no COMPOSE_PROFILES=voice → listener not started
docker compose -f deploy/docker-compose.yml --env-file deploy/.env \
  down voice-listener 2>/dev/null || true   # stop it if it was running
```

The bot goes dormant (never constructs its dispatcher) and the listener
container is not part of the stack. The rest of the stack is unaffected.

## Full removal (revert the feature)

The whole feature landed on branch `feat/voice-control`. To remove it entirely
after it is merged, revert the merge commit:

```bash
git revert -m 1 <merge-commit-sha>
```

That drops the listener service, the bot-side dispatcher, and the three env
vars. Nothing in the base stack depends on any of them.

## Privacy note

Transcription is **fully local** (Vosk small English model baked into the
listener image). Audio is processed in memory frame-by-frame and never written
to disk; only the recognized text intent (e.g. `skip`, `play <query>`) crosses
to the bot, and nothing — audio or transcript — is persisted anywhere. There is
no cloud speech API and no recording.

## Troubleshooting

- **Listener won't start / crash-loops:** almost always an empty or mismatched
  token. `DISCORD_EARS_TOKEN` must be the Jacky Ears bot token;
  `VOICE_INTERNAL_TOKEN` must be identical on the bot and listener. The listener
  is crash-only: it exits fast on a bad/empty token (visible in
  `docker compose logs voice-listener`). This never affects the music stack.
  If the token is empty on the **bot** side (`VOICE_CONTROL_ENABLED=true` but no
  `VOICE_INTERNAL_TOKEN`), the bot logs an error and runs with voice control
  **disabled** rather than exposing an unauthenticated `/voice-intent` endpoint —
  grep the bot log for "disabling voice control".
- **Wake word not triggering:** confirm Jacky Ears is in the same voice channel;
  check the listener log for recognized partials.
- **`j!wake` says offline:** the container is down or the bot can't reach
  `VOICE_LISTENER_URL` (`http://voice-listener:8090`) — check both are on the
  `jacky` network and the profile is active.
- **Kill switch:** stopping/removing `voice-listener` (or unsetting the profile)
  leaves music playback completely unaffected.

## Soak checklist (test guild, before merging to master)

- [ ] j!start in voice, say "hey jacky" → ack chime within ~1s
- [ ] "hey jacky … skip" → confirm blip, track skips
- [ ] pause / resume / volume up / volume down / stop each work
- [ ] "hey jacky … play <song name>" queues a plausible track
- [ ] gibberish after wake → error buzz, music continues
- [ ] j!wake "okay jacky" → takes effect without restart; j!wake bad word rejected
- [ ] kill voice-listener container → music unaffected; j!wake reports offline
- [ ] docker stats: listener RSS < 400m, lavalink/bot steady
