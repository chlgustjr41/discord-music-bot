# Shared View State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Panel expand/collapse and text input contents sync between shared-mode dashboard participants, with a chip showing who is typing.

**Architecture:** One collective document `presence/{sessionCode}/shared/view` alongside the existing per-user participant docs. All conflict rules live in a pure, tested `src/lib/sharedView.ts`; a single subscription is shared through context so seven panels do not open seven listeners. Adopting a remote value is deliberately inert — it must never fire a search or republish.

**Tech Stack:** React 19 + TypeScript + Vite + Firebase Firestore; vitest (61 tests currently pass).

**Spec:** `docs/superpowers/specs/2026-08-09-shared-view-state-design.md` — read it first; it governs.

**House rules:** TDD for pure modules — write the test, run it, watch it fail *for the right reason*, implement, watch it pass. Gates: `cd frontend && npm test -- --run && npm run build`. **`npx eslint .` has 16 pre-existing errors; the gate is zero NEW** (compare counts; the summary line itself contains the word "error", so count from the summary, not a grep).

**Mutation rule:** back files up by COPYING and restore from the copy — never `git checkout --`. Verify each mutation actually landed (grep the file) before trusting a green run. **If a predicted mutation does not fail, say so and strengthen the test.** Implementers on the previous three plans caught fourteen bad predictions in my instructions and were right every time.

**Baseline:** 61 tests. Branch `feat/shared-view-state` off master.

**Verified in the codebase:**
- Seven panels use the identical seam `const [expanded, setExpanded] = useState(<default>)`: `ActivityLog.tsx:22`, `CommandHistory.tsx:40`, `MusicHistory.tsx:41`, `StatsPanel.tsx:64`, `PlaylistManager.tsx:52`, `HistoryPanel.tsx:17` (all default `false`), and `Queue.tsx:182` (defaults **`true`**).
- `SearchPanel.tsx` holds `query` in local state; a debounce effect fires `fireSearch` 200 ms after it changes.
- `PresenceLayer` owns `usePresence`; `Dashboard` holds `mode` and passes it down.
- `presence.ts` exports `shouldPublish(mode, signedIn)` — the single auth gate.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `src/lib/sharedView.ts` + `.test.ts` | **create** | pure conflict/merge/attribution rules |
| `src/hooks/useSharedView.ts` | **create** | one Firestore subscription + writers, via context |
| `src/hooks/useSharedSection.ts` | **create** | drop-in replacement for `useState<boolean>` |
| `src/hooks/useSharedInput.ts` | **create** | publish/adopt for one text field |
| `src/components/TypistChip.tsx` | **create** | "Ada is typing" |
| 7 panel components | modify | one line each |
| `src/components/SearchPanel.tsx` | modify | shared query + loop guard |
| `src/components/Dashboard.tsx` | modify | provide the context |
| `firestore.rules` | modify | the shared view document |

---

## Task 1: Pure shared-view logic

**Files:** `frontend/src/lib/sharedView.ts`, `frontend/src/lib/sharedView.test.ts`

- [ ] **Step 1: Write the failing tests.**

