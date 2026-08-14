# Jacky Control for Logitech MX Creative Console — Design

**Date:** 2026-08-14
**Status:** Approved
**Scope:** A second hardware plugin, `creative-console-plugin/`, mirroring the Stream Deck plugin's feature set on the Logi Actions SDK (C#/.NET 8). **No bot changes** — the `/control/*` API is plugin-agnostic.

## Platform facts the design rests on (verified against the SDK docs)

- **C# SDK, not Node.** The Node SDK is beta with a thin API. The C# SDK has the three load-bearing capabilities: **Action Editor** (per-action config UI with request-time listboxes — the Property Inspector equivalent), **`GetCommandImage` + `ActionImageChanged`** (runtime key images, 80×80 PNG, drawn with `BitmapBuilder`), and **`AddParameter`** (one command class fans out into several draggable actions).
- Toolchain: `dotnet tool install --global LogiPluginTool` → `logiplugintool generate JackyControl` → `dotnet build` drops a `.link` file into `%LOCALAPPDATA%\Logi\LogiPluginService\Plugins\` for live testing → `logiplugintool` packages a **`.lplug4`** for distribution/install.
- Commands receive **press only** (`RunCommand`); no documented release event. Adjustments receive `ApplyAdjustment(actionParameter, diff)` from dials/rollers.
- Host: Logi Options+ (or Loupedeck) + Logi Plugin Service, Windows and macOS.

## Feature mapping

| Feature | Form on the console |
|---|---|
| Sign in with Discord | `ActionEditorCommand`: press opens the browser at `/control/auth/start`'s authorize URL and polls for the token (direct port of `auth.ts`, same same-address binding, same error vocabulary). Key image reflects signed-in vs not. Editor holds an optional **Server URL** textbox (default `https://control.jacky-music-bot.com`). Token + URL live in plugin settings storage. |
| Play/Pause | Command with a **live image**: artwork (fetched via the same mqdefault rewrite + size cap rules) or glyph, current title drawn across the bottom, `⏸` marker while paused. Repainted per poll tick via `ActionImageChanged`. **Truncated two-line title, no marquee** — repainting through the plugin service every 400 ms is not worth a scroll effect at 80 px. Press toggles play/pause. |
| Skip / Stop / Shuffle | Simple `PluginDynamicCommand`s, same endpoints. |
| Volume | **An Adjustment for the dial** (`diff × 5`, clamped 0–100, current volume drawn on the adjustment image) *plus* Volume +/− button commands. |
| Post to Discord | One parameterised command → four draggable actions: Session code / Now playing / Queue / Status → `POST /control/announce`. 429 renders "Just posted" on the key; `ok:false` renders the detail. |
| Play Playlist | `ActionEditorCommand`: Server + Playlist listboxes populated from `/control/playlists` when the editor opens. |
| Summon | `ActionEditorCommand`: Server + Voice-channel listboxes from `/control/channels`. Press toggles join/leave as the Stream Deck key does. |
| Open Dashboard | Simple command → `/control/dashboard-url` → open in default browser. **Same `https:`-only URL guard as the Stream Deck plugin** — the server URL is user-overridable, so an opened URL is validated before `Process.Start`. |
| Voice Command | **Press-to-toggle** (press = start recording with a red recording key image; press again = stop and send; auto-stop at 15 s) because only press events are documented. Capture via **NAudio** (managed WASAPI, 16 kHz mono 16-bit WAV in memory — no bundled ffmpeg). Editor: Microphone listbox (NAudio device enumeration), Language listbox (the server's seven codes), "Print debug message to Discord" checkbox. Responses render as on the Stream Deck: 422 → "Didn't catch that", 400 `no-audio` → "No audio", detail otherwise. **Windows-only**; every other action is cross-platform. |

## Architecture

```
creative-console-plugin/
  JackyControlPlugin/            # the plugin assembly (net8.0)
    JackyControlPlugin.cs        # Plugin subclass: settings, shared services
    ControlApiClient.cs          # port of api-client.ts (HttpClient, bearer, typed errors)
    AuthFlow.cs                  # port of auth.ts (start → open browser → poll)
    SessionPoller.cs             # port of poller.ts (5s→30s backoff, subscribers)
    UrlGuard.cs                  # port of url-guard.ts (https-only, returns normalized)
    Thumbnails.cs                # mqdefault rewrite + size caps, HttpClient fetch
    Audio/MicRecorder.cs         # NAudio capture, device enumeration, 15s cap
    Actions/…                    # one file per action, as in the Stream Deck plugin
  JackyControlPlugin.Tests/      # xUnit; everything that needs no device
```

Shared services (client, poller, auth state) live on the `Plugin` subclass; actions reach them via `this.Plugin`. All server semantics — status codes, "Just posted", empty-content details, the announce cooldown, the voice contract — are already server-side and arrive for free.

## Security invariants carried over

- OAuth token stored in plugin settings, never logged; sign-in must start from this machine (server-enforced same-address binding — document it as in the Stream Deck runbook).
- Opened URLs (dashboard) pass the `https:`-only guard and the **checked string is the opened string**.
- Thumbnails only ever fetched over the existing size caps; no arbitrary-host guard needed beyond what the server sends (same posture as the Stream Deck plugin).
- Voice: audio only in memory, transcripts never logged by the plugin.

## Testing

- **xUnit, no device:** `ControlApiClient` against a fake `HttpMessageHandler` (routes, bearer header, error mapping incl. 429/422/400-`no-audio`); `AuthFlow` polling (success, denial, timeout, unsafe authorize URL rejected); `SessionPoller` backoff and subscriber lifecycle; `UrlGuard` (the Stream Deck test matrix ported); `Thumbnails` URL rewriting; title truncation logic.
- **Mutation discipline as everywhere in this repo:** each guard proven by breaking it.
- **On-device:** not possible here — the machine has no console attached. Build + `logiplugintool` package verification happen locally; key rendering, dial feel, and Action Editor layout are the user's manual pass, with a checklist in the docs.

## Deliverables

The `.lplug4` package, `docs/creative-console-control.md` (setup + behaviour, mirroring `docs/streamdeck-control.md`), and the source under `creative-console-plugin/`.

## Out of scope

macOS voice capture, Marketplace submission, haptics, dynamic folders, and any bot-side change.
