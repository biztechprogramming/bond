import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { mkdirSync, writeFileSync, existsSync, readFileSync, readdirSync, rmSync } from "fs";
import { join } from "path";
import { tmpdir } from "os";
import { AllowList } from "../channels/allowlist.js";
import type { ChannelMessage } from "../channels/base.js";

// --- Baileys mock ---
let connectionHandler: ((update: any) => void) | null = null;
let credsHandler: (() => void) | null = null;
let messagesHandler: ((data: any) => void) | null = null;
let mockSocketUser: any = null;
let mockEndCalled = false;

const mockEv = {
  on: vi.fn((event: string, handler: Function) => {
    if (event === "connection.update") connectionHandler = handler as any;
    if (event === "creds.update") credsHandler = handler as any;
    if (event === "messages.upsert") messagesHandler = handler as any;
  }),
  removeAllListeners: vi.fn(),
};

const mockSocket = {
  ev: mockEv,
  get user() { return mockSocketUser; },
  sendMessage: vi.fn(),
  end: vi.fn(() => { mockEndCalled = true; }),
};

vi.mock("@whiskeysockets/baileys", () => {
  return {
    default: vi.fn(() => mockSocket),
    useMultiFileAuthState: vi.fn(async () => ({
      state: { creds: {}, keys: { get: vi.fn(), set: vi.fn() } },
      saveCreds: vi.fn(),
    })),
    DisconnectReason: {
      loggedOut: 401,
      restartRequired: 515,
      connectionClosed: 428,
      connectionLost: 408,
      timedOut: 408,
    },
  };
});

vi.mock("@hapi/boom", () => ({
  Boom: class Boom {
    output: any;
    constructor(message?: string, options?: any) {
      this.output = { statusCode: options?.statusCode || 500 };
    }
  },
}));

import { WhatsAppChannel } from "../channels/whatsapp.js";

function makeTmpAuthDir(): string {
  const dir = join(tmpdir(), `whatsapp-test-${Date.now()}-${Math.random().toString(36).slice(2)}`);
  mkdirSync(dir, { recursive: true });
  return dir;
}

function cleanDir(dir: string): void {
  try { rmSync(dir, { recursive: true, force: true }); } catch {}
}

