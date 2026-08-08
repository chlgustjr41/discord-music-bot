export type GlobalSettings = {
  apiUrl?: string;
  authToken?: string;
  discordUserId?: string;
  discordUserName?: string;
};

export const DEFAULT_API_URL = "https://control.jacky-music-bot.com";

/** The configured API URL (trimmed) or the hosted default. */
export function effectiveApiUrl(s: GlobalSettings): string {
  return s.apiUrl?.trim() || DEFAULT_API_URL;
}

export function settingsReady(
  s: GlobalSettings,
): s is GlobalSettings & { authToken: string } {
  return Boolean(effectiveApiUrl(s) && s.authToken);
}
