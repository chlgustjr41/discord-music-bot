import { EventEmitter } from "node:events";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ControlApiError } from "../src/api-client";

const h = vi.hoisted(() => ({
  spawnMock: vi.fn(),
  resolveMock: vi.fn<() => string | null>(() => "ffmpeg"),
  resolveDevice: vi.fn<(d?: string) => Promise<string | null>>(),
  voiceCommand: vi.fn(),
  openUrl: vi.fn(async (_url: string) => {}),
}));

vi.mock("node:child_process", () => ({
  spawn: (...args: unknown[]) => h.spawnMock(...args),
}));
vi.mock("../src/ffmpeg-path", () => ({ resolveFfmpeg: () => h.resolveMock() }));
vi.mock("../src/audio-devices", () => ({
  resolveInputDevice: (d?: string) => h.resolveDevice(d),
}));
vi.mock("../src/pi-bridge", () => ({ handlePiEvent: vi.fn() }));
vi.mock("../src/runtime", () => ({
  getClient: () => ({ voiceCommand: h.voiceCommand }),
}));
// The real module opens a websocket to the Stream Deck host on import.
// `logger` is stubbed because the voice path writes to it — a bare default
// would throw at import time on createScope.
vi.mock("@elgato/streamdeck", () => {
  const logger = {
    createScope: () => logger,
    trace: vi.fn(),
    debug: vi.fn(),
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
  };
  return {
    default: { logger, system: { openUrl: (url: string) => h.openUrl(url) } },
    action: () => (target: unknown) => target,
    SingletonAction: class {},
  };
});

const { Voice } = await import("../src/actions/voice");

type DownEv = Parameters<InstanceType<typeof Voice>["onKeyDown"]>[0];
type UpEv = Parameters<InstanceType<typeof Voice>["onKeyUp"]>[0];
type GoneEv = Parameters<InstanceType<typeof Voice>["onWillDisappear"]>[0];

class FakeProc extends EventEmitter {
  stdout = new EventEmitter();
  stderr = new EventEmitter();
  stdin = { write: vi.fn() };
  kill = vi.fn();
}

/** A key whose getSettings() is deferred on purpose: every ordering bug in
 *  this action lives inside that await, so the tests must control when it
 *  resolves rather than hoping to win a race. */
function fakeKey(id: string) {
  const waiting: Array<(v: unknown) => void> = [];
  const stub = {
    id,
    setTitle: vi.fn(async () => {}),
    showAlert: vi.fn(async () => {}),
    showOk: vi.fn(async () => {}),
    getSettings: vi.fn(() => new Promise((resolve) => waiting.push(resolve))),
  };
  const ev = { action: stub } as unknown;
  return {
    action: stub,
    down: ev as DownEv,
    up: ev as UpEv,
    gone: ev as GoneEv,
    /** Let the nth pending getSettings() resolve, optionally with settings. */
    settle: (n = 0, settings: Record<string, unknown> = {}) => waiting[n](settings),
  };
}

let procs: FakeProc[];

beforeEach(() => {
  procs = [];
  h.spawnMock.mockReset().mockImplementation(() => {
    const p = new FakeProc();
    procs.push(p);
    return p;
  });
  h.resolveMock.mockReset().mockReturnValue("ffmpeg");
  h.resolveDevice
    .mockReset()
    .mockImplementation(async (d?: string) => d || "Microphone (Yeti GX)");
  h.openUrl.mockReset().mockResolvedValue(undefined);
  h.voiceCommand.mockReset().mockResolvedValue({
    transcript: "skip",
    actions: [{ action: "skip", ok: true, detail: "Skipped" }],
    ok: true,
    detail: "Skipped",
    client: [],
  });
});

