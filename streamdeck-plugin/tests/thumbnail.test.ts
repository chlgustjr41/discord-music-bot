import { describe, expect, it, vi } from "vitest";
import { loadThumbnail, smallThumbnailUrl } from "../src/thumbnail";

function imageRes(bytes: number, type = "image/jpeg") {
  return {
    ok: true,
    status: 200,
    headers: { get: (h: string) => (h === "content-type" ? type : null) },
    arrayBuffer: async () => new ArrayBuffer(bytes),
  };
}

describe("loadThumbnail", () => {
  it("returns a data URI for a normal image", async () => {
    const f = vi.fn(async () => imageRes(8)) as unknown as typeof fetch;
    const uri = await loadThumbnail("https://i/t.jpg", f);
    expect(uri).toMatch(/^data:image\/jpeg;base64,/);
  });

  it("returns null on a non-2xx response", async () => {
    const f = vi.fn(async () => ({
      ok: false, status: 404,
      headers: { get: () => null },
      arrayBuffer: async () => new ArrayBuffer(0),
    })) as unknown as typeof fetch;
    expect(await loadThumbnail("https://i/missing.jpg", f)).toBeNull();
  });

  it("rejects a payload past the size ceiling", async () => {
    const f = vi.fn(async () => imageRes(3 * 1024 * 1024)) as unknown as typeof fetch;
    expect(await loadThumbnail("https://i/huge.jpg", f)).toBeNull();
  });

  it("rejects a non-image content type", async () => {
    const f = vi.fn(async () => imageRes(8, "text/html")) as unknown as typeof fetch;
    expect(await loadThumbnail("https://i/page.html", f)).toBeNull();
  });

  it("rejects an oversized payload before buffering it", async () => {
    const arrayBuffer = vi.fn(async () => new ArrayBuffer(8));
    const f = vi.fn(async () => ({
      ok: true,
      status: 200,
      headers: {
        get: (h: string) =>
          h === "content-type" ? "image/jpeg" : String(3 * 1024 * 1024),
      },
      arrayBuffer,
    })) as unknown as typeof fetch;
    expect(await loadThumbnail("https://i/huge.jpg", f)).toBeNull();
    expect(arrayBuffer).not.toHaveBeenCalled();
  });

  it("rejects an empty body", async () => {
    const f = vi.fn(async () => ({
      ok: true,
      status: 200,
      headers: { get: (h: string) => (h === "content-type" ? "image/jpeg" : null) },
      arrayBuffer: async () => new ArrayBuffer(0),
    })) as unknown as typeof fetch;
    expect(await loadThumbnail("https://i/empty.jpg", f)).toBeNull();
  });

  it("returns null when the request throws", async () => {
    const f = vi.fn(async () => {
      throw new Error("offline");
    }) as unknown as typeof fetch;
    expect(await loadThumbnail("https://i/t.jpg", f)).toBeNull();
  });

  it("fetches the small variant, not the one the server named", async () => {
    // The bug: a 1280x720 maxresdefault encodes to ~258,000 characters and is
    // pushed over the websocket to render on a 72-pixel key.
    const f = vi.fn(async () => imageRes(8)) as unknown as typeof fetch;
    await loadThumbnail("https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg", f);
    expect(vi.mocked(f).mock.calls[0][0]).toBe(
      "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg",
    );
  });
});

describe("smallThumbnailUrl", () => {
  const VARIANTS = ["maxresdefault", "hqdefault", "sddefault", "default"];

  it.each(VARIANTS)("rewrites the %s variant down to mqdefault", (variant) => {
    expect(smallThumbnailUrl(`https://i.ytimg.com/vi/dQw4w9WgXcQ/${variant}.jpg`)).toBe(
      "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg",
    );
  });

  it("leaves an already-small variant alone", () => {
    const url = "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg";
    expect(smallThumbnailUrl(url)).toBe(url);
  });

  it("rewrites the webp form without changing its extension", () => {
    expect(smallThumbnailUrl("https://i.ytimg.com/vi_webp/abc123/maxresdefault.webp")).toBe(
      "https://i.ytimg.com/vi_webp/abc123/mqdefault.webp",
    );
  });

  it("leaves a non-YouTube url untouched", () => {
    // An unknown host has no variant vocabulary to rewrite into; the size cap
    // is what makes leaving it alone safe.
    const url = "https://covers.test/artist/album/front-cover.jpg";
    expect(smallThumbnailUrl(url)).toBe(url);
  });

  it("leaves a ytimg path it does not recognise untouched", () => {
    const url = "https://i.ytimg.com/an/unexpected/path.jpg";
    expect(smallThumbnailUrl(url)).toBe(url);
  });

  it("hands back anything that is not a url at all", () => {
    expect(smallThumbnailUrl("not a url")).toBe("not a url");
  });
});
