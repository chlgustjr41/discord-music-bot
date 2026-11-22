/**
 * servers/{guildId}: `get` one known document; never `list`; never create or
 * delete; `update` only the fields the dashboard writes, each bounded.
 *
 * Every "what the dashboard does" case below is a write lifted from
 * frontend/src — the file is named in the test title — so tightening the
 * rules past what the product needs fails here first.
 */

import { beforeEach, describe, expect, it } from "vitest";
import {
  collection,
  deleteDoc,
  deleteField,
  doc,
  documentId,
  getDoc,
  getDocs,
  limit,
  query,
  setDoc,
  updateDoc,
  where,
  type Firestore,
} from "firebase/firestore";
import {
  GUILD,
  OTHER_GUILD,
  SESSION_CODE,
  VOICE_CHANNEL,
  admin,
  anon,
  asUser,
  assertFails,
  assertSucceeds,
  firstSnapshot,
  serverDoc,
  track,
} from "./helpers";

const server = (db: Firestore, id = GUILD) => doc(db, "servers", id);

async function stored(id = GUILD): Promise<Record<string, unknown>> {
  return (await getDoc(server(admin(), id))).data() ?? {};
}

beforeEach(async () => {
  await setDoc(server(admin()), serverDoc());
});

// ── reads ─────────────────────────────────────────────────────────────────

describe("servers — reading", () => {
  it("useServerState.ts: a signed-out visitor can listen to one known server", async () => {
    const snap = await assertSucceeds(firstSnapshot(server(anon())));
    expect(snap.data()?.serverName).toBe("Test Guild");
  });

  it("SearchPanel / MusicHistory / social.ts: one-shot read of the queue", async () => {
    const snap = await assertSucceeds(getDoc(server(anon())));
    expect(snap.data()?.queue).toHaveLength(2);
  });

  it("MyServers.tsx: a signed-in user can listen to a server they own or pinned", async () => {
    await assertSucceeds(firstSnapshot(server(asUser("alice"))));
  });

  it.each([
    ["signed out", () => anon()],
    ["signed in", () => asUser("mallory")],
  ])("the servers collection cannot be listed (%s)", async (_who, db) => {
    await assertFails(getDocs(collection(db(), "servers")));
    await assertFails(getDocs(query(collection(db(), "servers"), limit(1))));
    await assertFails(firstSnapshot(collection(db(), "servers")));
  });

  it("no query shape gets around the list ban", async () => {
    const servers = collection(anon(), "servers");
    await assertFails(getDocs(query(servers, where(documentId(), "==", GUILD))));
    await assertFails(getDocs(query(servers, where("sessionCode", "==", SESSION_CODE))));
    await assertFails(getDocs(query(servers, where("voiceChannelId", "!=", null))));
  });

  it.each(["not-a-guild", "12345", "1234567890123456789012345", "12345678901234567a"])(
    "a document whose id is not a snowflake is unreadable and unwritable (%s)",
    async (id) => {
      await setDoc(server(admin(), id), serverDoc());
      await assertFails(getDoc(server(anon(), id)));
      await assertFails(updateDoc(server(anon(), id), { volume: 10 }));
      await assertFails(updateDoc(server(asUser("alice"), id), { volume: 10 }));
    },
  );
});

// ── create / delete ───────────────────────────────────────────────────────

describe("servers — create and delete belong to the bot", () => {
  it.each([
    ["signed out", () => anon()],
    ["signed in", () => asUser("mallory")],
  ])("a client cannot create a server document (%s)", async (_who, db) => {
    // Exactly what the bot's _init_state writes — shape is not the problem.
    await assertFails(setDoc(server(db(), OTHER_GUILD), serverDoc()));
    // The smallest thing the update rule would accept on an existing doc.
    await assertFails(setDoc(server(db(), OTHER_GUILD), { queue: [] }));
    await assertFails(setDoc(server(db(), OTHER_GUILD), { volume: 50 }, { merge: true }));
    // The summon shape, on a guild with no document yet.
    await assertFails(
      setDoc(
        server(db(), OTHER_GUILD),
        { summonRequest: { channelId: VOICE_CHANNEL } },
        { merge: true },
      ),
    );
    expect((await getDoc(server(admin(), OTHER_GUILD))).exists()).toBe(false);
  });

  it("an update aimed at a guild with no document does not create one", async () => {
    await expect(updateDoc(server(anon(), OTHER_GUILD), { volume: 50 })).rejects.toThrow();
    expect((await getDoc(server(admin(), OTHER_GUILD))).exists()).toBe(false);
  });

  it.each([
    ["signed out", () => anon()],
    ["signed in", () => asUser("mallory")],
  ])("a client cannot delete a server document (%s)", async (_who, db) => {
    await assertFails(deleteDoc(server(db())));
    expect((await getDoc(server(admin()))).exists()).toBe(true);
  });

  it("a client cannot replace the document wholesale (set without merge)", async () => {
    await assertFails(setDoc(server(anon()), { queue: [] }));
    await assertFails(setDoc(server(anon()), serverDoc({ sessionCode: "HACKED" })));
  });
});

