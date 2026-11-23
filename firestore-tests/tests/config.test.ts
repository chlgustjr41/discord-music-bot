/**
 * The tests are only worth anything if they hit the database the rules are
 * deployed to, with the rules file that gets deployed. Pin both.
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { doc, getDoc, setDoc } from "firebase/firestore";
import {
  DATABASE_ID,
  REPO_ROOT,
  RULES_PATH,
  admin,
  anon,
  asUser,
  assertFails,
  assertSucceeds,
  loadRules,
  pushRules,
} from "./helpers";

/** A minimal ruleset around one `match` block, for the loader checks below. */
const ruleset = (body: string) => `rules_version = '2';
service cloud.firestore {
  match /databases/{database}/documents {
    ${body}
  }
}
`;

describe("test wiring", () => {
  it("uses the database id that firebase.json deploys the rules to", () => {
    const firebaseJson = JSON.parse(
      readFileSync(path.join(REPO_ROOT, "firebase.json"), "utf8"),
    );
    expect(firebaseJson.firestore.database).toBe(DATABASE_ID);
    expect(firebaseJson.firestore.rules).toBe("firestore.rules");
    // A named database, not the default one — the whole reason these clients
    // are hand-built instead of coming from rules-unit-testing.
    expect(DATABASE_ID).not.toBe("(default)");
  });

  it("is testing the repository's own rules file (no RULES_FILE override)", () => {
    // Deliberately fails when RULES_FILE is set, so a run against some other
    // file can never be mistaken for a pass of the real one.
    expect(RULES_PATH).toBe(path.join(REPO_ROOT, "firestore.rules"));
  });

  it("is the same database the dashboard opens", () => {
    const source = readFileSync(
      path.join(REPO_ROOT, "frontend", "src", "firebase.ts"),
      "utf8",
    );
    const match = source.match(/getFirestore\(\s*app\s*,\s*"([^"]+)"\s*\)/);
    expect(match?.[1]).toBe(DATABASE_ID);
  });

  it("enforces the rules on that named database (default deny is live)", async () => {
    await assertFails(getDoc(doc(anon(), "anything", "at-all")));
    await assertFails(setDoc(doc(anon(), "anything", "at-all"), { a: 1 }));
    await assertFails(setDoc(doc(asUser("alice"), "anything", "at-all"), { a: 1 }));
  });

  it("the rules file the suite loads is what governs that database — not a no-op", async () => {
    // The trap this pins: rules-unit-testing's own loader reports success and
    // changes nothing on a named database, so a suite can run green against
    // rules it never loaded. Swap in a wide-open ruleset and the same write
    // must flip to allowed; put the real file back and it must flip to denied.
    const probe = () => setDoc(doc(anon(), "anything", "loader-probe"), { a: 1 });
    await assertFails(probe());
    try {
      await pushRules(ruleset("match /{document=**} { allow read, write: if true; }"));
      await assertSucceeds(probe());
    } finally {
      await loadRules();
    }
    await assertFails(probe());
  });

  it("a rules file that does not compile cleanly fails the run instead of loading", async () => {
    const wrap = (condition: string) => ruleset(`match /x/{y} { allow read: if ${condition}; }`);
    try {
      // A syntax error, and a WARNING-level issue (unknown function) that the
      // emulator would otherwise accept with HTTP 200.
      await expect(pushRules(wrap(""))).rejects.toThrow(/rejected the rules/);
      await expect(pushRules(wrap("noSuchFunction()"))).rejects.toThrow(/warnings/);
    } finally {
      await loadRules();
    }
    // Still the real rules afterwards.
    await assertFails(getDoc(doc(anon(), "anything", "at-all")));
  });

  it("lets the rules-exempt seeding client through, as the bot's Admin SDK is", async () => {
    await assertSucceeds(setDoc(doc(admin(), "anything", "at-all"), { a: 1 }));
    expect((await getDoc(doc(admin(), "anything", "at-all"))).data()).toEqual({ a: 1 });
  });
});

describe("collections only the bot uses", () => {
  it("controlTokens (Stream Deck token hashes) are unreachable from any client", async () => {
    await setDoc(doc(admin(), "controlTokens", "a".repeat(64)), { userId: "1" });
    for (const db of [anon(), asUser("alice")]) {
      await assertFails(getDoc(doc(db, "controlTokens", "a".repeat(64))));
      await assertFails(setDoc(doc(db, "controlTokens", "b".repeat(64)), { userId: "1" }));
    }
  });
});