```ts
import { describe, expect, it } from "vitest";
import {
  MAX_INPUT_LEN,
  mergeSections,
  shouldAdoptInput,
  typistLabel,
  type InputEntry,
} from "./sharedView";
import type { Participant } from "./presence";

function entry(over: Partial<InputEntry> = {}): InputEntry {
  return { value: "radiohead", by: "ada", ...over };
}

function participant(over: Partial<Participant> = {}): Participant {
  return {
    uid: "ada", name: "Ada", photoURL: null,
    color: "hsl(1, 2%, 3%)", cursor: null, updatedAt: Date.now(), ...over,
  };
}

describe("mergeSections", () => {
  it("lets a remote value win over the panel default", () => {
    expect(mergeSections({ queue: false }, { queue: true, stats: false }))
      .toEqual({ queue: false, stats: false });
  });

  it("fills gaps with defaults so a panel nobody has touched still works", () => {
    expect(mergeSections({}, { queue: true })).toEqual({ queue: true });
  });

  it("ignores non-boolean remote values rather than rendering undefined", () => {
    expect(mergeSections({ queue: "yes" as unknown as boolean }, { queue: true }))
      .toEqual({ queue: true });
  });
});

describe("shouldAdoptInput", () => {
  const base = {
    focused: false, remoteBy: "ada", selfUid: "bob",
    remoteValue: "radiohead", localValue: "",
  };

  it("adopts someone else's value when you are not typing", () => {
    expect(shouldAdoptInput(base)).toBe(true);
  });

  it("never overwrites a field you are typing in", () => {
    // Two people typing at once each keep their own text until they blur,
    // rather than fighting mid-word.
    expect(shouldAdoptInput({ ...base, focused: true })).toBe(false);
  });

  it("ignores your own echo", () => {
    expect(shouldAdoptInput({ ...base, remoteBy: "bob" })).toBe(false);
  });

  it("does nothing when the value already matches", () => {
    expect(shouldAdoptInput({ ...base, localValue: "radiohead" })).toBe(false);
  });

  it("treats a missing writer as not adoptable", () => {
    expect(shouldAdoptInput({ ...base, remoteBy: "" })).toBe(false);
  });
});

describe("typistLabel", () => {
  const people = [participant()];

  it("names the person who last typed, with their presence colour", () => {
    expect(typistLabel(entry(), "bob", people)).toEqual({
      name: "Ada", color: "hsl(1, 2%, 3%)",
    });
  });

  it("is silent about your own typing", () => {
    expect(typistLabel(entry({ by: "bob" }), "bob", people)).toBeNull();
  });

  it("is silent when the writer has left the session", () => {
    // Their name would otherwise linger on a value nobody is still editing.
    expect(typistLabel(entry({ by: "ghost" }), "bob", people)).toBeNull();
  });

  it("is silent when there is no entry at all", () => {
    expect(typistLabel(undefined, "bob", people)).toBeNull();
  });
});

describe("MAX_INPUT_LEN", () => {
  it("is small enough to bound a write but large enough for a real query", () => {
    expect(MAX_INPUT_LEN).toBeGreaterThanOrEqual(100);
    expect(MAX_INPUT_LEN).toBeLessThanOrEqual(500);
  });
});
```

- [ ] **Step 2: Run to verify failure** — cannot resolve `./sharedView`.

- [ ] **Step 3: Implement.** Create `frontend/src/lib/sharedView.ts`:

```ts
/**
 * Pure rules for the collective dashboard view: which panels are open, what
 * is in the shared text fields, and who last typed there.
 *
 * Free of Firebase imports so the conflict rules — the part that decides
 * whether someone's keystrokes overwrite yours — are testable exhaustively.
 */

import type { Participant } from "./presence";

export const SHARED_INPUT_THROTTLE_MS = 300;
export const MAX_INPUT_LEN = 200;

export interface InputEntry {
  value: string;
  by: string;
}

export function mergeSections(
  remote: Record<string, boolean> | undefined,
  defaults: Record<string, boolean>,
): Record<string, boolean> {
  const out = { ...defaults };
  for (const [id, value] of Object.entries(remote ?? {})) {
    // A malformed remote value must not render the panel `undefined`.
    if (typeof value === "boolean") out[id] = value;
  }
  return out;
}

export function shouldAdoptInput(args: {
  focused: boolean;
  remoteBy: string;
  selfUid: string | null;
  remoteValue: string;
  localValue: string;
}): boolean {
  const { focused, remoteBy, selfUid, remoteValue, localValue } = args;
  // A focused field is never overwritten: you are mid-word.
  if (focused) return false;
  if (!remoteBy || remoteBy === selfUid) return false;
  return remoteValue !== localValue;
}

export function typistLabel(
  entry: InputEntry | undefined,
  selfUid: string | null,
  participants: Participant[],
): { name: string; color: string } | null {
  if (!entry?.by || entry.by === selfUid) return null;
  const who = participants.find((p) => p.uid === entry.by);
  // A writer who has left should not keep their name on the field.
  return who ? { name: who.name, color: who.color } : null;
}
```

- [ ] **Step 4: Verify.** Tests pass, `npm run build`, lint count unchanged.

