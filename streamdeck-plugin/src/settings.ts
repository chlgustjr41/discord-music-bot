export type GlobalSettings = {
  apiUrl?: string;
  apiToken?: string;
  discordUserId?: string;
};

export function settingsReady(s: GlobalSettings): s is Required<GlobalSettings> {
  return Boolean(s.apiUrl && s.apiToken && s.discordUserId);
}