// ── the writes the dashboard makes ────────────────────────────────────────

describe("servers — what the dashboard writes (signed out)", () => {
  const legit: [string, Record<string, unknown>][] = [
    ["Queue.tsx reorder / PlaybackControls.tsx shuffle", { queue: [track(2), track(1)] }],
    ["Queue.tsx remove one", { queue: [track(2)] }],
    ["Queue.tsx clear all", { queue: [] }],
    [
      "SearchPanel / MusicHistory / PlaylistManager / social.ts add tracks",
      { queue: [track(1), track(2), track(3), track(4)] },
    ],
    ["PlaybackControls.tsx skip", { currentTrack: null, isPlaying: true }],
    [
      "Queue.tsx play this track now",
      { queue: [track(2), track(1)], currentTrack: null, isPlaying: true },
    ],
    ["PlaybackControls.tsx pause", { isPaused: true }],
    ["PlaybackControls.tsx loop → track", { loopMode: "track" }],
    ["PlaybackControls.tsx loop → queue", { loopMode: "queue" }],
    ["PlaybackControls.tsx volume to 0 (mute)", { volume: 0 }],
    ["PlaybackControls.tsx volume to 100", { volume: 100 }],
    ["PlaybackControls.tsx volume to 55", { volume: 55 }],
    ["PlaybackControls.tsx Discord notifications off", { discordNotify: false }],
    ["NowPlaying.tsx seek", { seekPosition: 42 }],
    ["NowPlaying.tsx seek to the start", { seekPosition: 0 }],
    [
      "SearchPanel.tsx / useBotSearch.ts search",
      { searchQuery: "lofi hip hop radio", searchResults: [] },
    ],
    [
      "SearchPanel.tsx pasted link",
      { searchQuery: "https://www.youtube.com/watch?v=abc&list=PLxyz", searchResults: [] },
    ],
    [
      "CommandHistory.tsx re-run a command",
      {
        retriggerCommand: {
          command: "play",
          args: "never gonna give you up",
          triggeredAt: new Date().toISOString(),
        },
      },
    ],
    [
      "CommandHistory.tsx re-run a command with no arguments",
      { retriggerCommand: { command: "skip", args: "", triggeredAt: new Date().toISOString() } },
    ],
    ["Dashboard.tsx Reset", { resetRequested: true }],
    ["Dashboard.tsx Exit", { endSessionRequested: true }],
  ];

  it.each(legit)("%s", async (_label, patch) => {
    await assertSucceeds(updateDoc(server(anon()), patch));
    const after = await stored();
    for (const [key, value] of Object.entries(patch)) {
      expect(after[key]).toEqual(value);
    }
    // And nothing the bot owns moved.
    expect(after.sessionCode).toBe(SESSION_CODE);
    expect(after.voiceChannelId).toBe(VOICE_CHANNEL);
  });

  it("PlaybackControls.tsx resume, and the pause-then-unpause kick when idle", async () => {
    await assertSucceeds(updateDoc(server(anon()), { isPaused: true }));
    await assertSucceeds(updateDoc(server(anon()), { isPaused: false }));
  });

  it("PlaybackControls.tsx loop back to off, notifications back on", async () => {
    await setDoc(server(admin()), serverDoc({ loopMode: "queue", discordNotify: false }));
    await assertSucceeds(updateDoc(server(anon()), { loopMode: "off" }));
    await assertSucceeds(updateDoc(server(anon()), { discordNotify: true }));
  });

  it("Queue.tsx 'play now' while nothing is playing (isPlaying false → true)", async () => {
    await setDoc(server(admin()), serverDoc({ currentTrack: null, isPlaying: false }));
    await assertSucceeds(
      updateDoc(server(anon()), { queue: [track(2), track(1)], currentTrack: null, isPlaying: true }),
    );
  });

  it("a search while old results are still on the document", async () => {
    await setDoc(
      server(admin()),
      serverDoc({ searchResults: [{ videoId: "v", ...track(9) }], searchPlaylistName: "Mix" }),
    );
    await assertSucceeds(updateDoc(server(anon()), { searchQuery: "next", searchResults: [] }));
  });

  it("a first write to a field the bot never initialised (discordNotify, seekPosition)", async () => {
    const bare = serverDoc();
    delete bare.searchQuery;
    delete bare.searchResults;
    await setDoc(server(admin()), bare);
    await assertSucceeds(updateDoc(server(anon()), { discordNotify: false }));
    await assertSucceeds(updateDoc(server(anon()), { seekPosition: 10 }));
    await assertSucceeds(updateDoc(server(anon()), { searchQuery: "x", searchResults: [] }));
  });

  it("a legitimate write still works when the bot left an odd value in another field", async () => {
    // Validation is per CHANGED field: a float volume the bot wrote must not
    // lock the whole dashboard out of, say, pausing.
    await setDoc(server(admin()), serverDoc({ volume: 80.5, loopMode: "weird" }));
    await assertSucceeds(updateDoc(server(anon()), { isPaused: true }));
  });

  it("signed-in visitors can do all of the same", async () => {
    await assertSucceeds(updateDoc(server(asUser("alice")), { volume: 30, isPaused: true }));
  });
});

