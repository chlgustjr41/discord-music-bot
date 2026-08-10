import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  shuffle: vi.fn(async (...args: unknown[]): Promise<unknown> => args),
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

const { Shuffle } = await import("../src/actions/shuffle");

type DownEv = Parameters<InstanceType<typeof Shuffle>["onKeyDown"]>[0];

function fakeKey() {
  const stub = {
    id: "key-1",
    showAlert: vi.fn(async (...args: unknown[]) => args),
    showOk: vi.fn(async (...args: unknown[]) => args),
  };
  return { action: stub, down: { action: stub } as unknown as DownEv };
}

beforeEach(() => {
  h.shuffle.mockReset().mockResolvedValue(undefined);
  h.client = { shuffle: h.shuffle };
});

describe("Shuffle key", () => {
  it("shuffles and flashes OK", async () => {
    const k = fakeKey();
    await new Shuffle().onKeyDown(k.down);

    expect(h.shuffle).toHaveBeenCalledTimes(1);
    expect(k.action.showOk).toHaveBeenCalled();
    expect(k.action.showAlert).not.toHaveBeenCalled();
  });

  it("flashes alert when the server refuses (no live session)", async () => {
    h.shuffle.mockRejectedValue(new Error("409"));
    const k = fakeKey();
    await new Shuffle().onKeyDown(k.down);

    expect(k.action.showAlert).toHaveBeenCalled();
    expect(k.action.showOk).not.toHaveBeenCalled();
  });

  it("flashes alert and calls nothing when the plugin is unconfigured", async () => {
    h.client = null;
    const k = fakeKey();
    await new Shuffle().onKeyDown(k.down);

    expect(h.shuffle).not.toHaveBeenCalled();
    expect(k.action.showAlert).toHaveBeenCalled();
  });
});
