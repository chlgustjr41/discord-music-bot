/**
 * The artwork allowlist.
 *
 * What is being defended: a thumbnail or server icon is an `<img src>` every
 * viewer's browser fetches without interaction, and several of the fields it
 * comes from are writable by any visitor. So the only thing that matters here
 * is WHICH HOST a given string would make the browser contact — and that the
 * answer cannot be dressed up to look like an allowed one.
 */

import { describe, expect, it } from "vitest";
import { isAllowedArtworkUrl, safeArtworkUrl } from "./artwork";

describe("safeArtworkUrl — what real artwork looks like", () => {
  it.each([
    // Lavalink's artworkUrl for YouTube, and the bot's own fallback.
    "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg",
    "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg?sqp=-oaymwE&rs=AOn4CL",
    "https://img.youtube.com/vi/dQw4w9WgXcQ/mqdefault.jpg",
    // YouTube / YouTube Music channel and album art.
    "https://yt3.ggpht.com/ytc/AIdro_k=s88-c-k-c0x00ffffff-no-rj",
    "https://lh3.googleusercontent.com/abc123=w544-h544-l90-rj",
    // SoundCloud.
    "https://i1.sndcdn.com/artworks-000123456789-abcdef-t500x500.jpg",
    "https://i2.sndcdn.com/avatars-000123-abc-large.jpg",
    // The Discord server icon, as guild.icon.url renders it.
    "https://cdn.discordapp.com/icons/123456789012345678/a_0123abcd.png?size=1024",
  ])("allows %s", (url) => {
    expect(safeArtworkUrl(url)).toBe(url);
    expect(isAllowedArtworkUrl(url)).toBe(true);
  });

  it("is case-insensitive about the host, as DNS is", () => {
    expect(safeArtworkUrl("https://I.YTIMG.COM/vi/x/mqdefault.jpg")).toBe(
      "https://i.ytimg.com/vi/x/mqdefault.jpg",
    );
  });
});

describe("safeArtworkUrl — an arbitrary host is a tracking beacon", () => {
  it.each([
    "https://evil.test/pixel.gif",
    "https://attacker.example/t.gif?who=viewer",
    "https://192.0.2.10/t.gif",
    "https://[2001:db8::1]/t.gif",
    "https://localhost/t.gif",
    // Plausible, but not on the list: unlisted sources get the placeholder.
    "https://f4.bcbits.com/img/a0123456789_10.jpg",
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://discord.com/assets/icon.png",
    "https://media.discordapp.net/attachments/1/2/x.png",
  ])("refuses %s", (url) => {
    expect(safeArtworkUrl(url)).toBeNull();
    expect(isAllowedArtworkUrl(url)).toBe(false);
  });
});

describe("safeArtworkUrl — look-alike hosts", () => {
  it.each([
    // The allowed name as a SUBDOMAIN of somebody else's domain.
    "https://i.ytimg.com.evil.test/vi/x/mqdefault.jpg",
    "https://img.youtube.com.evil.test/vi/x/mqdefault.jpg",
    "https://cdn.discordapp.com.evil.test/icons/1/a.png",
    "https://i1.sndcdn.com.evil.test/a.jpg",
    "https://lh3.googleusercontent.com.evil.test/a",
    "https://yt3.ggpht.com.evil.test/a",
    // The allowed name as a SUFFIX of a different registrable domain.
    "https://evil-ytimg.com/vi/x/mqdefault.jpg",
    "https://xi.ytimg.com/vi/x/mqdefault.jpg",
    "https://evilsndcdn.com/a.jpg",
    "https://evil-sndcdn.com/a.jpg",
    "https://notggpht.com/a",
    "https://evilgoogleusercontent.com/a",
    "https://fakecdn.discordapp.com/icons/1/a.png",
    // A different TLD.
    "https://i.ytimg.co/vi/x/mqdefault.jpg",
    "https://i.ytimg.com.co/vi/x/mqdefault.jpg",
    // The bare parent of a wildcard entry is not itself a subdomain.
    "https://sndcdn.com/a.jpg",
    "https://ggpht.com/a",
    "https://googleusercontent.com/a",
    // Exact entries are exact: no other subdomain rides along.
    "https://evil.i.ytimg.com/vi/x/mqdefault.jpg",
    "https://youtube.com/favicon.ico",
    "https://discordapp.com/icons/1/a.png",
    // Trailing-dot and percent-encoded spellings of an allowed host.
    "https://i.ytimg.com./vi/x/mqdefault.jpg",
    // The allowed name somewhere other than the host.
    "https://evil.test/i.ytimg.com/vi/x/mqdefault.jpg",
    "https://evil.test/?u=https://i.ytimg.com/vi/x/mqdefault.jpg",
    "https://evil.test/#https://i.ytimg.com/vi/x/mqdefault.jpg",
  ])("refuses %s", (url) => {
    expect(safeArtworkUrl(url)).toBeNull();
  });
});