describe("Voice key lifecycle", () => {
  it("never opens the mic when the key is released during the settings round-trip", async () => {
    // Otherwise ffmpeg starts with no key left to stop it and the mic stays
    // open until the 15 s cap — the spec promises release on key-up.
    const v = new Voice();
    const k = fakeKey("key-1");

    const down = v.onKeyDown(k.down);
    await v.onKeyUp(k.up);
    k.settle();
    await down;

    expect(h.spawnMock).not.toHaveBeenCalled();
  });

  it("never opens the mic for two complete taps during the round-trips", async () => {
    // Two presses are outstanding at once, so a single pending-stop bit cannot
    // represent them: the second press would consume a flag already cleared by
    // the first and spawn with no key down.
    const v = new Voice();
    const k = fakeKey("key-1");

    const down1 = v.onKeyDown(k.down);
    await v.onKeyUp(k.up);
    const down2 = v.onKeyDown(k.down);
    await v.onKeyUp(k.up);
    k.settle(0);
    k.settle(1);
    await Promise.all([down1, down2]);

    expect(h.spawnMock).not.toHaveBeenCalled();
  });

  it("never orphans a recorder when the key disappears during the round-trip", async () => {
    // onWillDisappear drops the state object onKeyDown captured before its
    // await; without an identity re-check the spawned recorder lands in that
    // unreachable state and no later key-up can reach it.
    const v = new Voice();
    const k = fakeKey("key-1");

    const down = v.onKeyDown(k.down);
    v.onWillDisappear(k.gone);
    k.settle();
    await down;

    expect(h.spawnMock).not.toHaveBeenCalled();
    // And the key is not left dead: a fresh press still records.
    const down2 = v.onKeyDown(k.down);
    k.settle(1);
    await down2;
    expect(h.spawnMock).toHaveBeenCalledTimes(1);
  });

  it("releases the mic when a held key disappears", async () => {
    const v = new Voice();
    const k = fakeKey("key-1");
    const down = v.onKeyDown(k.down);
    k.settle();
    await down;
    expect(procs).toHaveLength(1);

    v.onWillDisappear(k.gone);

    // "q" is the graceful quit that closes the device.
    expect(procs[0].stdin.write).toHaveBeenCalledWith("q");
  });

  it("gives each key its own recorder and its own audio state", async () => {
    // SingletonAction is one instance for every key of this type.
    const v = new Voice();
    const a = fakeKey("key-a");
    const b = fakeKey("key-b");

    const downA = v.onKeyDown(a.down);
    const downB = v.onKeyDown(b.down);
    a.settle();
    b.settle();
    await Promise.all([downA, downB]);
    expect(procs).toHaveLength(2);

    // Only key A's microphone ever delivers audio.
    procs[0].stdout.emit("data", Buffer.alloc(2000, 1));

    const upB = v.onKeyUp(b.up);
    procs[1].emit("close");
    await upB;
    expect(b.action.setTitle).toHaveBeenCalledWith("Hold\nlonger");

    const upA = v.onKeyUp(a.up);
    procs[0].emit("close");
    await upA;
    expect(a.action.setTitle).not.toHaveBeenCalledWith("Hold\nlonger");
    expect(a.action.setTitle).toHaveBeenCalledWith("Skipped");
  });

  it("does not let a second key's press discard the first key's heard audio", async () => {
    // The only observable symptom of a SHARED heardAudio flag: the guard is
    // `!heardAudio || wav.length < 1000`, so a wrongly-true flag is always
    // caught by the byte count. A wrongly-false one is not — pressing B while
    // A is recording would reset it and report A's good recording as too
    // short. Ordering is the whole test: A must hear audio BEFORE B is pressed.
    const v = new Voice();
    const a = fakeKey("key-a");
    const b = fakeKey("key-b");

    const downA = v.onKeyDown(a.down);
    a.settle();
    await downA;
    procs[0].stdout.emit("data", Buffer.alloc(2000, 1));

    const downB = v.onKeyDown(b.down);
    b.settle();
    await downB;

    const upA = v.onKeyUp(a.up);
    procs[0].emit("close");
    await upA;

    expect(a.action.setTitle).not.toHaveBeenCalledWith("Hold\nlonger");
    expect(a.action.setTitle).toHaveBeenCalledWith("Skipped");
  });

  it("says the binary is missing rather than blaming the hold length", async () => {
    const v = new Voice();
    const k = fakeKey("key-1");
    const down = v.onKeyDown(k.down);
    k.settle();
    await down;

    procs[0].emit("error", new Error("spawn ffmpeg ENOENT"));
    await v.onKeyUp(k.up);

    expect(k.action.setTitle).toHaveBeenCalledWith("No\nffmpeg");
    expect(k.action.setTitle).not.toHaveBeenCalledWith("Hold\nlonger");
  });
});

