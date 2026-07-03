# ADR-0002: poToken sidecar as an independent auth layer

**Status:** Accepted · 2026-07-02

## Context
The dominant outage class (F2): Google periodically revokes the OAuth
refresh token, and recovery needed a human for days. Any single credential
is a single point of failure.

## Decision
Run a scheduled one-shot container (token-minter) that mints a poToken +
visitorData with headless Chromium and pushes them to Lavalink at runtime.
No Google account involved — nothing to revoke. OAuth stays as the second,
independent layer; client ordering as the third.

## Consequences
(+) OAuth revocation no longer stops playback; layers fail independently.
(−) ~400MB RAM spike during each ~1-minute mint (fits budget: one-shot).
