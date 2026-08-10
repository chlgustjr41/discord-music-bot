import { describe, expect, it, vi } from "vitest";
import {
  ControlApiError,
  JackyClient,
  type ChannelList,
  type NowPlaying,
  type SummonResult,
} from "../src/api-client";

const CONFIG = {
  apiUrl: "https://control.example.com/",
  authToken: "tok",
};

function fetchStub(status: number, body: unknown = {}) {
  return vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  })) as unknown as typeof fetch;
}

describe("JackyClient", () => {
  it("POSTs actions with auth header and trimmed base URL, no user id", async () => {
    const f = fetchStub(200);
    await new JackyClient(CONFIG, f).volume(5);
    const [url, init] = (f as any).mock.calls[0];
    expect(url).toBe("https://control.example.com/control/volume");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer tok");
    expect(JSON.parse(init.body)).toEqual({ delta: 5 });
    expect(init.body).not.toContain("discordUserId");
    expect(init.signal).toBeInstanceOf(AbortSignal);
  });

  it("GETs now-playing with auth header and no query string", async () => {
    const data: NowPlaying = { active: false };
    const f = fetchStub(200, data);
    const result = await new JackyClient(CONFIG, f).nowPlaying();
    const [url, init] = (f as any).mock.calls[0];
    expect(url).toBe("https://control.example.com/control/now-playing");
    expect(url).not.toContain("discordUserId");
    expect(result).toEqual({ active: false });
    expect(init.headers.Authorization).toBe("Bearer tok");
  });

  it("throws ControlApiError carrying the status on non-2xx", async () => {
    const client = new JackyClient(CONFIG, fetchStub(401));
    await expect(client.playPause()).rejects.toMatchObject({ status: 401 });
    await expect(client.playPause()).rejects.toBeInstanceOf(ControlApiError);
  });

  it("exposes one method per route", async () => {
    const f = fetchStub(200);
    const client = new JackyClient(CONFIG, f);
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

  it("POSTs shuffle to its own route, not skip's", async () => {
    const f = fetchStub(200, { ok: true, count: 3 });
    await new JackyClient(CONFIG, f).shuffle();
    const [url, init] = (f as any).mock.calls[0];
    expect(url).toBe("https://control.example.com/control/shuffle");
    expect(url).not.toContain("/control/skip");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer tok");
  });

  it("shuffle throws ControlApiError carrying the status with no live session", async () => {
    const client = new JackyClient(CONFIG, fetchStub(409));
    await expect(client.shuffle()).rejects.toMatchObject({ status: 409 });
    await expect(client.shuffle()).rejects.toBeInstanceOf(ControlApiError);
  });

  it("GETs channels with auth header and returns the list", async () => {
    const data: ChannelList = [
      {
        guildId: "1",
        guildName: "Guild",
        channels: [{ id: "10", name: "General" }],
      },
    ];
    const f = fetchStub(200, data);
    const result = await new JackyClient(CONFIG, f).channels();
    const [url, init] = (f as any).mock.calls[0];
    expect(url).toBe("https://control.example.com/control/channels");
    expect(init.headers.Authorization).toBe("Bearer tok");
    expect(result).toEqual(data);
  });

  it("channels throws ControlApiError on non-2xx", async () => {
    const client = new JackyClient(CONFIG, fetchStub(403));
    await expect(client.channels()).rejects.toMatchObject({ status: 403 });
  });

  it("POSTs summon with guild/channel body and parses the result", async () => {
    const data: SummonResult = { action: "joined", sessionCode: "ABCD" };
    const f = fetchStub(200, data);
    const result = await new JackyClient(CONFIG, f).summon("1", "10");
    const [url, init] = (f as any).mock.calls[0];
    expect(url).toBe("https://control.example.com/control/summon");
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer tok");
    expect(JSON.parse(init.body)).toEqual({ guildId: "1", channelId: "10" });
    expect(init.body).not.toContain("discordUserId");
    expect(result).toEqual({ action: "joined", sessionCode: "ABCD" });
  });

  it("summon throws ControlApiError carrying the status on non-2xx", async () => {
    const client = new JackyClient(CONFIG, fetchStub(409));
    await expect(client.summon("1", "10")).rejects.toMatchObject({
      status: 409,
    });
    await expect(client.summon("1", "10")).rejects.toBeInstanceOf(
      ControlApiError,
    );
  });

  it("GETs the playlist list", async () => {
    const data = [
      { guildId: "1", guildName: "G", playlists: [{ name: "Chill", trackCount: 2 }] },
    ];
    const f = fetchStub(200, data);
    const result = await new JackyClient(CONFIG, f).playlists();
    const [url, init] = (f as any).mock.calls[0];
    expect(url).toBe("https://control.example.com/control/playlists");
    expect(init.headers.Authorization).toBe("Bearer tok");
    expect(result).toEqual(data);
  });

  it("POSTs a playlist play request and returns the count", async () => {
    const f = fetchStub(200, { inserted: 2, playlistName: "Chill" });
    const result = await new JackyClient(CONFIG, f).playPlaylist("1", "Chill");
    const [url, init] = (f as any).mock.calls[0];
    expect(url).toBe("https://control.example.com/control/playlist");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ guildId: "1", playlistName: "Chill" });
    expect(result.inserted).toBe(2);
  });

  it("GETs the dashboard url", async () => {
    const f = fetchStub(200, { active: true, url: "https://web/dashboard/ABC123" });
    const result = await new JackyClient(CONFIG, f).dashboardUrl();
    expect((f as any).mock.calls[0][0]).toBe(
      "https://control.example.com/control/dashboard-url",
    );
    expect(result).toEqual({ active: true, url: "https://web/dashboard/ABC123" });
  });
});
