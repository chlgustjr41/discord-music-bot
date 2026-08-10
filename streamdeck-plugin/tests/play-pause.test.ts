import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PollState } from "../src/poller";

const h = vi.hoisted(() => ({
  playPause: vi.fn(async (...args: unknown[]): Promise<unknown> => args),
  loadThumbnail: vi.fn(async (...args: unknown[]): Promise<string | null> => args[0] as string),
  subs: new Set<(s: PollState) => void>(),
}));

vi.mock("../src/pi-bridge", () => ({ handlePiEvent: vi.fn() }));
vi.mock("../src/thumbnail", () => ({
  loadThumbnail: (...args: unknown[]) => h.loadThumbnail(...args),
}));
vi.mock("../src/runtime", () => ({
  getClient: () => ({ playPause: h.playPause }),
  poller: {
    subscribe: (cb: (s: PollState) => void) => h.subs.add(cb),
    unsubscribe: (cb: (s: PollState) => void) => h.subs.delete(cb),
  },
}));
// The real module opens a websocket to the Stream Deck host on import.
vi.mock("@elgato/streamdeck", () => ({
  default: {},
  action: () => (target: unknown) => target,
  SingletonAction: class {},
}));

const { PlayPause } = await import("../src/actions/play-pause");
type Settings = { showTitle?: boolean; showArtwork?: boolean };
type AppearEv = Parameters<InstanceType<typeof PlayPause>["onWillAppear"]>[0];
type GoneEv = Parameters<InstanceType<typeof PlayPause>["onWillDisappear"]>[0];
type SettingsEv = Parameters<InstanceType<typeof PlayPause>["onDidReceiveSettings"]>[0];

function fakeKey(id: string) {
  return {
    id,
    isKey: () => true,
    setTitle: vi.fn(async (...args: unknown[]) => args),
    setImage: vi.fn(async (...args: unknown[]) => args),
    setState: vi.fn(async (...args: unknown[]) => args),
    showOk: vi.fn(async (...args: unknown[]) => args),
    showAlert: vi.fn(async (...args: unknown[]) => args),
  };
}
type FakeKey = ReturnType<typeof fakeKey>;

/** Mount keys on the shared SingletonAction instance the way the host does:
 *  `this.actions` yields every visible key, and each arrives with its own
 *  settings payload. */
function mount(pp: InstanceType<typeof PlayPause>, keys: [FakeKey, Settings][]) {
  (pp as unknown as { actions: FakeKey[] }).actions = keys.map(([k]) => k);
  for (const [k, settings] of keys) {
    pp.onWillAppear({ action: k, payload: { settings } } as unknown as AppearEv);
  }
}

function emit(s: PollState): void {
  for (const cb of h.subs) cb(s);
}

function playing(title: string | null, thumbnail: string | null, paused = false): PollState {
  return {
    kind: "data",
    data: { active: true, title, author: "A", paused, volume: 50, guildName: "G", thumbnail },
  };
}

/** Let the thumbnail fetch promise chain settle. */
const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  h.subs.clear();
  h.playPause.mockReset().mockResolvedValue(undefined);
  h.loadThumbnail.mockReset().mockImplementation(async (...args: unknown[]) => args[0] as string);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("Play/Pause key, options off", () => {
  it("writes no title and no image at all", async () => {
    // The regression that matters: a key nobody reconfigured must behave
    // exactly as it did before the Now Playing merge — state glyph only.
    const pp = new PlayPause();
    const k = fakeKey("key-1");
    mount(pp, [[k, {}]]);

    emit(playing("A Very Long Song Title", "https://img.test/a.jpg", true));
    await flush();

    expect(k.setTitle).not.toHaveBeenCalled();
    expect(k.setImage).not.toHaveBeenCalled();
    expect(k.setState).toHaveBeenCalledWith(1);
  });

  it("still writes no title when there is no session", async () => {
    const pp = new PlayPause();
    const k = fakeKey("key-1");
    mount(pp, [[k, {}]]);

    emit({ kind: "offline" });
    emit({ kind: "data", data: { active: false } });
    await flush();

    expect(k.setTitle).not.toHaveBeenCalled();
    expect(k.setImage).not.toHaveBeenCalled();
  });

  it("still toggles playback on press", async () => {
    const pp = new PlayPause();
    const k = fakeKey("key-1");
    await pp.onKeyDown({ action: k } as never);

    expect(h.playPause).toHaveBeenCalledTimes(1);
    expect(k.showOk).toHaveBeenCalled();
  });
});

