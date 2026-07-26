# Voice Control ("Hey Jacky") — Design

**Date:** 2026-07-18
**Status:** Approved (brainstorm 2026-07-18)
**Branch:** `feat/voice-control` — merged via a single `--no-ff` merge commit so the
entire feature is removable with `git revert -m 1 <merge>`.

## Problem

Users in a voice session should be able to control playback by speaking a wake
phrase ("Hey Jacky!") followed by a command, and hear short musical tones as
feedback. A previous attempt failed because the bot could not receive audio:
Discord allows one voice connection per bot per guild, and Jacky's is delegated
to Lavalink — the Python process never sees audio packets.

## Decision summary

| Question | Decision |
|---|---|
| Audio receive | Companion listener service with its own bot token ("Jacky Ears") |
| STT / wake word | Local Vosk small English model, dual grammar (no cloud, no per-request cost) |
| Feedback | Earcons only (listening chime / confirm blip / error buzz), mixed over music |
| Commands (v1) | skip, pause, resume, volume up/down, stop, `play <free-form title>` |
| Language | English only |
| Wake phrase | Per-guild, host-set via `j!wake`, stored in Firestore, default `hey jacky` |
| Revertibility | One merge commit + compose profile `voice` + `VOICE_CONTROL_ENABLED` flag |

## Architecture

New service `services/voice-listener/` (Python, mirrors existing service layout).
It is a second Discord bot user that joins the session's voice channel.

```
Main bot                          voice-listener
────────                          ──────────────
session joins voice ────POST────▶ /session {guild, channel, wake_phrase, action}
                                  join channel (discord.py + discord-ext-voice-recv)
                                  per-speaker Opus → 16 kHz mono PCM → VAD gate
                                  PASSIVE: Vosk grammar = wake-phrase words only
                                  on wake → ack tone → ACTIVE (5 s window)
                                  ACTIVE: grammar = commands + free dictation tail
recognized intent ◀────POST────── /voice-intent {guild, intent, arg}
  └▶ playback service ─▶ Lavalink
                                  confirm tone / error buzz
```

- **One Vosk model, two grammars.** Passive mode constrains recognition to the
  wake-phrase words (cheap, accurate); active mode swaps in the command grammar.
  Grammar swapping is what makes user-adjustable wake phrases possible — no
  per-phrase model training.
- **Earcons never interrupt music.** The listener plays bundled OGG assets over
  its own voice connection; Discord mixes speakers in a channel.
- **Intents map 1:1** to existing playback-service calls. `play <text>` feeds
  the existing search path; YouTube search tolerates rough transcripts.
- **Transport:** internal compose network HTTP with a shared-secret header
  (`VOICE_INTERNAL_TOKEN`). Bot → listener on `voice-listener:8090`; listener →
  bot on the existing aiohttp server (`bot:8080`).

## Components

| Unit | Purpose | Depends on |
|---|---|---|
| `listener/gateway.py` | Discord client, channel join/leave, earcon playback | discord.py, voice-recv |
| `listener/pipeline.py` | Opus→PCM resample, VAD, per-speaker stream fan-out | — |
| `listener/engine.py` | Vosk wrapper: passive/active states, grammar builder | vosk |
| `listener/intents.py` | transcript → intent parsing (pure functions) | — |
| `listener/api.py` | `/session`, `/health` endpoints; posts `/voice-intent` | aiohttp |
| Bot: `commands/wake.py` | `j!wake <phrase>` — validate against Vosk vocab, store in Firestore, push to listener | existing config repo |
| Bot: `core/health.py` (+) | `/voice-intent` endpoint → playback service | existing |

## Wake phrase rules

2–4 English words, every word must exist in the Vosk model vocabulary
(validated at `j!wake` time with immediate feedback). Session host only.
Stored per guild in Firestore (single source of truth); pushed to the listener
on session start and on change.

## Error handling

- Listener crash: separate process, `restart: unless-stopped`; music unaffected.
- Bot unreachable from listener: intent dropped, error buzz.
- Unrecognized speech in active window: error buzz, return to passive.
- Concurrent speakers: per-speaker streams; first wake wins the active window.
- Privacy: audio is transcribed in-memory and discarded; never persisted.

## Resources

Container capped at `mem_limit: 400m`, `cpus: "0.5"` (Vosk small-en ≈ 90 MB +
runtime). VAD gates STT so silent channels cost ~nothing. Worst case the
listener OOMs alone; the music stack is untouched.

## Kill-switch & revert

1. **Runtime:** service under compose `profiles: ["voice"]`; started only when
   `VOICE_CONTROL_ENABLED=true`. Bot's endpoint and `j!wake` go dormant when
   the flag is off.
2. **Git:** whole feature in `feat/voice-control`, merged with one `--no-ff`
   commit; `git revert -m 1` removes it. Blast radius: one new directory, a
   compose block, `.env.example` lines, ~50 lines in the bot.

## Testing

- **Unit:** `intents.py` parser, wake-phrase validator, grammar builder.
- **Integration:** recorded PCM fixtures through the real Vosk pipeline
  (wake detection + each command intent).
- **CI:** service added to `make test` / `make lint`; Integration workflow gets
  a boot + `/health` smoke step (no Discord egress needed).
- **Manual soak** in the test guild before merge to master.

## Implementation strategy

Hierarchical multi-agent execution (token-efficient: each subagent receives only
its slice of context; orchestrator reviews between steps):

1. Service scaffold + compose/profile/CI wiring
2. Audio receive pipeline (gateway + pipeline)
3. STT engine + intent parser + unit/fixture tests
4. Bot side: `/voice-intent` endpoint, `j!wake`, Firestore config
5. Integration, earcons, docs, soak checklist

Detailed plan: see the companion implementation plan produced by writing-plans.

## Non-goals (v1)

- TTS spoken replies; Korean or multilingual recognition; voice-driven session
  activation (bot must already be in a session); guardian playbook integration
  beyond the `/health` endpoint.
