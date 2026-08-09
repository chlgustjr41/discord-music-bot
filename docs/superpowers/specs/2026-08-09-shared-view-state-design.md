# Shared View State — Sections and Text Inputs — Design

**Date:** 2026-08-09
**Status:** Approved
**Scope:** Extends the shared dashboard view (presence + cursors, shipped earlier today) so that panel expand/collapse and text input contents are synchronised between shared-mode participants, with attribution for who is typing. Touches `frontend/` and `firestore.rules` only.

## Problem

Presence and cursors tell you *where* someone is pointing but nothing about what they are doing. Two people on the same dashboard still see different screens: one has the queue collapsed and history open, the other the reverse, and neither can see what the other is typing into the search box until results appear.

Two additions:

1. **Panel expand/collapse** syncs across shared-mode participants.
2. **Text inputs** sync live, with a visible indication of who is typing.

## What already exists

- `presence/{sessionCode}/participants/{uid}` — per-user presence and cursor, server-stamped, uid-scoped writes.
- `src/lib/presence.ts` — pure logic, `shouldPublish(mode, signedIn)` as the single auth gate.
- `PresenceLayer` owns `usePresence`, deliberately outside `Dashboard` so cursor ticks do not re-render the panels.
- Seven panels share an identical seam: `const [expanded, setExpanded] = useState(<default>)` in `ActivityLog`, `CommandHistory`, `MusicHistory`, `StatsPanel`, `PlaylistManager`, `HistoryPanel`, and `Queue` (which defaults open; the rest default closed).
- `SearchPanel` holds `query` in local state and debounces a search 200 ms after typing stops.

## Decisions

| Question | Decision |
|---|---|
| Where shared view state lives | One document, `presence/{sessionCode}/shared/view` — collective state, unlike the per-user participant docs. |
| Who may write it | Any signed-in participant. This is a shared control surface, like the queue; it is not owned by one person. |
| Section sync shape | `sections: { [panelId]: boolean }`, merged per field so two people toggling different panels never clobber each other. |
| Input sync shape | `inputs: { [inputId]: { value, by, byName } }` — the value plus who last set it, so the UI can attribute it. |
| Conflict rule for inputs | **A focused input is never overwritten.** You adopt a remote value only when you are not typing in that field. Two people typing at once each keep their own text until they blur, rather than fighting mid-word. |
| Attribution | A small chip beside the input — "Ada is typing" — in that person's presence colour, shown only when the last writer was someone else and the value is recent. |
| Who participates | Shared mode and signed in, exactly as presence. Solo and anonymous users keep purely local state and write nothing. |
| Timestamps | `serverTimestamp()` with a rule requiring `== request.time`, for the same reason as presence: a client-set value cannot be trusted. |

## The loop hazard, and why it shapes the design

`SearchPanel` fires a search whenever `query` changes. If adopting a remote query went through the same path, this would happen:

> Ada types "radiohead" → publishes → Bob adopts → **Bob's debounce fires a search** → Bob writes `searchQuery` to the shared server doc → the bot answers Bob's duplicate request → and if Bob's adoption also republished the value, Ada adopts it back.

So adoption is explicitly inert: setting the input from a remote update must **not** schedule a search and must **not** republish. Only local typing does either. This is implemented with a ref flag consumed by the debounce effect, and it is the single most important correctness property of this feature — a search-per-keystroke-per-viewer amplification would be both a cost and a rate-limit problem.

## Components

### 1. `src/lib/sharedView.ts` (new, pure — tested)

- `mergeSections(remote, localDefaults)` — remote wins where present, defaults fill gaps.
- `shouldAdoptInput({ focused, remoteBy, selfUid, remoteValue, localValue })` — the conflict rule in one testable place. False when focused, false when the remote writer is you, false when the value is unchanged.
- `typistLabel(entry, selfUid, participants)` — resolves `by` to a display name and colour, or null when it is you or the writer has left.
- `SHARED_INPUT_THROTTLE_MS = 300`, `MAX_INPUT_LEN = 200`.

