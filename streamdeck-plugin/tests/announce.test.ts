import { beforeEach, describe, expect, it, vi } from "vitest";
import { ControlApiError } from "../src/api-client";

const h = vi.hoisted(() => ({
  announce: vi.fn(async (...args: unknown[]): Promise<unknown> => args),
  client: null as unknown,
}));

vi.mock("../src/pi-bridge", () => ({ handlePiEvent: vi.fn() }));
vi.mock("../src/runtime", () => ({ getClient: () => h.client }));
// The real module opens a websocket to the Stream Deck host on import.
vi.mock("@elgato/streamdeck", () => ({
  default: {},
  action: () => (target: unknown) => target,
  SingletonAction: class {},
}));

const { Announce } = await import("../src/actions/announce");

type DownEv = Parameters<InstanceType<typeof Announce>["onKeyDown"]>[0];

function fakeKey(settings: Record<string, unknown> = {}) {
  const stub = {
    id: "key-1",
    setTitle: vi.fn(async (...args: unknown[]): Promise<unknown> => args),
    showAlert: vi.fn(async (...args: unknown[]): Promise<unknown> => args),
    showOk: vi.fn(async (...args: unknown[]): Promise<unknown> => args),
    getSettings: vi.fn(async (...args: unknown[]): Promise<unknown> => {
      void args;
      return settings;
    }),
  };
  return { action: stub, down: { action: stub } as unknown as DownEv };
}

beforeEach(() => {
  h.announce.mockReset().mockResolvedValue({ ok: true, command: "session" });
  h.client = { announce: h.announce };
});

describe("Announce key", () => {
  it("posts the configured command and flashes OK", async () => {
    const k = fakeKey({ command: "session" });
    await new Announce().onKeyDown(k.down);

    expect(h.announce).toHaveBeenCalledWith("session");
    expect(k.action.showOk).toHaveBeenCalled();
    expect(k.action.showAlert).not.toHaveBeenCalled();
  });

  it("makes no request and flashes alert when the key has no command configured", async () => {
    // Like an unconfigured Summon key: nothing to post, so nothing is sent.
    const k = fakeKey({});
    await new Announce().onKeyDown(k.down);

    expect(h.announce).not.toHaveBeenCalled();
    expect(k.action.showAlert).toHaveBeenCalled();
    expect(k.action.showOk).not.toHaveBeenCalled();
  });

  it("makes no request and flashes alert when the plugin is unconfigured", async () => {
    h.client = null;
    const k = fakeKey({ command: "session" });
    await new Announce().onKeyDown(k.down);

    expect(h.announce).not.toHaveBeenCalled();
    expect(k.action.showAlert).toHaveBeenCalled();
  });

  it("renders the ok:false detail on the key and alerts instead of flashing OK", async () => {
    // "Queue is empty" is the server answering the person at the deck; the key
    // is where that answer must land, wrapped to fit its width.
    h.announce.mockResolvedValue({ ok: false, detail: "Queue is empty" });
    const k = fakeKey({ command: "queue" });
    await new Announce().onKeyDown(k.down);

    expect(k.action.setTitle).toHaveBeenCalledWith("Queue is\nempty");
    expect(k.action.showAlert).toHaveBeenCalled();
    expect(k.action.showOk).not.toHaveBeenCalled();
  });

  it("clears the rendered detail after a few seconds", async () => {
    vi.useFakeTimers();
    try {
      h.announce.mockResolvedValue({ ok: false, detail: "Nothing is playing" });
      const k = fakeKey({ command: "nowplaying" });
      await new Announce().onKeyDown(k.down);

      expect(k.action.setTitle).not.toHaveBeenCalledWith("");
      await vi.runAllTimersAsync();
      expect(k.action.setTitle).toHaveBeenCalledWith("");
    } finally {
      vi.useRealTimers();
    }
  });

  it("says 'Just posted' on the cooldown 429 rather than a generic failure", async () => {
    h.announce.mockRejectedValue(new ControlApiError(429));
    const k = fakeKey({ command: "session" });
    await new Announce().onKeyDown(k.down);

    expect(k.action.setTitle).toHaveBeenCalledWith("Just\nposted");
    expect(k.action.showAlert).toHaveBeenCalled();
    expect(k.action.showOk).not.toHaveBeenCalled();
  });

  it("flashes a plain alert with no 'Just posted' title on a server error", async () => {
    // Proves the 429 branch actually reads the status: a 500 must not be
    // mistaken for the cooldown.
    h.announce.mockRejectedValue(new ControlApiError(500));
    const k = fakeKey({ command: "status" });
    await new Announce().onKeyDown(k.down);

    expect(k.action.setTitle).not.toHaveBeenCalledWith("Just\nposted");
    expect(k.action.showAlert).toHaveBeenCalled();
    expect(k.action.showOk).not.toHaveBeenCalled();
  });

  it("flashes a plain alert when the request never reached the server", async () => {
    h.announce.mockRejectedValue(new Error("network down"));
    const k = fakeKey({ command: "session" });
    await new Announce().onKeyDown(k.down);

    expect(k.action.setTitle).not.toHaveBeenCalledWith("Just\nposted");
    expect(k.action.showAlert).toHaveBeenCalled();
  });
});