describe("Play/Pause key with showTitle", () => {
  it("marquees the track on a 400 ms clock, not the poll clock", () => {
    vi.useFakeTimers();
    const pp = new PlayPause();
    const k = fakeKey("key-1");
    mount(pp, [[k, { showTitle: true }]]);

    emit(playing("A Very Long Song Title", null));
    const first = k.setTitle.mock.calls.at(-1)?.[0];
    expect(first).toBe("A Very Lo");

    vi.advanceTimersByTime(400);
    const second = k.setTitle.mock.calls.at(-1)?.[0];
    expect(second).not.toBe(first);
    expect(second).toBe(" Very Lon");
  });

  it("carries the pause suffix", () => {
    const pp = new PlayPause();
    const k = fakeKey("key-1");
    mount(pp, [[k, { showTitle: true }]]);

    emit(playing("Short", null, true));
    expect(k.setTitle).toHaveBeenLastCalledWith("Short\n⏸");
  });

  it("stops the scroll when a static message takes the key", () => {
    // Otherwise the 400 ms timer repaints a marquee frame over "Offline".
    vi.useFakeTimers();
    const pp = new PlayPause();
    const k = fakeKey("key-1");
    mount(pp, [[k, { showTitle: true }]]);

    emit(playing("A Very Long Song Title", null));
    vi.advanceTimersByTime(400);
    emit({ kind: "offline" });
    expect(k.setTitle).toHaveBeenLastCalledWith("Offline");
    // Not just "the message survives": the clock itself has to be off, or the
    // key keeps a 400 ms interval ticking for as long as the bot is down.
    expect(vi.getTimerCount()).toBe(0);

    vi.advanceTimersByTime(2000);
    expect(k.setTitle).toHaveBeenLastCalledWith("Offline");
    expect(k.setTitle).toHaveBeenCalledTimes(3);
  });
});

describe("Play/Pause key with showArtwork", () => {
  it("sets a letterboxed cover", async () => {
    const pp = new PlayPause();
    const k = fakeKey("key-1");
    mount(pp, [[k, { showArtwork: true }]]);

    emit(playing("Track", "https://img.test/a.jpg"));
    await flush();

    const svg = k.setImage.mock.calls.at(-1)?.[0] as string;
    expect(svg).toContain('preserveAspectRatio="xMidYMid meet"');
    expect(svg).toContain("https://img.test/a.jpg");
  });

  it("resets to the manifest image when the next track has no artwork", async () => {
    // A stale cover must never outlive its track.
    const pp = new PlayPause();
    const k = fakeKey("key-1");
    mount(pp, [[k, { showArtwork: true }]]);

    emit(playing("Track", "https://img.test/a.jpg"));
    await flush();
    k.setImage.mockClear();

    emit(playing("Other track", null));
    await flush();

    expect(k.setImage).toHaveBeenCalledWith();
  });

  it("drops a slow fetch that lands after the track already changed", async () => {
    let release: ((v: string | null) => void) | null = null;
    h.loadThumbnail.mockImplementationOnce(
      () => new Promise<string | null>((r) => (release = r)),
    );
    const pp = new PlayPause();
    const k = fakeKey("key-1");
    mount(pp, [[k, { showArtwork: true }]]);

    emit(playing("Track", "https://img.test/slow.jpg"));
    emit(playing("Other track", null));
    await flush();
    k.setImage.mockClear();
    release!("data:image/png;base64,SLOW");
    await flush();

    expect(k.setImage).not.toHaveBeenCalled();
  });

  it("reverts the image when the option is switched back off", async () => {
    const pp = new PlayPause();
    const k = fakeKey("key-1");
    mount(pp, [[k, { showArtwork: true }]]);

    emit(playing("Track", "https://img.test/a.jpg"));
    await flush();
    k.setImage.mockClear();

    pp.onDidReceiveSettings({
      action: k,
      payload: { settings: {} },
    } as unknown as SettingsEv);
    emit(playing("Track", "https://img.test/a.jpg"));
    await flush();

    expect(k.setImage).toHaveBeenCalledWith();
  });
});

describe("Play/Pause settings are per key", () => {
  it("renders two keys differently from a single poll", async () => {
    // SingletonAction is one instance for every key of this type, so the poll
    // handler cannot read "the" settings — each key renders from its own.
    const pp = new PlayPause();
    const titleKey = fakeKey("key-title");
    const artKey = fakeKey("key-art");
    mount(pp, [
      [titleKey, { showTitle: true }],
      [artKey, { showArtwork: true }],
    ]);

    emit(playing("Track", "https://img.test/a.jpg"));
    await flush();

    expect(titleKey.setTitle).toHaveBeenCalledWith("Track");
    expect(titleKey.setImage).not.toHaveBeenCalled();
    expect(artKey.setTitle).not.toHaveBeenCalled();
    expect(artKey.setImage.mock.calls.at(-1)?.[0]).toContain("https://img.test/a.jpg");
  });

  it("forgets a key's state when it disappears", async () => {
    const pp = new PlayPause();
    const k = fakeKey("key-1");
    mount(pp, [[k, { showTitle: true }]]);

    pp.onWillDisappear({ action: k } as unknown as GoneEv);
    emit(playing("Track", null));
    await flush();

    expect(k.setTitle).not.toHaveBeenCalled();
  });
});
