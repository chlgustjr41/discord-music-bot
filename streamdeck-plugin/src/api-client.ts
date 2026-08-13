export type NowPlaying =
  | { active: false }
  | {
      active: true;
      title: string | null;
      author: string;
      paused: boolean;
      volume: number;
      guildName: string;
      thumbnail: string | null;
    };

export type ChannelList = {
  guildId: string;
  guildName: string;
  channels: { id: string; name: string }[];
}[];

export type PlaylistList = {
  guildId: string;
  guildName: string;
  playlists: { name: string; trackCount: number }[];
}[];
export type DashboardUrl = { active: boolean; url: string; guildName?: string };

export type SummonResult = { action: "joined" | "left"; sessionCode?: string };

export type VoiceResult = {
  transcript: string;
  actions: { action: string; ok: boolean; detail: string }[];
  ok: boolean;
  detail: string | null;
  /** Directives the server cannot carry out itself — today only `open_url`,
   *  because the browser lives on the user's machine, not on the server.
   *  Optional, and every field is re-checked at the read site: this is a cast
   *  over an unvalidated response, and an older plugin may face a newer server
   *  (or the reverse) where the field is absent or shaped differently. */
  client?: { type: string; url?: string }[];
};

/** `POST /control/announce` answers 200 for both outcomes a configured key can
 *  reach: `ok: true` posted the embed, `ok: false` carries a human-readable
 *  `detail` for content that does not exist yet ("Queue is empty"). Cooldown
 *  and no-session are non-2xx and surface as ControlApiError instead. */
export type AnnounceResult = { ok: boolean; detail?: string };

/** Per-press options for `voiceCommand`. `debug` asks the server to echo what
 *  it heard and how it resolved into the session's Discord channel — opt-in
 *  per key, because that channel is readable by everyone in the session. */
export type VoiceOptions = { language?: string; debug?: boolean };

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

  async shuffle(): Promise<void> {
    await this.post("/control/shuffle");
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

  async playlists(): Promise<PlaylistList> {
    const res = await this.get("/control/playlists");
    return (await res.json()) as PlaylistList;
  }

  async playPlaylist(
    guildId: string,
    playlistName: string,
  ): Promise<{ inserted: number; playlistName: string }> {
    const res = await this.post("/control/playlist", { guildId, playlistName });
    return (await res.json()) as { inserted: number; playlistName: string };
  }

  async dashboardUrl(): Promise<DashboardUrl> {
    const res = await this.get("/control/dashboard-url");
    return (await res.json()) as DashboardUrl;
  }

  async announce(command: string): Promise<AnnounceResult> {
    const res = await this.post("/control/announce", { command });
    return (await res.json()) as AnnounceResult;
  }

  async summon(guildId: string, channelId: string): Promise<SummonResult> {
    const res = await this.post("/control/summon", { guildId, channelId });
    return (await res.json()) as SummonResult;
  }

  /** 30 s, not the usual 10 s: this request includes a transcription round-trip.
   *
   *  An options object rather than positional arguments: both knobs are
   *  optional and one of them is a bare boolean, so `voiceCommand(wav, true)`
   *  would be a silent mis-call that publishes a transcript to Discord.
   *  Naming it at the call site makes that impossible.
   *
   *  `language` fixes the transcription language, which beats autodetect on
   *  clips this short. An unset setting OMITS the parameter rather than
   *  sending an empty one: the server's own default is English, and
   *  `?language=` would be a value it has to normalize away. `debug` is
   *  likewise only ever sent when ON — absent already means off server-side,
   *  so there is no `debug=0` to send. */
  async voiceCommand(wav: Uint8Array, opts: VoiceOptions = {}): Promise<VoiceResult> {
    const params = new URLSearchParams();
    if (opts.language) params.set("language", opts.language);
    if (opts.debug) params.set("debug", "1");
    const encoded = params.toString();
    const query = encoded ? `?${encoded}` : "";
    const res = await this.fetchFn(this.url("/control/voice" + query), {
      method: "POST",
      headers: {
        Authorization: `Bearer ${this.cfg.authToken}`,
        "Content-Type": "audio/wav",
      },
      body: wav,
      signal: AbortSignal.timeout(30_000),
    });
    if (!res.ok) throw new ControlApiError(res.status);
    return (await res.json()) as VoiceResult;
  }
}
