import type { GlobalSettings } from "./settings";

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

export class ControlApiError extends Error {
  constructor(readonly status: number) {
    super(`control api responded ${status}`);
    this.name = "ControlApiError";
  }
}

export class JackyClient {
  constructor(
    private readonly s: Required<GlobalSettings>,
    private readonly fetchFn: typeof fetch = fetch,
  ) {}

  private url(path: string): string {
    return this.s.apiUrl.replace(/\/+$/, "") + path;
  }

  private async post(path: string, extra: Record<string, unknown> = {}): Promise<void> {
    const res = await this.fetchFn(this.url(path), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.s.apiToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ discordUserId: this.s.discordUserId, ...extra }),
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) throw new ControlApiError(res.status);
  }

  playPause(): Promise<void> {
    return this.post("/control/play-pause");
  }

  skip(): Promise<void> {
    return this.post("/control/skip");
  }

  stop(): Promise<void> {
    return this.post("/control/stop");
  }

  volume(delta: number): Promise<void> {
    return this.post("/control/volume", { delta });
  }

  async nowPlaying(): Promise<NowPlaying> {
    const query = `?discordUserId=${encodeURIComponent(this.s.discordUserId)}`;
    const res = await this.fetchFn(this.url("/control/now-playing") + query, {
      headers: { Authorization: `Bearer ${this.s.apiToken}` },
      signal: AbortSignal.timeout(10_000),
    });
    if (!res.ok) throw new ControlApiError(res.status);
    return (await res.json()) as NowPlaying;
  }
}