describe("Voice microphone resolution", () => {
  /** Hold the key down with the given settings, letting the settings and the
   *  device lookup both settle. */
  async function press(settings: Record<string, unknown> = {}) {
    const v = new Voice();
    const k = fakeKey("key-1");
    const down = v.onKeyDown(k.down);
    k.settle(0, settings);
    await down;
    return { v, k };
  }

  it("records from the key's configured microphone", async () => {
    await press({ inputDevice: "Microphone (Yeti GX)" });
    expect(h.spawnMock.mock.calls[0][1]).toContain("audio=Microphone (Yeti GX)");
  });

  it("auto-picks a real device when the key has none configured", async () => {
    // Not "audio=default": there is no such DirectShow device, so ffmpeg used
    // to spawn, fail instantly, and hand the key zero bytes.
    h.resolveDevice.mockResolvedValue("Microphone (3- Logitech G733 Gaming Headset)");
    await press({});
    expect(h.resolveDevice).toHaveBeenCalledWith(undefined);
    expect(h.spawnMock.mock.calls[0][1]).toContain(
      "audio=Microphone (3- Logitech G733 Gaming Headset)",
    );
    expect(h.spawnMock.mock.calls[0][1].join(" ")).not.toContain("audio=default");
  });

  it("says there is no microphone instead of spawning against a name that cannot exist", async () => {
    h.resolveDevice.mockResolvedValue(null);
    const { k } = await press({});
    expect(h.spawnMock).not.toHaveBeenCalled();
    expect(k.action.setTitle).toHaveBeenCalledWith("No\nmic");
    expect(k.action.showAlert).toHaveBeenCalled();
  });

  it("never opens the mic when the key is released during the device lookup", async () => {
    // Same promise as the settings round-trip: the mic is open only while the
    // key is held, and this await is a second window for a tap to slip through.
    let release: ((d: string | null) => void) | null = null;
    h.resolveDevice.mockImplementation(
      () => new Promise<string | null>((r) => (release = r)),
    );
    const v = new Voice();
    const k = fakeKey("key-1");
    const down = v.onKeyDown(k.down);
    k.settle();
    // The press must be PAST the settings guard and inside the lookup, or the
    // first guard would catch it and this would prove nothing about the second.
    await new Promise((r) => setTimeout(r, 0));
    expect(h.resolveDevice).toHaveBeenCalled();

    await v.onKeyUp(k.up);
    release!("Microphone (Yeti GX)");
    await down;

    expect(h.spawnMock).not.toHaveBeenCalled();
  });

  it("says the mic failed rather than blaming the hold length", async () => {
    // ffmpeg started and then died on its own — the failure that used to be
    // indistinguishable from a tap, because both end at zero bytes.
    const { v, k } = await press({ inputDevice: "Gone" });
    procs[0].stderr.emit("data", Buffer.from("Could not find audio only device"));
    procs[0].emit("close", 1);
    await v.onKeyUp(k.up);

    expect(k.action.setTitle).toHaveBeenCalledWith("Mic\nerror");
    expect(k.action.setTitle).not.toHaveBeenCalledWith("Hold\nlonger");
    expect(k.action.setTitle).not.toHaveBeenCalledWith("No\nffmpeg");
  });

  it("still says to hold longer for a genuinely short press", async () => {
    const { v, k } = await press({ inputDevice: "Yeti" });
    const up = v.onKeyUp(k.up);
    procs[0].emit("close", 0);
    await up;

    expect(k.action.setTitle).toHaveBeenCalledWith("Hold\nlonger");
    expect(k.action.setTitle).not.toHaveBeenCalledWith("Mic\nerror");
  });
});

