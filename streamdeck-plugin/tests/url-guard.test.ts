import { describe, expect, it } from "vitest";
import { openableUrl } from "../src/url-guard";

describe("openableUrl", () => {
  it("allows https", () => {
    expect(openableUrl("https://web.test/dashboard/CODE1")).toBe(
      "https://web.test/dashboard/CODE1",
    );
  });

  // WHATWG parsing is not identity-preserving: it strips leading/trailing
  // C0-controls-and-space and removes tab/LF/CR from anywhere in the input.
  // Returning the parsed href is what makes "the string we checked" and "the
  // string we hand to the OS" the same string — the whole point of the guard.
  it("returns the normalized url, not the raw input", () => {
    expect(openableUrl(" https://good.test")).toBe("https://good.test/");
    expect(openableUrl("https://good.test\t/x")).toBe("https://good.test/x");
  });

  // The real escalation this guard exists to stop: these do not merely
  // navigate, they execute locally or hand off to another installed app.
  it.each([
    "javascript:alert(1)",
    "file:///C:/Windows/System32/calc.exe",
    "data:text/html,<script>alert(1)</script>",
    "steam://run/1",
    "http://web.test/dashboard/CODE1",
  ])("rejects %s", (url) => {
    expect(openableUrl(url)).toBeNull();
  });

  it.each(["", "not a url", "  ", "https://"])("rejects junk %s", (url) => {
    expect(openableUrl(url)).toBeNull();
  });

  it("is not fooled by a scheme appearing later in the string", () => {
    expect(openableUrl("javascript:void('https://web.test')")).toBeNull();
  });
});
