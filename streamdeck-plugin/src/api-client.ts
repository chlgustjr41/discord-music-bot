export type NowPlaying =
  | { active: false }
  | {
      active: true;
      title: string | null;
      author: string;
      paused: boolean;
      volume: number;
      guildName: string;
    };

export type ChannelList = {
  guildId: string;
  guildName: string;
  channels: { id: string; name: string }[];
}[];

export type SummonResult = { action: "joined" | "left"; sessionCode?: string };

export type ClientConfig = { apiUrl: string; authToken: string };

export class ControlApiError extends Error {
  constructor(readonly status: number) {
    super(`control api responded ${status}`);
    this.name = "ControlApiError";
  }
}

export class JackyClient {
  constructor(
    private readonly cfg: ClientConfig,
    private readonly fetchFn: typeof fetch = fetch,
  ) {}

  private url(path: string): string {
    return this.cfg.apiUrl.replace(/\/+$/, "") + path;
  }

  private async post(path: string, body: Record<string, unknown> = {}): Promise<Response> {
    const res = await this.fetchFn(this.url(path), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.cfg.authToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) throw new ControlApiError(res.status);
    return res;
  }

  private async get(path: string): Promise<Response> {
    const res = await this.fetchFn(this.url(path), {
      headers: { Authorization: `Bearer ${this.cfg.authToken}` },
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) throw new ControlApiError(res.status);
    return res;
  }

  async playPause(): Promise<void> {
    await this.post("/control/play-pause");
  }

  async skip(): Promise<void> {
    await this.post("/control/skip");
  }

  async stop(): Promise<void> {
    await this.post("/control/stop");
  }

  async volume(delta: number): Promise<void> {
    await this.post("/control/volume", { delta });
  }

  async nowPlaying(): Promise<NowPlaying> {
    const res = await this.get("/control/now-playing");
    return (await res.json()) as NowPlaying;
  }

  async channels(): Promise<ChannelList> {
    const res = await this.get("/control/channels");
    return (await res.json()) as ChannelList;
  }

  async summon(guildId: string, channelId: string): Promise<SummonResult> {
    const res = await this.post("/control/summon", { guildId, channelId });
    return (await res.json()) as SummonResult;
  }
}
