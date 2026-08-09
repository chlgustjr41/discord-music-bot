import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({
  dashboardUrl: vi.fn(),
  openUrl: vi.fn(async (_url: string) => {}),
}));

vi.mock("../src/pi-bridge", () => ({ handlePiEvent: vi.fn() }));
vi.mock("../src/runtime", () => ({
  getClient: () => ({ dashboardUrl: h.dashboardUrl }),
}));
// The real module opens a websocket to the Stream Deck host on import.
vi.mock("@elgato/streamdeck", () => ({
  default: { system: { openUrl: (url: string) => h.openUrl(url) } },
  action: () => (target: unknown) => target,
  SingletonAction: class {},
}));

const { Dashboard } = await import("../src/actions/dashboard");

type DownEv = Parameters<InstanceType<typeof Dashboard>["onKeyDown"]>[0];

function fakeKey() {
  const stub = {
    id: "key-1",
    showAlert: vi.fn(async () => {}),
    showOk: vi.fn(async () => {}),
  };
  return { action: stub, down: { action: stub } as unknown as DownEv };
}

beforeEach(() => {
  h.dashboardUrl.mockReset();
  h.openUrl.mockReset().mockResolvedValue(undefined);
});

describe("Dashboard key", () => {
  it("opens an https url the server returns", async () => {
    h.dashboardUrl.mockResolvedValue({ active: true, url: "https://web.test/dashboard/CODE1" });
    const k = fakeKey();
    await new Dashboard().onKeyDown(k.down);

    expect(h.openUrl).toHaveBeenCalledWith("https://web.test/dashboard/CODE1");
    expect(k.action.showOk).toHaveBeenCalled();
  });

  it("still opens the entry page when there is no active session", async () => {
    h.dashboardUrl.mockResolvedValue({ active: false, url: "https://web.test/" });
    const k = fakeKey();
    await new Dashboard().onKeyDown(k.down);

    expect(h.openUrl).toHaveBeenCalledWith("https://web.test/");
    expect(k.action.showAlert).toHaveBeenCalled();
  });

  it("refuses a javascript: url from the server", async () => {
    // apiUrl is user-overridable, so this response is not guaranteed to come
    // from the real server; a javascript: url executes rather than navigates.
    h.dashboardUrl.mockResolvedValue({ active: true, url: "javascript:alert(1)" });
    const k = fakeKey();
    await new Dashboard().onKeyDown(k.down);

    expect(h.openUrl).not.toHaveBeenCalled();
    expect(k.action.showAlert).toHaveBeenCalled();
    expect(k.action.showOk).not.toHaveBeenCalled();
  });

  it("refuses a file: url from the server", async () => {
    h.dashboardUrl.mockResolvedValue({ active: true, url: "file:///C:/Windows/System32/calc.exe" });
    const k = fakeKey();
    await new Dashboard().onKeyDown(k.down);

    expect(h.openUrl).not.toHaveBeenCalled();
    expect(k.action.showAlert).toHaveBeenCalled();
  });
});
