/**
 * Render-site checks for the artwork allowlist (lib/artwork.ts).
 *
 * The helper is unit-tested on its own; what these pin is that the components
 * actually USE it — that a thumbnail or server icon out of Firestore never
 * becomes an <img> request to a host a visitor chose. Like the presence
 * component tests, they exist because the dashboard cannot be opened in a
 * browser without a live bot session.
 */

import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { CurrentTrack, Track } from "../types";

vi.mock("../firebase", () => ({ db: {}, auth: {} }));
vi.mock("firebase/firestore", () => ({
  doc: vi.fn(() => ({})),
  updateDoc: vi.fn(async () => {}),
  setDoc: vi.fn(async () => {}),
  increment: (n: number) => ({ __inc: n }),
  serverTimestamp: () => ({ __ts: true }),
}));
vi.mock("../lib/identity", () => ({ getIdentityName: () => "Ada" }));
vi.mock("../lib/social", () => ({ bumpMemberStat: vi.fn() }));

import { NowPlaying } from "./NowPlaying";
import { Queue } from "./Queue";
import { PlaylistDraftList } from "./playlist/PlaylistDraftList";

const GOOD = "https://i.ytimg.com/vi/abc/mqdefault.jpg";
const EVIL = "https://evil.test/pixel.gif";
const LOOKALIKE = "https://i.ytimg.com.evil.test/vi/abc/mqdefault.jpg";

function track(thumbnail: string, n = 1): Track {
  return {
    title: `Track ${n}`,
    artist: "Artist",
    url: `https://www.youtube.com/watch?v=v${n}`,
    thumbnail,
    duration: 200,
    requestedBy: "Bob",
  };
}

const sources = (container: HTMLElement) =>
  [...container.querySelectorAll("img")].map((img) => img.getAttribute("src"));

describe("NowPlaying artwork", () => {
  const current = (thumbnail: string): CurrentTrack => ({
    ...track(thumbnail),
    startedAt: new Date().toISOString(),
  });

  it("loads a thumbnail from an allowed host", () => {
    const { container } = render(
      <NowPlaying track={current(GOOD)} isPaused={false} serverId="1" />,
    );
    expect(sources(container)).toEqual([GOOD]);
  });

  it.each([EVIL, LOOKALIKE, "http://i.ytimg.com/vi/abc/mqdefault.jpg", ""])(
    "shows the placeholder instead of requesting %s",
    (thumbnail) => {
      const { container, getByText } = render(
        <NowPlaying track={current(thumbnail)} isPaused={false} serverId="1" />,
      );
      expect(container.querySelector("img")).toBeNull();
      // The rest of the card is untouched.
      expect(getByText("Track 1")).toBeDefined();
    },
  );
});

describe("Queue artwork", () => {
  it("requests only the allowed thumbnails in a mixed queue", () => {
    const { container, getByText } = render(
      <Queue serverId="1" queue={[track(GOOD, 1), track(EVIL, 2), track(LOOKALIKE, 3)]} />,
    );
    expect(sources(container)).toEqual([GOOD]);
    // The tracks themselves still list — only the image is withheld.
    expect(getByText("Track 2")).toBeDefined();
    expect(getByText("Track 3")).toBeDefined();
  });
});

describe("Playlist editor artwork", () => {
  it("requests only the allowed thumbnails in a draft", () => {
    const { container } = render(
      <PlaylistDraftList
        draft={[track(GOOD, 1), track(EVIL, 2)]}
        onChange={() => {}}
        formatDuration={(s) => String(s)}
      />,
    );
    expect(sources(container)).toEqual([GOOD]);
  });
});

/**
 * A static sweep, because the components above are only three of the eleven
 * places a Firestore image URL is rendered, and the others need a live
 * snapshot to mount. Any `<img src={…}>` whose expression still reads a
 * `thumbnail` or `serverIcon` field directly has skipped the allowlist.
 */
describe("every Firestore image goes through the allowlist", () => {
  const files = import.meta.glob<string>("../**/*.tsx", {
    query: "?raw",
    import: "default",
    eager: true,
  });

  const componentFiles = Object.entries(files).filter(([path]) => !path.includes(".test."));

  it("finds the component sources (the sweep is not vacuous)", () => {
    expect(componentFiles.length).toBeGreaterThan(20);
    expect(componentFiles.some(([path]) => path.endsWith("/NowPlaying.tsx"))).toBe(true);
  });

  it("no <img> takes a thumbnail or serverIcon field straight from the data", () => {
    const offenders: string[] = [];
    for (const [path, source] of componentFiles) {
      for (const match of source.matchAll(/src=\{([^}]*)\}/g)) {
        if (/\b(thumbnail|serverIcon|artworkUrl|iconUrl)\b/.test(match[1])) {
          offenders.push(`${path}: src={${match[1]}}`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  it("each file that renders such an image imports the helper", () => {
    const rendersArtwork = componentFiles.filter(
      ([, source]) => /<img\b/.test(source) && /\b(thumbnail|serverIcon)\b/.test(source),
    );
    expect(rendersArtwork.length).toBeGreaterThanOrEqual(10);
    for (const [path, source] of rendersArtwork) {
      expect(source, path).toMatch(/safeArtworkUrl/);
    }
  });
});