describe("servers — summon (My Servers, signed in)", () => {
  it("MyServers.tsx: a signed-in user can ask the bot to join a known channel", async () => {
    await assertSucceeds(
      updateDoc(server(asUser("alice")), { summonRequest: { channelId: VOICE_CHANNEL } }),
    );
    expect((await stored()).summonRequest).toEqual({ channelId: VOICE_CHANNEL });
  });

  it("a signed-out visitor cannot make the bot join a voice channel", async () => {
    await assertFails(
      updateDoc(server(anon()), { summonRequest: { channelId: VOICE_CHANNEL } }),
    );
  });

  it.each([
    ["non-numeric channel id (bot does int() on it)", { channelId: "general" }],
    ["channel id as a number", { channelId: 323456789012345 }],
    ["too short to be a snowflake", { channelId: "123" }],
    ["extra keys", { channelId: VOICE_CHANNEL, force: true }],
    ["empty map", {}],
    ["not a map", VOICE_CHANNEL],
    ["null (only the bot clears the request)", null],
  ])("rejects a malformed summon: %s", async (_label, summonRequest) => {
    await assertFails(updateDoc(server(asUser("alice")), { summonRequest }));
  });
});

// ── abuse ─────────────────────────────────────────────────────────────────

describe("servers — bot-owned fields are read-only to clients", () => {
  const botOwned: [string, Record<string, unknown>][] = [
    ["sessionCode", { sessionCode: "HACKED" }],
    ["sessionCode cleared", { sessionCode: null }],
    ["serverName", { serverName: "pwned" }],
    ["serverIcon (an image every viewer loads)", { serverIcon: "https://evil.example/t.gif" }],
    ["voiceChannelId (rejoined after a restart)", { voiceChannelId: "999999999999999999" }],
    ["voiceChannelName", { voiceChannelName: "x" }],
    ["textChannelId (where the bot posts)", { textChannelId: "999999999999999999" }],
    ["knownVoiceChannels (the summon menu)", { knownVoiceChannels: [{ id: "1", name: "x" }] }],
    ["idleTimeoutMinutes", { idleTimeoutMinutes: 99999 }],
    ["searchPlaylistName", { searchPlaylistName: "x" }],
    ["a brand-new field", { anything: 1 }],
    [
      "localNodeRequest (legacy v1 field; would park a password in a public document)",
      { localNodeRequest: { action: "connect", url: "https://x.example", password: "p" } },
    ],
    ["a nested path into a bot-owned map", { "knownVoiceChannels.0.name": "x" }],
  ];

  it.each(botOwned)("signed out: %s", async (_label, patch) => {
    await assertFails(updateDoc(server(anon()), patch));
  });

  it.each(botOwned)("signed in: %s", async (_label, patch) => {
    await assertFails(updateDoc(server(asUser("mallory")), patch));
  });

  it("even the activated owner cannot write them", async () => {
    await setDoc(doc(admin(), "serverOwners", GUILD), {
      firebaseUid: "alice",
      ownerEmail: "alice@example.test",
      ownerDiscordId: "",
      isActive: true,
    });
    await assertFails(updateDoc(server(asUser("alice")), { serverName: "mine" }));
    await assertFails(updateDoc(server(asUser("alice")), { sessionCode: "MINE00" }));
  });

  it("an allowed field does not smuggle a forbidden one through in the same write", async () => {
    await assertFails(updateDoc(server(anon()), { volume: 50, serverName: "pwned" }));
    await assertFails(updateDoc(server(anon()), { queue: [], sessionCode: null }));
    expect((await stored()).volume).toBe(80);
  });

  it("fields cannot be deleted, bot-owned or not", async () => {
    await assertFails(updateDoc(server(anon()), { sessionCode: deleteField() }));
    await assertFails(updateDoc(server(anon()), { voiceChannelId: deleteField() }));
    await assertFails(updateDoc(server(anon()), { volume: deleteField() }));
    await assertFails(updateDoc(server(anon()), { queue: deleteField() }));
    await assertFails(updateDoc(server(anon()), { currentTrack: deleteField() }));
  });
});