### 2. `src/hooks/useSharedView.ts` (new)

Subscribes to `presence/{code}/shared/view` and exposes `{ sections, inputs, setSection, setInput }`. Writes nothing when not publishing. One subscription for the whole dashboard, provided through a small context so seven panels do not open seven listeners.

### 3. `src/hooks/useSharedSection.ts` (new)

`useSharedSection(id, defaultOpen)` returning `[expanded, setExpanded]` — a drop-in replacement for `useState<boolean>`, so each panel changes exactly one line. Falls back to plain local state when not in shared mode.

### 4. `src/hooks/useSharedInput.ts` (new)

`useSharedInput(id, value, setValue)` returning `{ onFocus, onBlur, typist }`. Publishes throttled while focused; adopts when not focused per `shouldAdoptInput`; returns the typist for the attribution chip. Adoption sets a flag the caller can read so it can suppress side effects.

### 5. `src/components/TypistChip.tsx` (new)

Name plus colour dot, rendered beside an input when someone else is typing in it.

### 6. Modified

Seven panels swap `useState` for `useSharedSection`. `SearchPanel` adds `useSharedInput` for its query field plus the adoption guard on its debounce effect. `Dashboard` provides the shared-view context.

### 7. `firestore.rules`

```
match /presence/{sessionCode}/shared/view {
  allow read: if request.auth != null;
  allow write: if request.auth != null
    && request.resource.data.keys().hasOnly(['sections', 'inputs', 'updatedAt'])
    && request.resource.data.updatedAt == request.time;
}
```

with size limits on the maps and on each input value. Deliberately **not** uid-scoped: this is collective state.

## Security and privacy

- **Same auth gate.** Anonymous and solo users neither publish nor subscribe; `shouldPublish` remains the one place that decides.
- **Inputs are already-shared content.** A search query typed in shared mode was going to reach the shared server document a moment later anyway. Nothing newly private is exposed — but the spec is explicit that in shared mode *what you type is visible before you submit it*, and that is what the toggle is for.
- **Bounded writes.** Input length capped at 200 characters in the rules and truncated client-side; throttled to 300 ms; sections are user-driven and rare.
- **No new impersonation surface.** `by` is a uid resolved against the presence list for display; a forged `byName` cannot be written because the rules constrain the shape and the UI reads names from presence, not from the input document.

## Error handling

| Condition | Result |
|---|---|
| Not signed in, or solo | Local state only; nothing published or subscribed |
| Shared-view listener fails | Panels fall back to local state; dashboard unaffected |
| Remote input arrives while you are typing in that field | Ignored until you blur |
| Remote input adopted | Value set, **no search fired, no republish** |
| Writer leaves mid-type | Chip disappears; the value stays until someone changes it |
| Two people toggle different panels at once | Both apply — merged per field |
| Two people toggle the same panel at once | Last write wins; both converge |

## Testing

- **`sharedView.ts`:** `shouldAdoptInput` false when focused, false when the writer is you, false on an unchanged value, true otherwise; `mergeSections` remote-over-default and gap-filling; `typistLabel` null for self and for a departed writer.
- **Loop guard:** an adopted value must not schedule a search — pinned on the pure decision, and mutation-verified by making adoption look like local typing.
- **Panels:** each of the seven still opens and closes locally when not in shared mode.
- **Manual:** two accounts, two browsers — collapse a panel in one and watch the other; type in the search box and watch the text and the "is typing" chip appear; confirm typing in one does not fire two searches; toggle solo and confirm both stop.

## Out of scope

Cursor selection ranges inside inputs, per-character operational transform (last-writer-wins is sufficient at this scale), syncing scroll position, syncing dialogs, and inputs inside the playlist editor dialog — the main search field is the one that matters and the hook is reusable if the others are wanted later.