describe("WhatsAppChannel", () => {
  let channel: WhatsAppChannel;
  let messages: ChannelMessage[];
  let statusChanges: string[];
  let qrCodes: string[];
  let authDir: string;

  beforeEach(() => {
    vi.clearAllMocks();
    connectionHandler = null;
    credsHandler = null;
    messagesHandler = null;
    mockSocketUser = null;
    mockEndCalled = false;

    authDir = makeTmpAuthDir();
    messages = [];
    statusChanges = [];
    qrCodes = [];

    channel = new WhatsAppChannel({
      authDir,
      allowList: new AllowList(["sender@s.whatsapp.net"]),
      onMessage: (msg) => messages.push(msg),
      onQR: (qr) => qrCodes.push(qr),
      onStatusChange: (status) => statusChanges.push(status),
    });
  });

  afterEach(async () => {
    try { await channel.stop(); } catch {}
    cleanDir(authDir);
  });

  it("has channelType whatsapp", () => {
    expect(channel.channelType).toBe("whatsapp");
  });

  it("starts and sets running state", async () => {
    await channel.start();
    expect(channel.isRunning()).toBe(true);
    expect(channel.isConnected()).toBe(false); // not connected until "open" event
  });

  it("does not report connected until connection.open fires", async () => {
    await channel.start();
    // Socket user is set (simulating creds being loaded) but connection isn't open
    mockSocketUser = { id: "123@s.whatsapp.net", name: "Test" };
    expect(channel.isConnected()).toBe(false);

    // Fire open event
    connectionHandler?.({ connection: "open" });
    expect(channel.isConnected()).toBe(true);
  });

  it("reports disconnected after connection.close", async () => {
    await channel.start();
    connectionHandler?.({ connection: "open" });
    expect(channel.isConnected()).toBe(true);

    connectionHandler?.({
      connection: "close",
      lastDisconnect: { error: { output: { statusCode: 401 } } },
    });
    expect(channel.isConnected()).toBe(false);
  });

  it("stops cleanly and cancels reconnect", async () => {
    await channel.start();
    connectionHandler?.({ connection: "open" });
    await channel.stop();

    expect(channel.isRunning()).toBe(false);
    expect(channel.isConnected()).toBe(false);
    expect(mockEv.removeAllListeners).toHaveBeenCalledWith("connection.update");
    expect(mockEv.removeAllListeners).toHaveBeenCalledWith("creds.update");
    expect(mockEv.removeAllListeners).toHaveBeenCalledWith("messages.upsert");
  });

  it("cleans up old socket on reconnect (destroySocket called)", async () => {
    await channel.start();
    const firstEndCalls = mockSocket.end.mock.calls.length;

    // Simulate a non-fatal close to trigger reconnect
    connectionHandler?.({
      connection: "close",
      lastDisconnect: { error: { output: { statusCode: 428 } } },
    });

    // After the reconnect timer fires and connect() is called,
    // the old socket should have been ended
    // (We can't easily wait for the timer, but we can verify stop cleans up)
    await channel.stop();
    expect(mockSocket.end.mock.calls.length).toBeGreaterThan(firstEndCalls);
  });

  it("does not count 515 restart-required as a reconnect failure", async () => {
    await channel.start();

    // Simulate 515 — should reconnect immediately without incrementing counter
    connectionHandler?.({
      connection: "close",
      lastDisconnect: { error: { output: { statusCode: 515 } } },
    });

    // Channel should still be running (not given up)
    expect(channel.isRunning()).toBe(true);
  });

  it("gives up after max reconnect attempts on real failures", async () => {
    await channel.start();

    // Simulate 10+ connection failures (non-515)
    for (let i = 0; i < 11; i++) {
      connectionHandler?.({
        connection: "close",
        lastDisconnect: { error: { output: { statusCode: 408 } } },
      });
    }

    // Eventually gives up
    expect(channel.isRunning()).toBe(false);
  });

  it("does not reconnect on loggedOut (401)", async () => {
    await channel.start();
    connectionHandler?.({
      connection: "close",
      lastDisconnect: { error: { output: { statusCode: 401 } } },
    });
    expect(channel.isRunning()).toBe(false);
  });

  it("emits QR codes", async () => {
    await channel.start();
    connectionHandler?.({ qr: "test-qr-data" });
    expect(qrCodes).toEqual(["test-qr-data"]);
  });

  it("emits status changes", async () => {
    await channel.start();
    connectionHandler?.({ connection: "connecting" });
    connectionHandler?.({ connection: "open" });
    expect(statusChanges).toContain("connecting");
    expect(statusChanges).toContain("open");
  });

  it("forwards messages from allowed senders", async () => {
    await channel.start();
    connectionHandler?.({ connection: "open" });

    messagesHandler?.({
      messages: [{
        message: { conversation: "hello" },
        key: { remoteJid: "sender@s.whatsapp.net", fromMe: false },
        pushName: "Sender",
      }],
    });

    expect(messages).toHaveLength(1);
    expect(messages[0].content).toBe("hello");
    expect(messages[0].channelType).toBe("whatsapp");
  });

  it("ignores messages from non-allowed senders", async () => {
    await channel.start();
    messagesHandler?.({
      messages: [{
        message: { conversation: "hello" },
        key: { remoteJid: "stranger@s.whatsapp.net", fromMe: false },
      }],
    });
    expect(messages).toHaveLength(0);
  });

  it("ignores own messages", async () => {
    await channel.start();
    messagesHandler?.({
      messages: [{
        message: { conversation: "hello" },
        key: { remoteJid: "sender@s.whatsapp.net", fromMe: true },
      }],
    });
    expect(messages).toHaveLength(0);
  });
});

describe("WhatsAppChannel static methods", () => {
  let authDir: string;

  beforeEach(() => {
    authDir = makeTmpAuthDir();
  });

  afterEach(() => {
    cleanDir(authDir);
  });

  it("hasValidCreds returns false for missing dir", () => {
    expect(WhatsAppChannel.hasValidCreds("/nonexistent/path")).toBe(false);
  });

  it("hasValidCreds returns false for empty creds.json", () => {
    writeFileSync(join(authDir, "creds.json"), "");
    expect(WhatsAppChannel.hasValidCreds(authDir)).toBe(false);
  });

  it("hasValidCreds returns false for invalid JSON", () => {
    writeFileSync(join(authDir, "creds.json"), "{broken");
    expect(WhatsAppChannel.hasValidCreds(authDir)).toBe(false);
  });

  it("hasValidCreds returns false for creds without noise key", () => {
    writeFileSync(join(authDir, "creds.json"), JSON.stringify({ registrationId: 1 }));
    expect(WhatsAppChannel.hasValidCreds(authDir)).toBe(false);
  });

  it("hasValidCreds returns true for valid creds", () => {
    writeFileSync(join(authDir, "creds.json"), JSON.stringify({
      noiseKey: { private: "abc", public: "def" },
    }));
    expect(WhatsAppChannel.hasValidCreds(authDir)).toBe(true);
  });

  it("cleanAuthDir removes all files", () => {
    writeFileSync(join(authDir, "creds.json"), "{}");
    writeFileSync(join(authDir, "pre-key-1.json"), "{}");
    writeFileSync(join(authDir, "pre-key-2.json"), "{}");

    WhatsAppChannel.cleanAuthDir(authDir);

    const remaining = readdirSync(authDir);
    expect(remaining).toHaveLength(0);
  });

  it("cleanAuthDir handles nonexistent dir gracefully", () => {
    expect(() => WhatsAppChannel.cleanAuthDir("/nonexistent/path")).not.toThrow();
  });
});
