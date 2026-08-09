import { describe, expect, it } from "vitest";
import { isOpenableUrl } from "../src/url-guard";

describe("isOpenableUrl", () => {
  it("allows https", () => {
    expect(isOpenableUrl("https://web.test/dashboard/CODE1")).toBe(true);
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
    expect(isOpenableUrl(url)).toBe(false);
  });

  it.each(["", "not a url", "  ", "https://"])("rejects junk %s", (url) => {
    expect(isOpenableUrl(url)).toBe(false);
  });

  it("is not fooled by a scheme appearing later in the string", () => {
    expect(isOpenableUrl("javascript:void('https://web.test')")).toBe(false);
  });
});