describe("Voice client directives", () => {
  /** One complete hold-and-release that reaches the server round-trip, so the
   *  directive loop actually runs. */
  async function speak(client: unknown) {
    h.voiceCommand.mockResolvedValue({
      transcript: "open the dashboard",
      actions: [{ action: "open_dashboard", ok: true, detail: "Opening" }],
      ok: true,
      detail: "Opening",
      client,
    });
    const v = new Voice();
    const k = fakeKey("key-1");
    const down = v.onKeyDown(k.down);
    k.settle();
    await down;
    procs[0].stdout.emit("data", Buffer.alloc(2000, 1));
    const up = v.onKeyUp(k.up);
    procs[0].emit("close");
    await up;
    return k;
  }

  it("opens the browser for an https open_url directive", async () => {
    await speak([{ type: "open_url", url: "https://web.test/dashboard/CODE1" }]);
    expect(h.openUrl).toHaveBeenCalledWith("https://web.test/dashboard/CODE1");
  });

  it("refuses a javascript: directive url", async () => {
    // The escalation the guard exists for: this executes locally rather than
    // navigating. Without the isOpenableUrl call this test is the only thing
    // standing between a hostile server and the user's machine.
    await speak([{ type: "open_url", url: "javascript:alert(1)" }]);
    expect(h.openUrl).not.toHaveBeenCalled();
  });

  it("ignores a directive type it does not know", async () => {
    // The directive vocabulary is closed: an unknown type is dropped, not
    // dispatched to openUrl just because it happens to carry a url.
    await speak([{ type: "run_command", url: "https://x.test" }]);
    expect(h.openUrl).not.toHaveBeenCalled();
  });

  it("still renders the detail when there are no directives", async () => {
    const k = await speak([]);
    expect(h.openUrl).not.toHaveBeenCalled();
    expect(k.action.setTitle).toHaveBeenCalledWith("Opening");
  });

  it("reports success when `client` is not an array at all", async () => {
    // The response is an unvalidated cast, so a non-array `client` reaches
    // for-of and throws — landing in the outer catch, which overwrites the
    // already-rendered detail with "Failed" and alerts. The command SUCCEEDED
    // server-side, so a false failure just makes the user run it again.
    const k = await speak({ type: "open_url" });
    expect(h.openUrl).not.toHaveBeenCalled();
    expect(k.action.setTitle).not.toHaveBeenCalledWith("Failed");
    expect(k.action.setTitle).toHaveBeenCalledWith("Opening");
  });

  it("survives a null `client`", async () => {
    const k = await speak(null);
    expect(k.action.setTitle).not.toHaveBeenCalledWith("Failed");
    expect(k.action.setTitle).toHaveBeenCalledWith("Opening");
  });

  it("does not open a directive whose url is an array", async () => {
    // new URL(["https://x"]) stringifies the array and would otherwise pass
    // the guard — but openUrl wants a string, not whatever this is.
    await speak([{ type: "open_url", url: ["https://web.test/x"] }]);
    expect(h.openUrl).not.toHaveBeenCalled();
  });

  it("skips a malformed directive without dropping the ones after it", async () => {
    await speak([
      null,
      { type: "open_url", url: 42 },
      { type: "open_url", url: "https://web.test/ok" },
    ]);
    expect(h.openUrl).toHaveBeenCalledWith("https://web.test/ok");
  });
});

describe("Voice language and failure reporting", () => {
  /** One complete hold-and-release that reaches the server round-trip, with
   *  the given per-key settings answering getSettings(). */
  async function speakWith(settings: Record<string, unknown>) {
    const v = new Voice();
    const k = fakeKey("key-1");
    const down = v.onKeyDown(k.down);
    k.settle(0, settings);
    await down;
    procs[0].stdout.emit("data", Buffer.alloc(2000, 1));
    const up = v.onKeyUp(k.up);
    procs[0].emit("close");
    await up;
    return k;
  }

  it("sends the key's language setting with the recording", async () => {
    await speakWith({ inputDevice: "Mic", language: "ko" });
    expect(h.voiceCommand).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ language: "ko" }),
    );
  });

  it("sends no language when the key has none stored", async () => {
    // An older key predates the setting; it must not send an empty code.
    await speakWith({});
    expect(h.voiceCommand).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ language: undefined }),
    );
  });

  it("passes the key's debug option through to the request", async () => {
    await speakWith({ language: "ko", debug: true });
    expect(h.voiceCommand).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ language: "ko", debug: true }),
    );
  });

  it("asks for no debug echo when the key never enabled it", async () => {
    // Default off: the echo publishes the transcript to a channel other people
    // can read, so an unconfigured key must not opt itself in.
    await speakWith({ language: "ko" });
    const opts = h.voiceCommand.mock.calls.at(-1)?.[1] as { debug?: boolean };
    expect(opts.debug).toBeFalsy();
  });

  it("says it did not catch the words rather than that it failed, on 422", async () => {
    // 422 is the server saying "nothing was resolvable, nothing ran" — the one
    // failure the user fixes by speaking again rather than by checking the bot.
    h.voiceCommand.mockRejectedValue(new ControlApiError(422));
    const k = await speakWith({});
    expect(k.action.setTitle).toHaveBeenCalledWith("Didn't\ncatch that");
    expect(k.action.setTitle).not.toHaveBeenCalledWith("Failed");
    expect(k.action.showAlert).toHaveBeenCalled();
  });

  it("still reports a plain failure for a server error", async () => {
    h.voiceCommand.mockRejectedValue(new ControlApiError(500));
    const k = await speakWith({});
    expect(k.action.setTitle).toHaveBeenCalledWith("Failed");
    expect(k.action.setTitle).not.toHaveBeenCalledWith("Didn't\ncatch that");
  });

  it("reports a plain failure when the request never reached the server", async () => {
    h.voiceCommand.mockRejectedValue(new Error("network down"));
    const k = await speakWith({});
    expect(k.action.setTitle).toHaveBeenCalledWith("Failed");
    expect(k.action.setTitle).not.toHaveBeenCalledWith("Didn't\ncatch that");
  });
});
