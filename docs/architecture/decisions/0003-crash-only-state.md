# ADR-0003: Crash-only containers; state externalized

**Status:** Accepted · 2026-07-02

## Context
The old bot accreted 1,443 lines of interleaved playback + watchdog code
because in-process recovery required delicate reconnect choreography.

## Decision
No container holds durable state. Firestore is the source of truth (written
BEFORE Lavalink is instructed); tokens live on a named volume. The guardian's
only recovery verb is "restart." Bot and guardian each carry a small
duplicated runtime helper rather than sharing a library — 15 lines of
duplication beats cross-service packaging coupling at this scale.

## Consequences
(+) Recovery logic is trivial and centralized in the guardian; bot code is
pure playback. (−) Firestore write latency is on the command path (~50ms,
acceptable for a music bot).
