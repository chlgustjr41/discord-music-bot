/**
 * The two signed-in screens that touch `serverOwners`, against the tightened
 * rules: owner records are private, so "someone else's" now arrives as a
 * permission-denied error instead of a readable document. These pin that the
 * screens turn that into a sentence — no crash, no raw SDK message, no
 * uncaught listener error.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const fs = vi.hoisted(() => ({
  doc: vi.fn((...segments: unknown[]) => ({ path: segments.slice(1).join("/") })),
  collection: vi.fn((...segments: unknown[]) => ({ path: segments.slice(1).join("/") })),
  query: vi.fn((...args: unknown[]) => ({ query: args })),
  where: vi.fn((...args: unknown[]) => ({ where: args })),
  // Typed through the generic rather than by declaring parameters nobody
  // reads: `mock.calls[0][1]` needs the variadic signature to type-check.
  getDoc: vi.fn<(...args: unknown[]) => Promise<unknown>>(async () => ({ exists: () => false })),
  getDocs: vi.fn<(...args: unknown[]) => Promise<unknown>>(async () => ({ docs: [] })),
  setDoc: vi.fn<(...args: unknown[]) => Promise<void>>(async () => {}),
  updateDoc: vi.fn<(...args: unknown[]) => Promise<void>>(async () => {}),
  onSnapshot: vi.fn<(...args: unknown[]) => () => void>(() => () => {}),
  serverTimestamp: () => ({ __ts: true }),
}));

vi.mock("firebase/firestore", () => fs);
vi.mock("../firebase", () => ({ db: {}, auth: {} }));
// One stable function, as the real hook returns. MyServers lists `navigate`
// in an effect's dependencies; a fresh mock per render would re-run that
// effect forever and reload the server list out from under the assertions.
const navigate = vi.hoisted(() => vi.fn());
vi.mock("react-router-dom", () => ({ useNavigate: () => navigate }));

const user = { uid: "alice-uid", email: "alice@example.test" };
vi.mock("../hooks/useAuth", () => ({
  useAuth: () => ({
    user,
    loading: false,
    error: null,
    signInWithGoogle: vi.fn(),
    logout: vi.fn(),
  }),
}));

import { ActivateServer } from "./ActivateServer";
import { MyServers } from "./MyServers";

const GUILD = "123456789012345678";
const denied = () => Object.assign(new Error("Missing or insufficient permissions."), {
  code: "permission-denied",
});

beforeEach(() => {
  for (const mock of Object.values(fs)) {
    if (typeof mock === "function" && "mockClear" in mock) mock.mockClear();
  }
  fs.getDoc.mockImplementation(async () => ({ exists: () => false }));
  fs.getDocs.mockImplementation(async () => ({ docs: [] }));
  fs.setDoc.mockImplementation(async () => {});
  fs.onSnapshot.mockImplementation(() => () => {});
});

describe("ActivateServer", () => {
  const activate = (id: string) => {
    render(<ActivateServer />);
    fireEvent.change(screen.getByPlaceholderText("Discord Server ID"), {
      target: { value: id },
    });
    fireEvent.click(screen.getByRole("button", { name: "Activate Server" }));
  };

  it("activates an unclaimed server as the signed-in user", async () => {
    activate(`  ${GUILD}  `);
    await waitFor(() => expect(fs.setDoc).toHaveBeenCalledTimes(1));
    const [ref, data] = fs.setDoc.mock.calls[0] as [{ path: string }, Record<string, unknown>];
    expect(ref.path).toBe(`serverOwners/${GUILD}`);
    // Exactly the shape validOwnerRecord() in firestore.rules accepts.
    expect(data).toEqual({
      ownerDiscordId: "",
      ownerEmail: "alice@example.test",
      firebaseUid: "alice-uid",
      activatedAt: { __ts: true },
      isActive: true,
    });
    expect(await screen.findByText(/has\s+been activated/)).toBeDefined();
  });

  it.each(["my-cool-server", "12345", "12345678901234567a"])(
    "refuses %s before touching Firestore, and says what an ID looks like",
    async (id) => {
      activate(id);
      expect(await screen.findByText(/doesn't look like a Discord Server ID/)).toBeDefined();
      expect(fs.getDoc).not.toHaveBeenCalled();
      expect(fs.setDoc).not.toHaveBeenCalled();
    },
  );

  it("a server someone else activated reads as 'taken', not as a raw permission error", async () => {
    fs.getDoc.mockImplementation(async () => {
      throw denied();
    });
    activate(GUILD);
    expect(await screen.findByText(/already been activated by another account/)).toBeDefined();
    expect(screen.queryByText(/insufficient permissions/i)).toBeNull();
    // And it does not go on to attempt the overwrite.
    expect(fs.setDoc).not.toHaveBeenCalled();
  });

  it("losing the race between the check and the write is also 'taken'", async () => {
    fs.setDoc.mockImplementation(async () => {
      throw denied();
    });
    activate(GUILD);
    expect(await screen.findByText(/already been activated by another account/)).toBeDefined();
  });

  it("being offline is not reported as 'taken'", async () => {
    fs.getDoc.mockImplementation(async () => {
      throw Object.assign(new Error("client is offline"), { code: "unavailable" });
    });
    activate(GUILD);
    expect(await screen.findByText(/Couldn't check that server/)).toBeDefined();
    expect(screen.queryByText(/another account/)).toBeNull();
    expect(fs.setDoc).not.toHaveBeenCalled();
  });
});

describe("MyServers", () => {
  const owned = (...ids: string[]) => ({
    docs: ids.map((id) => ({ id, data: () => ({ isActive: true, firebaseUid: "alice-uid" }) })),
  });

  it("asks only for the signed-in user's own owner records", async () => {
    render(<MyServers />);
    await waitFor(() => expect(fs.getDocs).toHaveBeenCalled());
    // The rules refuse any serverOwners list not constrained to your own uid.
    expect(fs.where).toHaveBeenCalledWith("firebaseUid", "==", "alice-uid");
    expect(fs.collection.mock.calls.map((c) => c.slice(1).join("/"))).toEqual(
      expect.arrayContaining(["serverOwners", "users/alice-uid/pinnedServers"]),
    );
    // And it never lists the servers collection itself.
    expect(fs.collection.mock.calls.map((c) => c.slice(1).join("/"))).not.toContain("servers");
  });

  it("skips a record whose id is not a Discord server id instead of subscribing to it", async () => {
    fs.getDocs
      .mockImplementationOnce(async () => owned(GUILD, "my-cool-server"))
      .mockImplementationOnce(async () => ({ docs: [] }));
    render(<MyServers />);
    await waitFor(() => expect(fs.onSnapshot).toHaveBeenCalled());
    const paths = fs.onSnapshot.mock.calls.map((c) => (c[0] as { path: string }).path);
    expect(paths).toEqual([`servers/${GUILD}`]);
  });

  it("a server it may not read leaves a quiet card, not an uncaught listener error", async () => {
    fs.getDocs
      .mockImplementationOnce(async () => owned(GUILD))
      .mockImplementationOnce(async () => ({ docs: [] }));
    render(<MyServers />);
    await waitFor(() => expect(fs.onSnapshot).toHaveBeenCalledTimes(1));
    // Without an error callback the SDK logs "Uncaught Error in snapshot
    // listener" for every refused document.
    const onError = fs.onSnapshot.mock.calls[0][2] as ((err: unknown) => void) | undefined;
    expect(typeof onError).toBe("function");
    expect(() => onError!(denied())).not.toThrow();
    // The card is still there, identified by its id, in the idle state.
    expect(await screen.findByText(GUILD)).toBeDefined();
    expect(screen.getByText("No active session")).toBeDefined();
  });

  it("a refused owner query shows one message rather than crashing", async () => {
    fs.getDocs.mockImplementation(async () => {
      throw denied();
    });
    render(<MyServers />);
    expect(await screen.findByText("Could not load your servers.")).toBeDefined();
  });
});