- [ ] **Step 5: Mutation-verify.** Copy first, then:
1. `shouldAdoptInput` → drop the `focused` check → the focused test must fail.
2. → drop the `remoteBy === selfUid` check → the echo test must fail.
3. `typistLabel` → return a label for an unknown uid → the departed-writer test must fail.
4. `mergeSections` → accept any remote value → the non-boolean test must fail.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/sharedView.ts frontend/src/lib/sharedView.test.ts
git commit -m "feat(dashboard): pure shared view state rules"
```

## Task 2: The shared-view subscription

**Files:** `frontend/src/hooks/useSharedView.ts`

One document, one listener, provided by context. Follow the shape of `usePresence.ts` — same auth gating (`shouldPublish`), same server-timestamp discipline, same "writes nothing when not publishing".

- [ ] **Step 1: Implement.**

- A context providing `{ sections, inputs, setSection, setInput, publishing, selfUid, participants }`.
- `SharedViewProvider` subscribes to `doc(db, "presence", sessionCode, "shared", "view")` only when publishing; otherwise holds local-only state and returns no-op writers.
- `setSection(id, open)` → `setDoc(ref, { sections: { [id]: open }, updatedAt: serverTimestamp() }, { merge: true })`. **Use a nested merge**, so two people toggling different panels do not clobber each other — verify that `setDoc` with `merge: true` merges nested maps here rather than replacing `sections` wholesale; if it replaces, use `updateDoc` with a dotted field path (`` `sections.${id}` ``) and say which you used.
- `setInput(id, value, uid)` → throttled to `SHARED_INPUT_THROTTLE_MS`, value truncated to `MAX_INPUT_LEN`, same nested-merge consideration.
- Takes `participants` from `usePresence`'s existing subscription rather than opening its own — check how `PresenceLayer` gets them and thread it, or lift as needed. Do **not** open a second participants listener.

- [ ] **Step 2: Verify.** Build, tests, lint count.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/hooks/useSharedView.ts
git commit -m "feat(dashboard): shared view subscription"
```

## Task 3: The two consumer hooks and the chip

**Files:** `frontend/src/hooks/useSharedSection.ts`, `frontend/src/hooks/useSharedInput.ts`, `frontend/src/components/TypistChip.tsx`

- [ ] **Step 1: `useSharedSection(id, defaultOpen)`** returns `[boolean, (v: boolean) => void]`, identical in shape to `useState<boolean>` so each panel changes one line. When not publishing it *is* plain local state. When publishing, the value comes from the shared document (falling back to `defaultOpen`) and the setter writes through.

