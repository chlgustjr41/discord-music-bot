import { describe, expect, it } from "vitest";
import { isSnowflake } from "./limits";

// Mirrors isSnowflake() in firestore.rules — the rules are the authority; this
// only has to agree with them so the UI can explain a refusal in advance.
describe("isSnowflake", () => {
  it.each(["123456789012345678", "1234567890123456789", "123456789012345", "1234567890123456789012"])(
    "accepts %s",
    (id) => {
      expect(isSnowflake(id)).toBe(true);
    },
  );

  it.each([
    ["too short", "12345678901234"],
    ["too long", "12345678901234567890123"],
    ["letters", "12345678901234567a"],
    ["a name", "my-cool-server"],
    ["a path", "123456789012345678/x"],
    ["surrounding space", " 123456789012345678 "],
    ["a trailing newline", "123456789012345678\n"],
    ["empty", ""],
    ["a number, not a string", 1234567890123456],
    ["null", null],
    ["undefined", undefined],
  ])("rejects %s", (_label, id) => {
    expect(isSnowflake(id)).toBe(false);
  });
});
