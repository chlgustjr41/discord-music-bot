# Collaborative Session Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show who else is on the session dashboard, render their live cursors, and let signed-in users choose between the shared view and a private one.

**Architecture:** A new top-level Firestore collection `presence/{sessionCode}/participants/{uid}` carries display name, colour, and a normalized cursor position. All decision logic lives in a pure, tested `src/lib/presence.ts`; the hook does nothing but Firestore I/O. Anonymous visitors never publish or read presence — enforced in rules, not just UI.

**Tech Stack:** React 19 + TypeScript + Vite + Firebase Firestore; **vitest + jsdom added** (the frontend has no test runner today).

**Spec:** `docs/superpowers/specs/2026-08-09-collaborative-dashboard-design.md` — read it first; it governs.

**House rules (every task):** TDD for the pure modules — write the test, run it, watch it fail *for the right reason*, implement, watch it pass. Gates: `cd frontend && npm test -- --run && npm run build && npm run lint`. Commit per task.

**Mutation-testing rule:** this project proves every test non-vacuous. Each task lists mutations; revert the fix, confirm the named test fails, restore **from a file copy you take first — NOT `git checkout --`**, which reverts to HEAD and wipes uncommitted work. **If a predicted mutation does not fail, say so plainly and strengthen the test** — implementers on the previous two plans caught eight bad predictions in my instructions and were right every time.

**Baseline:** frontend has 0 tests and no runner. Branch `feat/collab-dashboard` off master.

**Already verified in the codebase — do not rebuild:**
- `AccountMenu` is already rendered in `LandingPage.tsx`.
- `useIdleSignOut()` is already called app-wide in `App.tsx` (30 min, warning at 60 s, cross-tab).
- `SearchPanel` search goes through the **bot**: it writes `searchQuery` to `servers/{id}` and the bot writes `searchResults` back.
- `src/services/api.ts` exports `searchYouTube(query, signal)` and is currently **unused**.
- `functions/searchYouTube` is **not deployed** (404) and `VITE_FUNCTIONS_URL` is unset.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `frontend/package.json`, `vitest.config.ts` | modify/create | test runner |
| `frontend/src/lib/presence.ts` | **create** | all pure logic: colour, staleness, coordinates, gating |
| `frontend/src/lib/presence.test.ts` | **create** | its tests |
| `frontend/src/hooks/usePresence.ts` | **create** | Firestore subscribe/publish/heartbeat/cleanup |
| `frontend/src/components/PresenceBar.tsx` | **create** | avatar stack |
| `frontend/src/components/CursorLayer.tsx` | **create** | remote cursor overlay |
| `frontend/src/components/SharedViewToggle.tsx` | **create** | shared/solo control |
| `frontend/src/components/Dashboard.tsx` | modify | header wiring, cursor container, pass `shared` down |
| `frontend/src/components/SearchPanel.tsx` | modify | solo search path |
| `frontend/src/lib/searchMode.ts` + `.test.ts` | **create** | pure decision logic for the search path |
| `firestore.rules` | modify | presence rules |
| `firebase.json` | modify | `/api/searchYouTube` rewrite |

---

## Task 1: Test runner

**Files:** `frontend/package.json`, `frontend/vitest.config.ts`, `frontend/src/lib/smoke.test.ts` (temporary)

- [ ] **Step 1: Install.** `cd frontend && npm i -D vitest@^3 jsdom@^25 @testing-library/react@^16 @testing-library/jest-dom@^6`

- [ ] **Step 2: Config.** Create `frontend/vitest.config.ts`:

```ts
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  test: { environment: "jsdom", globals: true },
});
```

Add to `package.json` scripts: `"test": "vitest"`.

- [ ] **Step 3: Prove it runs.** Create `frontend/src/lib/smoke.test.ts`:

```ts
import { describe, expect, it } from "vitest";

describe("runner", () => {
  it("runs", () => expect(1 + 1).toBe(2));
});
```

Run `npm test -- --run` → 1 passed. Then **delete the smoke file** — it has served its purpose and a permanent tautology test is noise.

- [ ] **Step 4: Verify.** `npm run build` and `npm run lint` still pass. If `vitest.config.ts` trips the ESLint config (it is outside `src`), add it to the ESLint ignore list rather than weakening a rule.

- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/vitest.config.ts
git commit -m "test(frontend): add vitest"
```

## Task 2: Pure presence logic

**Files:** `frontend/src/lib/presence.ts`, `frontend/src/lib/presence.test.ts`

- [ ] **Step 1: Write the failing tests.** Create `frontend/src/lib/presence.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  PRESENCE_TTL_MS,
  colorForUid,
  livingParticipants,
  movedEnough,
  shouldPublish,
  toNormalized,
  toPixels,
  type Participant,
} from "./presence";

const rect = { left: 100, top: 50, width: 800, height: 400 } as DOMRect;

function p(uid: string, updatedAt: number): Participant {
  return { uid, name: uid, photoURL: null, color: "#fff", cursor: null, updatedAt };
}

describe("colorForUid", () => {
  it("is stable for the same uid", () => {
    expect(colorForUid("abc")).toBe(colorForUid("abc"));
  });

  it("differs across typical uids", () => {
    const uids = ["a", "b", "c", "d", "e", "f", "g", "h"];
    expect(new Set(uids.map(colorForUid)).size).toBeGreaterThan(4);
  });

  it("never returns an empty or transparent colour", () => {
    for (const uid of ["", "x", "averyverylonguid0123456789"]) {
      expect(colorForUid(uid)).toMatch(/^hsl\(/);
    }
  });
});

describe("livingParticipants", () => {
  const now = 10_000;

  it("drops entries older than the TTL and keeps fresh ones", () => {
    const all = [p("fresh", now - 1_000), p("stale", now - PRESENCE_TTL_MS - 1)];
    expect(livingParticipants(all, "me", now).map((x) => x.uid)).toEqual(["fresh"]);
  });

  it("keeps an entry exactly at the TTL boundary", () => {
    const all = [p("edge", now - PRESENCE_TTL_MS)];
    expect(livingParticipants(all, "me", now)).toHaveLength(1);
  });

  it("always excludes yourself — you do not need your own cursor", () => {
    const all = [p("me", now), p("other", now)];
    expect(livingParticipants(all, "me", now).map((x) => x.uid)).toEqual(["other"]);
  });

  it("tolerates a missing or malformed updatedAt rather than throwing", () => {
    const all = [{ ...p("bad", now), updatedAt: undefined as unknown as number }];
    expect(livingParticipants(all, "me", now)).toEqual([]);
  });
});

describe("coordinates", () => {
  it("round-trips a point through normalize and back", () => {
    const norm = toNormalized(500, 250, rect)!;
    const px = toPixels(norm, rect);
    expect(px.x).toBeCloseTo(500);
    expect(px.y).toBeCloseTo(250);
  });

  it("normalizes to 0..1 relative to the rect, not the viewport", () => {
    expect(toNormalized(100, 50, rect)).toEqual({ x: 0, y: 0 });
    expect(toNormalized(900, 450, rect)).toEqual({ x: 1, y: 1 });
  });

  it("returns null outside the rect instead of clamping", () => {
    // Clamping would pin a departed pointer to the edge, which reads as
    // "they are still here, hugging the border".
    expect(toNormalized(99, 250, rect)).toBeNull();
    expect(toNormalized(500, 451, rect)).toBeNull();
  });

  it("returns null for a zero-sized rect rather than dividing by zero", () => {
    expect(toNormalized(0, 0, { left: 0, top: 0, width: 0, height: 0 } as DOMRect)).toBeNull();
  });
});

describe("movedEnough", () => {
  it("suppresses sub-threshold movement", () => {
    expect(movedEnough({ x: 0.5, y: 0.5 }, { x: 0.5005, y: 0.5 }, 8, rect)).toBe(false);
  });

  it("allows movement past the threshold", () => {
    expect(movedEnough({ x: 0.5, y: 0.5 }, { x: 0.53, y: 0.5 }, 8, rect)).toBe(true);
  });

  it("always allows the first point", () => {
    expect(movedEnough(null, { x: 0.5, y: 0.5 }, 8, rect)).toBe(true);
  });
});

describe("shouldPublish", () => {
  it("is the single auth gate: signed out never publishes", () => {
    expect(shouldPublish("shared", false)).toBe(false);
    expect(shouldPublish("solo", false)).toBe(false);
  });

  it("publishes only when signed in AND shared", () => {
    expect(shouldPublish("shared", true)).toBe(true);
    expect(shouldPublish("solo", true)).toBe(false);
  });
});
```

- [ ] **Step 2: Run to verify failure**

Run: `cd frontend && npm test -- --run`
Expected: FAIL — cannot resolve `./presence`.

- [ ] **Step 3: Implement.** Create `frontend/src/lib/presence.ts`:

```ts
/**
 * Pure logic behind dashboard presence and live cursors.
 *
 * Deliberately free of Firebase imports so every rule here — who publishes,
 * who is still here, where a cursor actually is — can be tested exhaustively
 * without a network or an emulator. The hook that owns the I/O makes no
 * decisions of its own.
 */

export type ViewMode = "shared" | "solo";

export interface Point {
  x: number;
  y: number;
}

export interface Participant {
  uid: string;
  name: string;
  photoURL: string | null;
  color: string;
  cursor: Point | null;
  updatedAt: number;
}

/** Entries older than this are treated as gone. Firestore has no
 *  server-side disconnect hook, so staleness — not onDisconnect — is what
 *  removes someone whose laptop lid closed mid-session. */
export const PRESENCE_TTL_MS = 45_000;
export const HEARTBEAT_MS = 15_000;
export const CURSOR_THROTTLE_MS = 100;
export const CURSOR_MIN_PX = 8;

/** Stable hue per uid: a person keeps their colour across reloads and looks
 *  the same to everyone, because it is derived rather than assigned. */
export function colorForUid(uid: string): string {
  let hash = 0;
  for (let i = 0; i < uid.length; i++) {
    hash = (hash << 5) - hash + uid.charCodeAt(i);
    hash |= 0;
  }
  return `hsl(${Math.abs(hash) % 360}, 70%, 60%)`;
}

export function livingParticipants(
  all: Participant[],
  selfUid: string | null,
  now: number,
): Participant[] {
  return all.filter(
    (p) =>
      p.uid !== selfUid &&
      typeof p.updatedAt === "number" &&
      now - p.updatedAt <= PRESENCE_TTL_MS,
  );
}

export function toNormalized(
  clientX: number,
  clientY: number,
  rect: DOMRect,
): Point | null {
  if (rect.width <= 0 || rect.height <= 0) return null;
  const x = (clientX - rect.left) / rect.width;
  const y = (clientY - rect.top) / rect.height;
  // Outside the shared area is "not here", not "pinned to the edge".
  if (x < 0 || x > 1 || y < 0 || y > 1) return null;
  return { x, y };
}

export function toPixels(point: Point, rect: DOMRect): Point {
  return { x: rect.left + point.x * rect.width, y: rect.top + point.y * rect.height };
}

export function movedEnough(
  prev: Point | null,
  next: Point,
  minPx: number,
  rect: DOMRect,
): boolean {
  if (!prev) return true;
  const dx = (next.x - prev.x) * rect.width;
  const dy = (next.y - prev.y) * rect.height;
  return Math.hypot(dx, dy) >= minPx;
}

/** The one place that answers "does this browser broadcast?", so the auth
 *  gate cannot be applied in one code path and forgotten in another. */
export function shouldPublish(mode: ViewMode, signedIn: boolean): boolean {
  return signedIn && mode === "shared";
}
```

- [ ] **Step 4: Verify.** `npm test -- --run` → all pass. `npm run build`, `npm run lint` clean.

- [ ] **Step 5: Mutation-verify.** Copy the file first, then:
1. `shouldPublish` → `return mode === "shared"` (drop the auth check) → the signed-out test must fail.
2. `toNormalized` → clamp instead of returning null → the out-of-rect test must fail.
3. `livingParticipants` → drop the `p.uid !== selfUid` clause → the self test must fail.
4. `movedEnough` → `return true` always → the sub-threshold test must fail.
5. TTL comparison `<=` → `<` → the boundary test must fail.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/presence.ts frontend/src/lib/presence.test.ts
git commit -m "feat(dashboard): pure presence and cursor logic"
```

## Task 3: Search-mode decision logic

**Files:** `frontend/src/lib/searchMode.ts`, `frontend/src/lib/searchMode.test.ts`

The search panel has two paths and a fallback. Extract the decision so it is testable without a network.

- [ ] **Step 1: Write the failing tests.** Create `frontend/src/lib/searchMode.test.ts`:

```ts
import { describe, expect, it, vi } from "vitest";
import { runSearch, shouldFollowSharedSearch } from "./searchMode";

describe("shouldFollowSharedSearch", () => {
  it("follows in shared mode", () => {
    expect(shouldFollowSharedSearch("shared")).toBe(true);
  });

  it("ignores other people's searches in solo mode", () => {
    expect(shouldFollowSharedSearch("solo")).toBe(false);
  });
});

describe("runSearch", () => {
  it("uses the bot path in shared mode without touching the local endpoint", async () => {
    const local = vi.fn(async (_q: string) => [{ videoId: "x" }]);
    const bot = vi.fn(async (_q: string) => {});
    const out = await runSearch("shared", "hello", { local, bot });
    expect(bot).toHaveBeenCalledWith("hello");
    expect(local).not.toHaveBeenCalled();
    expect(out).toEqual({ via: "bot", results: null });
  });

  it("uses the local endpoint in solo mode", async () => {
    const local = vi.fn(async (_q: string) => [{ videoId: "x" }]);
    const bot = vi.fn(async (_q: string) => {});
    const out = await runSearch("solo", "hello", { local, bot });
    expect(local).toHaveBeenCalledWith("hello");
    expect(bot).not.toHaveBeenCalled();
    expect(out).toEqual({ via: "local", results: [{ videoId: "x" }] });
  });

  it("falls back to the bot when the local endpoint is unavailable", async () => {
    // True today: functions/searchYouTube is not deployed. Solo search must
    // still work rather than silently returning nothing.
    const local = vi.fn(async (_q: string) => {
      throw new Error("404");
    });
    const bot = vi.fn(async (_q: string) => {});
    const out = await runSearch("solo", "hello", { local, bot });
    expect(bot).toHaveBeenCalledWith("hello");
    expect(out).toEqual({ via: "bot-fallback", results: null });
  });

  it("propagates a bot failure rather than reporting success", async () => {
    const local = vi.fn(async (_q: string) => {
      throw new Error("404");
    });
    const bot = vi.fn(async (_q: string) => {
      throw new Error("firestore down");
    });
    await expect(runSearch("solo", "hello", { local, bot })).rejects.toThrow();
  });
});
```

- [ ] **Step 2: Run to verify failure** — `npm test -- --run` → cannot resolve `./searchMode`.

- [ ] **Step 3: Implement.** Create `frontend/src/lib/searchMode.ts`:

```ts
/**
 * Which search path a dashboard uses, and what happens when the private one
 * is unavailable.
 *
 * Search is a BOT capability: the client writes `searchQuery` to the shared
 * server document and the bot writes results back, so a search in shared mode
 * is visible to everyone in the session by construction.
 *
 * Solo mode therefore tries a client-side endpoint first. That endpoint
 * (functions/searchYouTube) is not deployed today, so the fallback is the
 * live path rather than a theoretical one — solo search works, it just is not
 * yet private. Deploying the function makes it private with no change here.
 */

import type { ViewMode } from "./presence";

export type SearchVia = "bot" | "local" | "bot-fallback";

export interface SearchDeps<T> {
  local: (query: string) => Promise<T[]>;
  bot: (query: string) => Promise<void>;
}

export interface SearchOutcome<T> {
  via: SearchVia;
  results: T[] | null;
}

/** Solo dashboards keep their own results: another person searching must not
 *  replace what you are looking at. */
export function shouldFollowSharedSearch(mode: ViewMode): boolean {
  return mode === "shared";
}

export async function runSearch<T>(
  mode: ViewMode,
  query: string,
  deps: SearchDeps<T>,
): Promise<SearchOutcome<T>> {
  if (mode === "shared") {
    await deps.bot(query);
    return { via: "bot", results: null };
  }
  try {
    return { via: "local", results: await deps.local(query) };
  } catch {
    // Deliberately not swallowed silently: the caller toasts once so the user
    // knows this search became visible to the session.
    await deps.bot(query);
    return { via: "bot-fallback", results: null };
  }
}
```

- [ ] **Step 4: Verify.** `npm test -- --run`, `npm run build`, `npm run lint`.

- [ ] **Step 5: Mutation-verify.** Copy first, then:
1. Make `runSearch` always use `bot` → the solo test must fail.
2. Remove the `try/catch` → the fallback test must fail.
3. `shouldFollowSharedSearch` → `return true` → the solo test must fail.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/searchMode.ts frontend/src/lib/searchMode.test.ts
git commit -m "feat(dashboard): search path decision logic"
```

## Task 4: The presence hook

**Files:** `frontend/src/hooks/usePresence.ts`

No unit tests — this is I/O over Firestore, and every decision it makes was tested in Task 2. It is verified in the browser in Task 8.

- [ ] **Step 1: Implement.** Create `frontend/src/hooks/usePresence.ts`:

```ts
/**
 * Publishes this browser's presence and cursor, and subscribes to everyone
 * else's, for one session dashboard.
 *
 * Every rule lives in lib/presence.ts; this file is I/O only. It writes
 * nothing at all unless shouldPublish() says so, which is what keeps
 * anonymous visitors invisible.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import {
  collection,
  deleteDoc,
  doc,
  onSnapshot,
  setDoc,
} from "firebase/firestore";
import type { User } from "firebase/auth";
import { db } from "../firebase";
import {
  CURSOR_MIN_PX,
  CURSOR_THROTTLE_MS,
  HEARTBEAT_MS,
  type Participant,
  type Point,
  type ViewMode,
  colorForUid,
  livingParticipants,
  movedEnough,
  shouldPublish,
} from "../lib/presence";

export function usePresence(
  sessionCode: string | undefined,
  user: User | null,
  mode: ViewMode,
) {
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [now, setNow] = useState(() => Date.now());
  const lastPoint = useRef<Point | null>(null);
  const lastWrite = useRef(0);

  const publishing = shouldPublish(mode, !!user);
  const selfUid = user?.uid ?? null;

  const selfRef = useCallback(() => {
    if (!sessionCode || !selfUid) return null;
    return doc(db, "presence", sessionCode, "participants", selfUid);
  }, [sessionCode, selfUid]);

  // Subscribe. Reading requires auth (see firestore.rules), so anonymous
  // visitors get nothing and the UI shows nothing.
  useEffect(() => {
    if (!sessionCode || !user) {
      setParticipants([]);
      return;
    }
    const unsub = onSnapshot(
      collection(db, "presence", sessionCode, "participants"),
      (snap) => {
        setParticipants(
          snap.docs.map((d) => ({ uid: d.id, ...d.data() }) as Participant),
        );
      },
      // Presence must never take the dashboard down with it.
      () => setParticipants([]),
    );
    return unsub;
  }, [sessionCode, user]);

  // Re-evaluate staleness on a timer: a participant who stops heartbeating
  // produces no snapshot, so nothing would otherwise re-render them away.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 5_000);
    return () => clearInterval(id);
  }, []);

  // Publish + heartbeat, and remove ourselves the moment we stop publishing.
  useEffect(() => {
    const ref = selfRef();
    if (!ref || !user) return;
    if (!publishing) {
      void deleteDoc(ref).catch(() => {});
      return;
    }
    const write = (cursor: Point | null) =>
      setDoc(
        ref,
        {
          name: user.displayName || "Guest",
          photoURL: user.photoURL ?? null,
          color: colorForUid(user.uid),
          cursor,
          updatedAt: Date.now(),
        },
        { merge: true },
      ).catch(() => {});

    void write(null);
    const id = setInterval(() => void write(lastPoint.current), HEARTBEAT_MS);
    const leave = () => void deleteDoc(ref).catch(() => {});
    window.addEventListener("pagehide", leave);
    return () => {
      clearInterval(id);
      window.removeEventListener("pagehide", leave);
      leave();
    };
  }, [selfRef, publishing, user]);

  const publishCursor = useCallback(
    (clientX: number, clientY: number, rect: DOMRect) => {
      const ref = selfRef();
      if (!ref || !publishing || document.visibilityState !== "visible") return;
      const point = normalize(clientX, clientY, rect);
      if (!point) return;
      if (!movedEnough(lastPoint.current, point, CURSOR_MIN_PX, rect)) return;
      const t = Date.now();
      if (t - lastWrite.current < CURSOR_THROTTLE_MS) return;
      lastWrite.current = t;
      lastPoint.current = point;
      void setDoc(ref, { cursor: point, updatedAt: t }, { merge: true }).catch(
        () => {},
      );
    },
    [selfRef, publishing],
  );

  return {
    participants: livingParticipants(participants, selfUid, now),
    publishCursor,
    publishing,
  };
}
```

Add `import { toNormalized as normalize } from "../lib/presence";` to the import block rather than defining a local `normalize` — the alias exists only to keep the call site short.

- [ ] **Step 2: Verify.** `npm run build` and `npm run lint` clean. `npm test -- --run` unchanged.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/usePresence.ts
git commit -m "feat(dashboard): presence hook"
```

## Task 5: Presence UI

**Files:** `frontend/src/components/PresenceBar.tsx`, `CursorLayer.tsx`, `SharedViewToggle.tsx`

Match the dashboard's existing shadcn idiom — read `IdentityChip.tsx` and `NodeStatus.tsx` first and follow their class conventions rather than inventing a new style.

- [ ] **Step 1: `PresenceBar.tsx`** — overlapping avatars, `ring` in each participant's colour, initial fallback when `photoURL` is null, `title` for the name, `+N` chip past 4. Renders `null` when the list is empty so an empty session shows no chrome.

- [ ] **Step 2: `CursorLayer.tsx`** — a `fixed inset-0 pointer-events-none z-50` overlay. For each participant with a non-null cursor, convert with `toPixels(cursor, rect)` and render an SVG arrow plus a name pill in their colour, positioned with `transform: translate3d(...)` and `transition: transform 120ms linear` so 10 Hz updates read as motion rather than teleporting. Takes the container `rect` as a prop.

- [ ] **Step 3: `SharedViewToggle.tsx`** — a two-state button (`Users` / `UserRound` from lucide). Renders `null` when signed out. Shows a short tooltip: shared broadcasts your name, colour, and pointer to others in this session.

- [ ] **Step 4: Verify.** `npm run build`, `npm run lint` clean.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/PresenceBar.tsx frontend/src/components/CursorLayer.tsx frontend/src/components/SharedViewToggle.tsx
git commit -m "feat(dashboard): presence bar, cursor layer, shared-view toggle"
```

## Task 6: Wire the dashboard

**Files:** `frontend/src/components/Dashboard.tsx`

- [ ] **Step 1: Mode state.** Persist per session, defaulting to shared:

```tsx
const MODE_KEY = (code: string) => `jacky:view:${code}`;

const [mode, setMode] = useState<ViewMode>(() =>
  (localStorage.getItem(MODE_KEY(sessionCode ?? "")) as ViewMode) || "shared",
);
useEffect(() => {
  if (sessionCode) localStorage.setItem(MODE_KEY(sessionCode), mode);
}, [sessionCode, mode]);
```

- [ ] **Step 2: Header.** Add `<AccountMenu />`, `<PresenceBar participants={participants} />`, and `<SharedViewToggle mode={mode} onChange={setMode} signedIn={!!user} />` beside the existing `IdentityChip` / `PinServerButton`. Keep the existing mobile behavior — check whether the header row wraps at narrow widths and, if adding three controls breaks it, let them wrap rather than shrinking existing controls.

- [ ] **Step 3: Cursor container.** Put a `ref` on the main content element, track its rect (initial measure plus `resize` and `scroll` listeners), attach `onPointerMove` that calls `publishCursor(e.clientX, e.clientY, rect)`, and render `<CursorLayer participants={participants} rect={rect} />`.

- [ ] **Step 4: Pass mode down.** `<SearchPanel … mode={mode} />`.

- [ ] **Step 5: Verify.** `npm run build`, `npm run lint`, `npm test -- --run`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Dashboard.tsx
git commit -m "feat(dashboard): wire presence, cursors, and the view toggle"
```

## Task 7: Search panel modes

**Files:** `frontend/src/components/SearchPanel.tsx`

- [ ] **Step 1: Accept `mode: ViewMode`.**

- [ ] **Step 2: Gate the follow.** The effect that consumes `searchResults` / `searchQuery` props must early-return when `!shouldFollowSharedSearch(mode)`, so someone else's search cannot replace a solo user's results.

- [ ] **Step 3: Route the search.** Replace the body of `fireSearch` with `runSearch(mode, q, { local, bot })` where `local` is `searchYouTube` from `../services/api` and `bot` is the existing `updateDoc(...)` write. On `via === "local"`, set results directly and clear loading. On `via === "bot"` / `"bot-fallback"`, keep the existing wait-for-Firestore behavior. On `"bot-fallback"`, `toast()` **once per mount** (a ref guard, not per search) explaining that search runs through the bot so results are visible to others.

- [ ] **Step 4: Verify.** `npm run build`, `npm run lint`, `npm test -- --run`.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/SearchPanel.tsx
git commit -m "feat(dashboard): solo search keeps its own results"
```

## Task 8: Rules, rewrite, browser verification

**Files:** `firestore.rules`, `firebase.json`

- [ ] **Step 1: Rules.** Add above the default-deny block:

```
    // Ephemeral dashboard presence and cursors. Deliberately NOT under
    // /servers, whose subcollection rule is `allow read, write: if true` —
    // a rule there could never restrict anything. Read requires auth so
    // anonymous visitors cannot enumerate a session's participants; write is
    // uid-scoped so nobody can move someone else's cursor.
    match /presence/{sessionCode}/participants/{uid} {
      allow read: if request.auth != null;
      allow write: if request.auth != null && request.auth.uid == uid;
    }
```

- [ ] **Step 2: Rewrite.** In `firebase.json`, **before** the SPA catch-all:

```json
      { "source": "/api/searchYouTube", "function": "searchYouTube" },
```

and default `FUNCTIONS_BASE` in `src/services/api.ts` to `/api` when `VITE_FUNCTIONS_URL` is unset, so solo search is same-origin and needs no env var once the function is deployed.

- [ ] **Step 3: Browser verification.** Use the preview tools, not a manual request to the user.
  - `preview_start` the dev server (add a `.claude/launch.json` entry if absent).
  - Load `/dashboard/<a real session code>`; confirm no console errors, the header shows the account menu, and an anonymous window shows no presence UI.
  - Sign in; confirm your own avatar does NOT appear in your own bar (self is excluded).
  - Open a second browser context signed in as the same account to confirm the wiring end-to-end; note in your report that a true two-account test needs a second Google account, which is a manual follow-up.
  - Toggle to solo; confirm the presence doc is deleted (watch the network/Firestore tab) and cursors disappear.
  - Screenshot the dashboard header and the cursor overlay.

- [ ] **Step 4: Deploy rules only.** `firebase deploy --only firestore:rules` — do **not** deploy hosting or functions in this step; the frontend deploy is the last action after everything is verified.

- [ ] **Step 5: Commit**

```bash
git add firestore.rules firebase.json frontend/src/services/api.ts
git commit -m "feat(dashboard): presence rules and same-origin search rewrite"
```

## Task 9: Docs, merge, deploy

- [ ] **Step 1: README/docs.** Document in `docs/` (find where the web app is documented; `README.md` if nowhere else) the shared/solo distinction, that presence is signed-in only, the 45 s staleness window, and the honest note that solo search currently falls back to the bot until `searchYouTube` is deployed.

- [ ] **Step 2: Merge.**

```bash
git checkout master && git merge --no-ff feat/collab-dashboard -m "Merge feat/collab-dashboard: presence, live cursors, shared/solo view" && git push origin master
```

- [ ] **Step 3: Deploy hosting.**

```bash
cd frontend && npm run build && cd .. && firebase deploy --only hosting
```

- [ ] **Step 4: Verify live.** Load the deployed dashboard, confirm the header renders and there are no console errors.
