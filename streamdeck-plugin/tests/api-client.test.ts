import { describe, expect, it, vi } from "vitest";
import { ControlApiError, JackyClient, type NowPlaying } from "../src/api-client";

const SETTINGS = {
  apiUrl: "https://control.example.com/",
  apiToken: "tok",
  discordUserId: "42",
};

function fetchStub(status: number, body: unknown = {}) {
  return vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  })) as unknown as typeof fetch;
}

describe("JackyClient", () => {
  it("POSTs actions with auth header, user id, and trimmed base URL", async () => {
    const f = fetchStub(200);
    await new JackyClient(SETTINGS, f).volume(5);
    const [url, init] = (f as any).mock.calls[0];
    expect(url).toBe("https://control.example.com/control/volume");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer tok");
    expect(JSON.parse(init.body)).toEqual({ discordUserId: "42", delta: 5 });
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("GETs now-playing with the user id in the query", async () => {
    const data: NowPlaying = { active: false };
    const f = fetchStub(200, data);
    const result = await new JackyClient(SETTINGS, f).nowPlaying();
    const [url] = (f as any).mock.calls[0];
    expect(url).toBe(
      "https://control.example.com/control/now-playing?discordUserId=42",
    );
    expect(result).toEqual({ active: false });
    expect((f as any).mock.calls[0][1].headers.Authorization).toBe("Bearer tok");
  });

  it("throws ControlApiError carrying the status on non-2xx", async () => {
    const client = new JackyClient(SETTINGS, fetchStub(401));
    await expect(client.playPause()).rejects.toMatchObject({ status: 401 });
    await expect(client.playPause()).rejects.toBeInstanceOf(ControlApiError);
  });

  it("exposes one method per route", async () => {
    const f = fetchStub(200);
    const client = new JackyClient(SETTINGS, f);
    await client.playPause();
    await client.skip();
    await client.stop();
    const urls = (f as any).mock.calls.map((c: any[]) => c[0]);
    expect(urls).toEqual([
      "https://control.example.com/control/play-pause",
      "https://control.example.com/control/skip",
      "https://control.example.com/control/stop",
    ]);
  });
});