describe("servers — currentTrack / isPlaying may only be written as 'skip'", () => {
  it("a client cannot SET a current track (the bot replays its url after a restart)", async () => {
    await assertFails(
      updateDoc(server(anon()), {
        currentTrack: { ...track(9), url: "http://169.254.169.254/latest/meta-data/" },
      }),
    );
    await assertFails(
      updateDoc(server(anon()), { "currentTrack.url": "http://192.168.1.1/admin" }),
    );
    await assertFails(updateDoc(server(anon()), { "currentTrack.thumbnail": "https://evil.example/t.gif" }));
    await assertFails(updateDoc(server(anon()), { currentTrack: "x" }));
  });

  it("a client cannot clear isPlaying, or set it to a non-boolean", async () => {
    await assertFails(updateDoc(server(anon()), { isPlaying: false }));
    await assertFails(updateDoc(server(anon()), { isPlaying: "yes" }));
    await assertFails(updateDoc(server(anon()), { isPlaying: null }));
  });
});

describe("servers — type and range bounds on the writable fields", () => {
  const bad: [string, Record<string, unknown>][] = [
    ["volume below range", { volume: -1 }],
    ["volume above range", { volume: 101 }],
    ["volume far above range", { volume: 1_000_000 }],
    ["volume as a float", { volume: 50.5 }],
    ["volume as a string (int() in the bot would raise)", { volume: "50" }],
    ["volume as text", { volume: "loud" }],
    ["volume null", { volume: null }],
    ["volume NaN", { volume: NaN }],
    ["volume as a list", { volume: [50] }],
    ["loopMode outside the three modes", { loopMode: "shuffle" }],
    ["loopMode not a string", { loopMode: 1 }],
    ["isPaused not a boolean", { isPaused: "true" }],
    ["isPaused null", { isPaused: null }],
    ["discordNotify not a boolean", { discordNotify: 1 }],
    ["seekPosition negative", { seekPosition: -1 }],
    ["seekPosition beyond a day", { seekPosition: 86_401 }],
    ["seekPosition as a string", { seekPosition: "10" }],
    ["seekPosition NaN", { seekPosition: NaN }],
    ["seekPosition Infinity", { seekPosition: Infinity }],
    ["searchQuery empty", { searchQuery: "", searchResults: [] }],
    ["searchQuery oversized", { searchQuery: "q".repeat(501), searchResults: [] }],
    ["searchQuery not a string", { searchQuery: 42 }],
    ["searchResults planted by a client", { searchResults: [{ videoId: "v", ...track(9) }] }],
    ["searchResults not a list", { searchResults: "x" }],
    ["resetRequested false", { resetRequested: false }],
    ["resetRequested not a boolean", { resetRequested: "now" }],
    ["endSessionRequested false", { endSessionRequested: false }],
    ["endSessionRequested as a number", { endSessionRequested: 1 }],
    ["queue not a list (string)", { queue: "x" }],
    ["queue not a list (map)", { queue: { 0: track(1) } }],
    ["queue null", { queue: null }],
    ["queue over the cap", { queue: Array.from({ length: 1001 }, () => ({ t: 1 })) }],
  ];

  it.each(bad)("rejects %s", async (_label, patch) => {
    await assertFails(updateDoc(server(anon()), patch));
  });

  it("a client cannot clear the markers the bot clears when it has handled a request", async () => {
    // Each of these is pending on the document; nulling it would cancel
    // someone else's request before the bot acts on it.
    await setDoc(
      server(admin()),
      serverDoc({
        searchQuery: "pending search",
        seekPosition: 30,
        resetRequested: true,
        endSessionRequested: true,
        summonRequest: { channelId: VOICE_CHANNEL },
        retriggerCommand: { command: "skip", args: "", triggeredAt: "2026-11-21T00:00:00.000Z" },
      }),
    );
    for (const field of [
      "searchQuery",
      "seekPosition",
      "resetRequested",
      "endSessionRequested",
      "summonRequest",
      "retriggerCommand",
    ]) {
      await assertFails(updateDoc(server(anon()), { [field]: null }));
      await assertFails(updateDoc(server(asUser("mallory")), { [field]: null }));
      await assertFails(updateDoc(server(anon()), { [field]: deleteField() }));
    }
  });

  it("accepts the boundary values", async () => {
    await assertSucceeds(updateDoc(server(anon()), { seekPosition: 86_400 }));
    await assertSucceeds(
      updateDoc(server(anon()), { searchQuery: "q".repeat(500), searchResults: [] }),
    );
    await assertSucceeds(
      updateDoc(server(anon()), { queue: Array.from({ length: 1000 }, (_, i) => ({ t: i })) }),
    );
  });

  it("a queue the bot grew past the cap can still be reordered and shrunk, not grown", async () => {
    const big = Array.from({ length: 1200 }, (_, i) => ({ t: i }));
    await setDoc(server(admin()), serverDoc({ queue: big }));
    await assertSucceeds(updateDoc(server(anon()), { queue: [...big].reverse() }));
    await assertSucceeds(updateDoc(server(anon()), { queue: big.slice(1) }));
    await assertFails(updateDoc(server(anon()), { queue: [...big, { t: 1200 }] }));
  });
});

