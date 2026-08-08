import { describe, expect, it, vi } from "vitest";
import { loadThumbnail } from "../src/thumbnail";

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
});
