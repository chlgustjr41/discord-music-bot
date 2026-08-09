import { EventEmitter } from "node:events";
import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  spawnMock: vi.fn(),
  resolveMock: vi.fn<() => string | null>(() => "ffmpeg"),
  voiceCommand: vi.fn(),
}));

vi.mock("node:child_process", () => ({
  spawn: (...args: unknown[]) => h.spawnMock(...args),
}));
vi.mock("../src/ffmpeg-path", () => ({ resolveFfmpeg: () => h.resolveMock() }));
vi.mock("../src/pi-bridge", () => ({ handlePiEvent: vi.fn() }));
vi.mock("../src/runtime", () => ({
  getClient: () => ({ voiceCommand: h.voiceCommand }),
}));
// The real module opens a websocket to the Stream Deck host on import.
vi.mock("@elgato/streamdeck", () => ({
  action: () => (target: unknown) => target,
  SingletonAction: class {},
}));

const { Voice } = await import("../src/actions/voice");

type DownEv = Parameters<InstanceType<typeof Voice>["onKeyDown"]>[0];
type UpEv = Parameters<InstanceType<typeof Voice>["onKeyUp"]>[0];
type GoneEv = Parameters<InstanceType<typeof Voice>["onWillDisappear"]>[0];

class FakeProc extends EventEmitter {
  stdout = new EventEmitter();
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
    /** Let the nth pending getSettings() resolve. */
    settle: (n = 0) => waiting[n]({}),
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
  h.voiceCommand.mockReset().mockResolvedValue({
    transcript: "skip",
    actions: [{ action: "skip", ok: true, detail: "Skipped" }],
    ok: true,
    detail: "Skipped",
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
