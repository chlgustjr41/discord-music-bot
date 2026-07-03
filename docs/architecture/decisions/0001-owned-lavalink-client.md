# ADR-0001: Own the Lavalink client instead of using wavelink

**Status:** Accepted · 2026-07-02

## Context
wavelink required monkey-patching for Discord DAVE voice encryption, and its
connection-state model obscured silent playback failures (Class B outages).
Recovery behavior — the whole point of this rewrite — lived in a dependency
we didn't control.

## Decision
Write a thin client (~300–400 lines: REST for loads, WebSocket for events)
in `services/bot/src/jacky/audio/`. We own reconnect, backoff, and session
resuming outright.

## Consequences
(+) Full control over failure behavior; no patching. (−) We maintain
protocol code; mitigated by Lavalink v4's stable REST/WS API and tests
against a fake WebSocket.
