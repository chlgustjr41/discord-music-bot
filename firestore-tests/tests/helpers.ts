/**
 * Shared plumbing for the rules tests.
 *
 * Two things here are deliberate and easy to get wrong:
 *
 * 1. THE DATABASE IS NAMED. firebase.json deploys firestore.rules to the
 *    database `discord-music-bot`, and the dashboard opens that same database
 *    (frontend/src/firebase.ts). @firebase/rules-unit-testing only knows
 *    `(default)`, in BOTH halves of what it does:
 *      - its clients (`testEnv.authenticatedContext().firestore()`) open
 *        `(default)`, and
 *      - its rule loader (the `firestore.rules` option of its test
 *        environment) PUTs to the project-level endpoint, which changes what
 *        the emulator enforces on `(default)` and leaves a named database
 *        untouched.
 *    The second one is silent: the call succeeds and every test still runs,
 *    against whatever rules the emulator happened to start with. (Checked
 *    against firebase-tools 15.35.0 / emulator 1.22.0.)
 *    So this file does both jobs itself: clients are built with the ordinary
 *    modular SDK against the database id READ FROM firebase.json, and
 *    loadRules() PUTs the rules file to the database-scoped endpoint. The
 *    library is used only for assertFails / assertSucceeds. config.test.ts
 *    pins that loadRules() really governs the database under test.
 *
 * 2. NOTHING HERE CAN REACH PRODUCTION. Every client is wired to the emulator
 *    before it is returned, and this module refuses to load unless the
 *    emulator address is local and the project id is a `demo-` one (which the
 *    Firebase tooling itself refuses to resolve to a real project).
 */

import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { deleteApp, initializeApp, type FirebaseApp } from "firebase/app";
import {
  connectFirestoreEmulator,
  getFirestore,
  onSnapshot,
  setLogLevel,
  type DocumentReference,
  type DocumentSnapshot,
  type Firestore,
  type Query,
  type QuerySnapshot,
} from "firebase/firestore";

export { assertFails, assertSucceeds } from "@firebase/rules-unit-testing";

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = path.resolve(HERE, "..", "..");

const firebaseJson = JSON.parse(
  readFileSync(path.join(REPO_ROOT, "firebase.json"), "utf8"),
) as { firestore: { rules: string; database: string } };

/** The database the rules are DEPLOYED to — never hard-coded here. */
export const DATABASE_ID = firebaseJson.firestore.database;

/**
 * The rules file under test: the one firebase.json deploys, unless RULES_FILE
 * points somewhere else. The override exists for one purpose — proving the
 * suite has teeth by running it against a file that SHOULD fail (the previous
 * rules, or a deliberately weakened copy). See README.md.
 *
 * A run with the override set can never come out fully green: config.test.ts
 * has a test that fails whenever RULES_PATH is not the repository's file, so
 * "all passed" always means "the real rules passed".
 */
const rulesOverride = process.env.RULES_FILE;
export const RULES_PATH = rulesOverride
  ? path.resolve(rulesOverride)
  : path.join(REPO_ROOT, firebaseJson.firestore.rules);
if (rulesOverride) {
  console.warn(
    `\n*** RULES_FILE override active: testing ${RULES_PATH}, ` +
      `NOT the repository's ${firebaseJson.firestore.rules} ***\n`,
  );
}

export const PROJECT_ID = process.env.GCLOUD_PROJECT ?? "demo-jacky";

const emulatorHost = process.env.FIRESTORE_EMULATOR_HOST;
if (!emulatorHost) {
  throw new Error(
    "FIRESTORE_EMULATOR_HOST is not set. Run these through `npm test` in " +
      "firestore-tests/, which starts the emulator (see README.md).",
  );
}
const [HOST, portText] = emulatorHost.split(":");
const PORT = Number(portText);
if (!["127.0.0.1", "localhost", "[::1]"].includes(HOST) || !Number.isInteger(PORT)) {
  throw new Error(`Refusing to run against a non-local Firestore: ${emulatorHost}`);
}
if (!PROJECT_ID.startsWith("demo-")) {
  throw new Error(`Refusing to run against a non-demo project id: ${PROJECT_ID}`);
}

// A denied write is the expected outcome of half these tests; the SDK logs
// each one as a warning, which would bury the real output.
setLogLevel("silent");

// ── identities ────────────────────────────────────────────────────────────

const apps: FirebaseApp[] = [];
const cache = new Map<string, Firestore>();

type MockToken = string | { sub: string; [claim: string]: unknown };

function client(cacheKey: string, token?: MockToken): Firestore {
  const hit = cache.get(cacheKey);
  if (hit) return hit;
  const app = initializeApp({ projectId: PROJECT_ID }, `rules-test-${apps.length}`);
  apps.push(app);
  const db = getFirestore(app, DATABASE_ID);
  connectFirestoreEmulator(
    db,
    HOST,
    PORT,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    token === undefined ? undefined : { mockUserToken: token as any },
  );
  cache.set(cacheKey, db);
  return db;
}

/** Signed out: what a visitor with only a session link is. */
export function anon(): Firestore {
  return client("anon");
}