describe("servers — retriggerCommand shape", () => {
  const now = () => new Date().toISOString();
  const bad: [string, unknown][] = [
    ["a command the listener does not implement", { command: "playlist", args: "", triggeredAt: now() }],
    ["reset smuggled in as a command", { command: "reset", args: "", triggeredAt: now() }],
    ["command not a string", { command: 1, args: "", triggeredAt: now() }],
    ["args not a string", { command: "volume", args: 50, triggeredAt: now() }],
    ["args oversized", { command: "play", args: "a".repeat(2001), triggeredAt: now() }],
    ["triggeredAt oversized", { command: "skip", args: "", triggeredAt: "x".repeat(41) }],
    ["triggeredAt not a string", { command: "skip", args: "", triggeredAt: 0 }],
    ["missing args", { command: "skip", triggeredAt: now() }],
    ["missing triggeredAt", { command: "skip", args: "" }],
    ["an extra key", { command: "skip", args: "", triggeredAt: now(), as: "admin" }],
    ["not a map", "skip"],
    ["null (only the bot clears it)", null],
  ];

  it.each(bad)("rejects %s", async (_label, retriggerCommand) => {
    await assertFails(updateDoc(server(anon()), { retriggerCommand }));
  });

  it.each(["play", "skip", "pause", "resume", "loop", "volume"])(
    "accepts the retriggerable command %s",
    async (command) => {
      await assertSucceeds(
        updateDoc(server(anon()), {
          retriggerCommand: { command, args: command === "volume" ? "40" : "", triggeredAt: now() },
        }),
      );
    },
  );
});
