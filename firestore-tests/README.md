# Firestore rules tests

Tests for `../firestore.rules`, run against the **local Firestore emulator**.
They never contact the real project: the project id is `demo-jacky` (a `demo-`
id cannot resolve to a real Firebase project), and `tests/helpers.ts` refuses
to load unless the emulator address is local.

Every rule is pinned in both directions — the operation the dashboard really
performs succeeds, and the abusive variant is denied. The "what the dashboard
does" cases name the frontend file the write was taken from.

## Run

```bash
cd firestore-tests
npm install     # first time only
npm test
```

Needs Node 20+ and a Java runtime on the PATH (the emulator is a jar; these
were written and run with Java 26 — `firebase-tools` reports its own minimum
if yours is too old). `npm test` starts the emulator through a pinned
`firebase-tools` (via `npx`, nothing global), runs the suite, and stops it.
The first run downloads the emulator jar into `~/.cache/firebase/emulators`.
The emulator listens on `127.0.0.1:8085` — set in the `emulators` block of
`../firebase.json`.

If a run is killed half-way, an emulator can be left holding the port ("port
taken" on the next run): end the leftover `java … cloud-firestore-emulator`
process and run again.

## The database is named — read this before changing the helpers

`firebase.json` deploys the rules to the database **`discord-music-bot`**, not
`(default)`, and the dashboard opens that same database. The tests read the id
from `firebase.json`, so they follow it if it ever changes.

`@firebase/rules-unit-testing` only knows `(default)`: its clients open
`(default)`, and its rule loader updates `(default)` and leaves a named
database untouched — **without any error**. A suite built the usual way runs
green against rules it never loaded. So `tests/helpers.ts` builds its own
clients with the normal web SDK and pushes the rules file to the
database-scoped emulator endpoint itself; the library is used only for
`assertFails` / `assertSucceeds`. `tests/config.test.ts` checks, on every run,
that the loader really governs the database under test and that a rules file
with compile errors or warnings fails the run.

## Checking that the suite still has teeth

Point `RULES_FILE` at a rules file that *should* fail — the previous version,
or a copy with one check removed — and the suite must go red:

```bash
git show HEAD~1:firestore.rules > /tmp/old.rules
RULES_FILE=/tmp/old.rules npm test        # PowerShell: $env:RULES_FILE = "..."
```

A run with `RULES_FILE` set always has at least one failure by design (the
"no RULES_FILE override" test), so "all passed" can only ever mean the
repository's own rules passed.

## Files

| File | Covers |
|------|--------|
| `tests/helpers.ts` | emulator wiring, identities (signed out / signed in / rules-exempt seeding), fixtures |
| `tests/config.test.ts` | the wiring itself; default deny; bot-only collections |
| `tests/sessionCodes.test.ts` | `sessionCodes` — get yes, list and write no |
| `tests/servers.test.ts` | `servers/{guildId}` — get, no list / create / delete, field-level update rules |
| `tests/subcollections.test.ts` | `playlists`, `commandHistory`, `musicHistory`, `dragStats`, `memberStats`, `history`, and that nothing else exists |
| `tests/serverOwners.test.ts` | `serverOwners` — private reads, activation, takeover and sabotage attempts |
| `tests/unchanged.test.ts` | regression pins for `presence` and `users/{uid}/pinnedServers` |

`.npmrc` sets `legacy-peer-deps` because npm 10.x crashes resolving vitest's
optional peers when it builds this tree from scratch; the one peer that
matters (`firebase`) is pinned in `package.json`.
