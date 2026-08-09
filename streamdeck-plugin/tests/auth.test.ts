import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { signIn, SignInError } from "../src/auth";

function res(status: number, body: unknown = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}

const START = res(200, {
  state: "st4te",
  authorizeUrl: "https://discord.example/authorize?state=st4te",
});

const RESULT = res(200, {
  token: "minted-token",
  discordUserId: "42",
  discordUserName: "jacob",
});

describe("signIn", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("opens the authorize URL and resolves once poll returns the token", async () => {
    const f = vi
      .fn()
      .mockResolvedValueOnce(START)
      .mockResolvedValueOnce(res(202, { status: "pending" }))
      .mockResolvedValueOnce(res(202, { status: "pending" }))
      .mockResolvedValueOnce(RESULT) as unknown as typeof fetch;
    const openUrl = vi.fn();

    const promise = signIn("https://control.example.com/", openUrl, f);
    await vi.advanceTimersByTimeAsync(6000);

    await expect(promise).resolves.toEqual({
      token: "minted-token",
      discordUserId: "42",
      discordUserName: "jacob",
    });
    expect(openUrl).toHaveBeenCalledWith(
      "https://discord.example/authorize?state=st4te",
    );
    const calls = (f as any).mock.calls;
    expect(calls[0][0]).toBe("https://control.example.com/control/auth/start");
    expect(calls[0][1].method).toBe("POST");
    // Both auth fetches carry a 10s abort timeout so a hung tunnel can't
    // stall the sign-in past its own deadline check (which only runs on 202).
    expect(calls[0][1].signal).toBeInstanceOf(AbortSignal);
    expect(calls[1][1].signal).toBeInstanceOf(AbortSignal);
    expect(calls[1][0]).toBe(
      "https://control.example.com/control/auth/poll?state=st4te",
    );
    expect(calls).toHaveLength(4);
  });

  it("rejects with status 410 when the state expires", async () => {
    const f = vi
      .fn()
      .mockResolvedValueOnce(START)
      .mockResolvedValueOnce(res(410, { error: "gone" })) as unknown as typeof fetch;

    const promise = signIn("https://control.example.com", vi.fn(), f);
    // The server's own error code rides along so the PI can explain the
    // cause (403 alone can't distinguish not-a-member from device-mismatch).
    const assertion = expect(promise).rejects.toMatchObject({
      status: 410,
      code: "gone",
    });
    await vi.advanceTimersByTimeAsync(2000);
    await assertion;
    await expect(promise).rejects.toBeInstanceOf(SignInError);
  });

  it("rejects with 408 after five minutes of pending polls", async () => {
    const f = vi
      .fn()
      .mockResolvedValueOnce(START)
      .mockResolvedValue(res(202, { status: "pending" })) as unknown as typeof fetch;

    const promise = signIn("https://control.example.com", vi.fn(), f);
    const assertion = expect(promise).rejects.toMatchObject({ status: 408 });
    await vi.advanceTimersByTimeAsync(5 * 60 * 1000 + 4000);
    await assertion;
  });

  it("refuses to open a javascript: authorize url", async () => {
    // /control/auth/start is pre-auth and apiUrl is user-overridable, so this
    // response body is the least trustworthy URL the plugin ever handles.
    // Failing the sign-in beats skipping the open: a sign-in that never opens
    // a browser but keeps polling would just hang for five minutes.
    const f = vi
      .fn()
      .mockResolvedValueOnce(
        res(200, { state: "st4te", authorizeUrl: "javascript:alert(1)" }),
      ) as unknown as typeof fetch;
    const openUrl = vi.fn();

    await expect(
      signIn("https://control.example.com", openUrl, f),
    ).rejects.toMatchObject({ code: "unsafe-authorize-url" });
    expect(openUrl).not.toHaveBeenCalled();
  });

  it("rejects when the start request fails", async () => {
    const f = vi
      .fn()
      .mockResolvedValueOnce(res(500)) as unknown as typeof fetch;
    const openUrl = vi.fn();

    await expect(
      signIn("https://control.example.com", openUrl, f),
    ).rejects.toMatchObject({ status: 500 });
    expect(openUrl).not.toHaveBeenCalled();
  });
});