/** A signed-in Google account. `email` lands in request.auth.token.email. */
export function asUser(uid: string, claims: Record<string, unknown> = {}): Firestore {
  const token = {
    sub: uid,
    user_id: uid,
    email: `${uid}@example.test`,
    email_verified: true,
    ...claims,
  };
  return client(`user:${JSON.stringify(token)}`, token);
}

/**
 * Bypasses the rules, as the bot's Admin SDK does. Used ONLY to seed and to
 * inspect — the emulator treats the bearer token "owner" as rules-exempt,
 * which is exactly what rules-unit-testing's withSecurityRulesDisabled sends.
 */
export function admin(): Firestore {
  return client("admin", "owner");
}

// ── lifecycle ─────────────────────────────────────────────────────────────

const EMULATOR = `http://${HOST}:${PORT}/emulator/v1/projects/${PROJECT_ID}`;

/**
 * Make the emulator enforce `source` on the NAMED database.
 *
 * Throws unless the ruleset compiles with no issues at all. The emulator
 * answers 400 for a syntax error, but 200 plus a list of WARNINGS for things
 * like a call to a function that does not exist — rules that "load" and then
 * deny at evaluation time. A warning in firestore.rules is a bug, so it fails
 * the run here rather than surfacing as one mysteriously denied operation.
 */
export async function pushRules(source: string): Promise<void> {
  const res = await fetch(`${EMULATOR}/databases/${DATABASE_ID}:securityRules`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ rules: { files: [{ content: source }] } }),
  });
  const body = await res.text();
  if (!res.ok) {
    throw new Error(`the emulator rejected the rules (HTTP ${res.status}): ${body}`);
  }
  const issues = (JSON.parse(body || "{}") as { issues?: unknown[] }).issues ?? [];
  if (issues.length > 0) {
    throw new Error(`the rules compiled with warnings: ${JSON.stringify(issues, null, 2)}`);
  }
}

/** Load the rules file under test (RULES_PATH) into the named database. */
export async function loadRules(): Promise<void> {
  await pushRules(readFileSync(RULES_PATH, "utf8"));
}

/** Wipe the NAMED database (the library's clearFirestore only knows `(default)`). */
export async function clearDatabase(): Promise<void> {
  const res = await fetch(`${EMULATOR}/databases/${DATABASE_ID}/documents`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw new Error(`could not clear the emulator database: HTTP ${res.status}`);
  }
}

export async function shutdown(): Promise<void> {
  await Promise.all(apps.map((app) => deleteApp(app).catch(() => {})));
  apps.length = 0;
  cache.clear();
}

// ── real-time reads ───────────────────────────────────────────────────────

/**
 * First result of a real-time listener, as a promise.
 *
 * The dashboard reads almost everything through onSnapshot, not getDoc /
 * getDocs. The rules treat a document listener as `get` and a query listener
 * as `list`, and a few tests go through the listener so that equivalence is
 * exercised rather than assumed.
 */
export function firstSnapshot(ref: DocumentReference): Promise<DocumentSnapshot>;
export function firstSnapshot(ref: Query): Promise<QuerySnapshot>;
export function firstSnapshot(
  ref: DocumentReference | Query,
): Promise<DocumentSnapshot | QuerySnapshot> {
  return new Promise((resolve, reject) => {
    const unsub = onSnapshot(
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      ref as any,
      // Metadata changes included so the cache → server transition is
      // delivered even when the data itself did not change (a document this
      // same client just wrote is already in its cache).
      { includeMetadataChanges: true },
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      (snap: any) => {
        // Skip cache-only emissions: only a server answer proves the rules
        // let the read through.
        if (snap.metadata?.fromCache) return;
        unsub();
        resolve(snap);
      },
      (err: unknown) => reject(err),
    );
  });
}

// ── fixtures ──────────────────────────────────────────────────────────────

/** Snowflake-shaped ids. Not real guilds or channels. */
export const GUILD = "123456789012345678";
export const OTHER_GUILD = "223456789012345678";
export const VOICE_CHANNEL = "323456789012345678";
export const TEXT_CHANNEL = "423456789012345678";
export const SESSION_CODE = "ABC123";

export function track(n: number): Record<string, unknown> {
  return {
    title: `Track ${n}`,
    artist: `Artist ${n}`,
    url: `https://www.youtube.com/watch?v=video${n}`,
    thumbnail: `https://i.ytimg.com/vi/video${n}/mqdefault.jpg`,
    duration: 180 + n,
    requestedBy: "Bob",
  };
}

/** A server document as the bot leaves it mid-session (begin_session + play). */
export function serverDoc(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    sessionCode: SESSION_CODE,
    currentTrack: { ...track(0), startedAt: "2026-11-21T00:00:00+00:00" },
    queue: [track(1), track(2)],
    isPlaying: true,
    isPaused: false,
    loopMode: "off",
    volume: 80,
    voiceChannelId: VOICE_CHANNEL,
    voiceChannelName: "General",
    textChannelId: TEXT_CHANNEL,
    idleTimeoutMinutes: 5,
    knownVoiceChannels: [{ id: VOICE_CHANNEL, name: "General" }],
    serverName: "Test Guild",
    serverIcon: "https://cdn.discordapp.com/icons/123456789012345678/abc.png",
    searchQuery: null,
    searchResults: [],
    searchPlaylistName: null,
    ...overrides,
  };
}
