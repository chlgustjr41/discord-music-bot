import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PollState } from "../src/poller";

const h = vi.hoisted(() => ({
  playPause: vi.fn(async (...args: unknown[]): Promise<unknown> => args),
  loadThumbnail: vi.fn(async (...args: unknown[]): Promise<string | null> => args[0] as string),
  subs: new Set<(s: PollState) => void>(),
}));

vi.mock("../src/pi-bridge", () => ({ handlePiEvent: vi.fn() }));
// Only the network call is replaced; MAX_ENCODED_CHARS stays the real cap, so
// the size test cannot pass against a number invented by the test.
vi.mock("../src/thumbnail", async (orig) => ({
  ...(await orig<typeof import("../src/thumbnail")>()),
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
// `logger` is stubbed because the artwork path writes to it — a bare `{}`
// default would throw at import time on createScope.
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
    default: { logger },
    action: () => (target: unknown) => target,
    SingletonAction: class {},
  };
});

const { PlayPause } = await import("../src/actions/play-pause");
const { MAX_ENCODED_CHARS } = await import("../src/thumbnail");
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

/** setImage payloads are base64 SVG data URIs (raw <svg> strings are silently
 *  ignored by Stream Deck 7.x on Windows), so assertions on their content must
 *  decode them first. Fails loudly if the payload regresses to a raw string. */
function decodeApplied(payload: unknown): string {
  const uri = payload as string;
  const prefix = "data:image/svg+xml;base64,";
  expect(uri.startsWith(prefix)).toBe(true);
  return Buffer.from(uri.slice(prefix.length), "base64").toString("utf8");
}

describe("Play/Pause key with showArtwork", () => {
  it("sets a letterboxed cover", async () => {
    const pp = new PlayPause();
    const k = fakeKey("key-1");
    mount(pp, [[k, { showArtwork: true }]]);

    emit(playing("Track", "https://img.test/a.jpg"));
    await flush();

    const svg = decodeApplied(k.setImage.mock.calls.at(-1)?.[0]);
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

  it("re-applies the cached cover on a poll where the url did not change", async () => {
    // The bug this fixes: applying the image only on change assumes nothing
    // else ever repaints the key. A state change, a profile switch or a host
    // redraw would leave the manifest glyph there permanently. Re-applying is
    // a cheap local call, so the key converges within one poll — but the fetch
    // must NOT repeat, or every key hammers the artwork host every 5 seconds.
    const pp = new PlayPause();
    const k = fakeKey("key-1");
    mount(pp, [[k, { showArtwork: true }]]);

    emit(playing("Track", "https://img.test/a.jpg"));
    await flush();
    const applied = k.setImage.mock.calls.at(-1)?.[0] as string;
    expect(decodeApplied(applied)).toContain("https://img.test/a.jpg");
    k.setImage.mockClear();
    h.loadThumbnail.mockClear();

    emit(playing("Track", "https://img.test/a.jpg"));
    await flush();

    expect(k.setImage).toHaveBeenCalledTimes(1);
    expect(k.setImage).toHaveBeenCalledWith(applied);
    expect(h.loadThumbnail).not.toHaveBeenCalled();
  });

  it("refetches exactly once when the url changes", async () => {
    const pp = new PlayPause();
    const k = fakeKey("key-1");
    mount(pp, [[k, { showArtwork: true }]]);

    emit(playing("Track", "https://img.test/a.jpg"));
    await flush();
    emit(playing("Other", "https://img.test/b.jpg"));
    await flush();
    emit(playing("Other", "https://img.test/b.jpg"));
    await flush();

    expect(h.loadThumbnail).toHaveBeenCalledTimes(2);
    expect(h.loadThumbnail).toHaveBeenLastCalledWith("https://img.test/b.jpg");
    expect(decodeApplied(k.setImage.mock.calls.at(-1)?.[0])).toContain("https://img.test/b.jpg");
  });

  it("re-applies nothing when the fetch produced no image", async () => {
    // Nothing was cached, so there is nothing to re-apply; the key keeps the
    // manifest glyph rather than being handed an empty setImage every poll.
    h.loadThumbnail.mockResolvedValue(null);
    const pp = new PlayPause();
    const k = fakeKey("key-1");
    mount(pp, [[k, { showArtwork: true }]]);

    emit(playing("Track", "https://img.test/a.jpg"));
    await flush();
    emit(playing("Track", "https://img.test/a.jpg"));
    await flush();

    expect(k.setImage).not.toHaveBeenCalled();
  });

  it("keeps the glyph rather than sending an image too large to render", async () => {
    // The reported bug, as a number: 258,023 encoded characters went over the
    // websocket to a 72-pixel key and nothing appeared. Past the cap the
    // artwork is worth less than the glyph it would replace.
    h.loadThumbnail.mockResolvedValue(
      "data:image/jpeg;base64," + "A".repeat(MAX_ENCODED_CHARS),
    );
    const pp = new PlayPause();
    const k = fakeKey("key-1");
    mount(pp, [[k, { showArtwork: true }]]);

    emit(playing("Track", "https://img.test/huge.jpg"));
    await flush();

    expect(k.setImage).not.toHaveBeenCalled();
  });

  it("still applies an image that fits under the cap", async () => {
    h.loadThumbnail.mockResolvedValue("data:image/jpeg;base64," + "A".repeat(2000));
    const pp = new PlayPause();
    const k = fakeKey("key-1");
    mount(pp, [[k, { showArtwork: true }]]);

    emit(playing("Track", "https://img.test/ok.jpg"));
    await flush();

    expect(k.setImage).toHaveBeenCalledTimes(1);
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
    expect(decodeApplied(artKey.setImage.mock.calls.at(-1)?.[0])).toContain("https://img.test/a.jpg");
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
