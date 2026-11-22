import { afterAll, beforeAll, beforeEach } from "vitest";
import { clearDatabase, loadRules, shutdown } from "./helpers";

// Registered for every test file (vitest setupFiles).
beforeAll(async () => {
  await loadRules();
});

// Each test starts from an empty database and seeds what it needs.
beforeEach(async () => {
  await clearDatabase();
});

afterAll(async () => {
  await shutdown();
});