- [ ] **Step 2: `useSharedInput(id, value, setValue)`** returns `{ onFocus, onBlur, typist, adopted }`.
  - Tracks focus locally.
  - On local change (caller still owns the input's `onChange`), publishes throttled — the caller signals local typing by calling the returned `publish(value)`; decide the exact API and document it.
  - On remote change, consults `shouldAdoptInput` and, when true, calls `setValue(remoteValue)` **and sets an `adopted` ref the caller can read to suppress side effects**.
  - `typist` comes from `typistLabel`.

- [ ] **Step 3: `TypistChip.tsx`** — `{ typist: { name, color } | null }`, returns null when null, otherwise a small pill: coloured dot plus `{name} is typing`.

- [ ] **Step 4: Verify.** Build, tests, lint count.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/hooks/useSharedSection.ts frontend/src/hooks/useSharedInput.ts frontend/src/components/TypistChip.tsx
git commit -m "feat(dashboard): shared section and input hooks"
```

## Task 4: Wire the seven panels

**Files:** `ActivityLog.tsx`, `CommandHistory.tsx`, `MusicHistory.tsx`, `StatsPanel.tsx`, `PlaylistManager.tsx`, `HistoryPanel.tsx`, `Queue.tsx`

- [ ] **Step 1:** In each, replace `const [expanded, setExpanded] = useState(<default>);` with `const [expanded, setExpanded] = useSharedSection("<id>", <default>);` using a stable id per panel (`activity`, `commands`, `music`, `stats`, `playlists`, `history`, `queue`).

**`Queue.tsx` defaults to `true`; the other six default to `false`.** Getting that wrong silently collapses the queue for everyone.

- [ ] **Step 2:** Confirm each panel still works when not in shared mode — the hook must behave exactly like `useState` there.

- [ ] **Step 3: Verify.** Build, tests, lint count.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/{ActivityLog,CommandHistory,MusicHistory,StatsPanel,PlaylistManager,HistoryPanel,Queue}.tsx
git commit -m "feat(dashboard): sync panel expand and collapse"
```

## Task 5: The search input, and the loop guard

**Files:** `frontend/src/components/SearchPanel.tsx`

**This is the task with the real hazard.** `SearchPanel` fires a search whenever `query` changes. Without a guard:

> Ada types → publishes → Bob adopts → **Bob's debounce fires a search** → Bob writes `searchQuery` to the shared server doc → the bot answers Bob's duplicate → and if adoption also republished, it ping-pongs.

- [ ] **Step 1:** Wire `useSharedInput("search", query, setQuery)`; put `onFocus`/`onBlur` on the `Input` and publish on local change only (inside the existing `onChange`, alongside `setQuery`).

- [ ] **Step 2: The guard.** The debounce effect must skip scheduling a search when the change came from adoption. Consume the `adopted` flag and clear it. An adopted value must **not** be republished either.

- [ ] **Step 3: Render `<TypistChip typist={typist} />`** beside the search input.

- [ ] **Step 4: Prove the guard.** This is behavioural and lives in a component, so extract whatever decides "should this change schedule a search" into a tiny pure function in `sharedView.ts` and test it, rather than leaving the guard untested. Mutation-verify by making adoption look like local typing — a test must fail.

- [ ] **Step 5: Verify.** Build, tests, lint count.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/SearchPanel.tsx frontend/src/lib/sharedView.ts frontend/src/lib/sharedView.test.ts
git commit -m "feat(dashboard): share the search field without amplifying searches"
```

## Task 6: Provider, rules, verification

**Files:** `frontend/src/components/Dashboard.tsx`, `firestore.rules`

- [ ] **Step 1:** Mount `SharedViewProvider` in `Dashboard` around the panels, fed by `mode`, `user`, `sessionCode`, and the participants list. Presence updates must still not re-render the panels — check that adding the provider does not undo the `PresenceLayer` isolation; if the provider would re-render everything on every cursor tick, keep participants out of its value or memoize.

- [ ] **Step 2: Rules.** Add beside the participants rule:

```
    // Collective view state: which panels are open and what is in the shared
    // text fields. Deliberately NOT uid-scoped — this is a shared control
    // surface, like the queue, so any authenticated participant may write it.
    // The shape is still constrained, and updatedAt is server-stamped for the
    // same reason as presence: a client-set timestamp cannot be trusted.
    match /presence/{sessionCode}/shared/view {
      allow read: if request.auth != null;
      allow write: if request.auth != null
        && request.resource.data.keys().hasOnly(['sections', 'inputs', 'updatedAt'])
        && request.resource.data.updatedAt == request.time;
    }
```

Add size limits: at most ~20 sections, at most ~10 inputs, each input `value` a string under 200 characters and `by` a string under 128. Write them as rule functions like the existing `validPhoto`/`validCursor`, and **verify the whole file still compiles** with `firebase deploy --only firestore:rules --dry-run`.

Note the existing participants rule matches `presence/{sessionCode}/participants/{uid}`; confirm the new path cannot be captured by it or by the default-deny, and that `{sessionCode}/shared/view` is a valid collection/document alternation.

- [ ] **Step 3: Verify in the browser.** `preview_start` the frontend (`.claude/launch.json` has `autoPort`). The dashboard needs a live session to render, so at minimum: confirm the app builds and loads with no console errors, and add component tests for `TypistChip` and for `useSharedSection`'s local-mode behavior.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/components/Dashboard.tsx firestore.rules
git commit -m "feat(dashboard): shared view provider and rules"
```

## Task 7: Docs, merge, deploy

- [ ] **Step 1:** Extend the "Web app: collaborative dashboard" section of `docs/architecture/ARCHITECTURE.md`: what syncs, the focused-input conflict rule, and the loop guard with the reason it exists.

- [ ] **Step 2: Merge and deploy.**

```bash
git checkout master && git merge --no-ff feat/shared-view-state -m "Merge feat/shared-view-state: sync panels and text inputs" && git push origin master
firebase deploy --only firestore:rules
cd frontend && npm run build && cd .. && firebase deploy --only hosting
```

- [ ] **Step 3: Verify live.** Load the deployed dashboard; confirm no console errors.