describe("safeArtworkUrl — userinfo tricks", () => {
  it.each([
    // Everything before the @ is a username; the HOST is evil.test.
    "https://i.ytimg.com@evil.test/vi/x/mqdefault.jpg",
    "https://i.ytimg.com:443@evil.test/vi/x/mqdefault.jpg",
    "https://cdn.discordapp.com%2Ficons@evil.test/a.png",
    "https://i.ytimg.com%40evil.test/vi/x/mqdefault.jpg",
    // Credentials on an allowed host are never legitimate artwork either.
    "https://user@i.ytimg.com/vi/x/mqdefault.jpg",
    "https://user:pass@i.ytimg.com/vi/x/mqdefault.jpg",
  ])("refuses %s", (url) => {
    expect(safeArtworkUrl(url)).toBeNull();
  });

  it("a backslash cannot move the host: the checked URL is the requested URL", () => {
    // WHATWG parsing treats "\" as "/" for https, so this host IS i.ytimg.com
    // and the rest is path. What matters is that the value handed to <img> is
    // the normalised one, so there is no second, different parse.
    const out = safeArtworkUrl("https://i.ytimg.com\\@evil.test/x.jpg");
    expect(out).toBe("https://i.ytimg.com/@evil.test/x.jpg");
    expect(new URL(out!).hostname).toBe("i.ytimg.com");
  });
});

describe("safeArtworkUrl — scheme and port", () => {
  it.each([
    "http://i.ytimg.com/vi/x/mqdefault.jpg",
    "http://cdn.discordapp.com/icons/1/a.png",
    "//i.ytimg.com/vi/x/mqdefault.jpg",
    "ftp://i.ytimg.com/vi/x/mqdefault.jpg",
    "javascript:alert(1)//https://i.ytimg.com/",
    "data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg'/>",
    "blob:https://i.ytimg.com/0d5e3a1c",
    "file:///etc/passwd",
    "https://i.ytimg.com:8443/vi/x/mqdefault.jpg",
    "https://i.ytimg.com:80/vi/x/mqdefault.jpg",
  ])("refuses %s", (url) => {
    expect(safeArtworkUrl(url)).toBeNull();
  });

  it("the explicit default port is the same URL", () => {
    expect(safeArtworkUrl("https://i.ytimg.com:443/vi/x/mqdefault.jpg")).toBe(
      "https://i.ytimg.com/vi/x/mqdefault.jpg",
    );
  });
});

describe("safeArtworkUrl — junk out of the database", () => {
  it.each([
    ["an empty string", ""],
    ["whitespace", "   "],
    ["not a URL", "not a url"],
    ["a relative path", "/favicon.svg"],
    ["null", null],
    ["undefined", undefined],
    ["a number", 42],
    ["an object", { url: "https://i.ytimg.com/vi/x/mqdefault.jpg" }],
    ["an array", ["https://i.ytimg.com/vi/x/mqdefault.jpg"]],
    ["an absurdly long URL", `https://i.ytimg.com/${"a".repeat(3000)}`],
  ])("refuses %s", (_label, value) => {
    expect(safeArtworkUrl(value)).toBeNull();
    expect(isAllowedArtworkUrl(value)).toBe(false);
  });

  it("normalises stray whitespace and control characters away rather than passing them on", () => {
    expect(safeArtworkUrl("  https://i.ytimg.com/vi/x/mq\ndefault.jpg  ")).toBe(
      "https://i.ytimg.com/vi/x/mqdefault.jpg",
    );
  });
});
