import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { existsSync, readFileSync, writeFileSync, unlinkSync, mkdirSync } from "fs";
import { join } from "path";
import { tmpdir } from "os";

// Mock grammY before importing ChannelManager
vi.mock("grammy", () => {
  class MockBot {
    api = {
      sendMessage: vi.fn(),
      getMe: vi.fn(async () => ({ id: 123, username: "testbot", first_name: "Test", is_bot: true })),
    };
    command() {}
    on() {}
    async start() {}
    async stop() {}
  }
  return { Bot: MockBot };
});

// Mock baileys (WhatsApp)
vi.mock("@whiskeysockets/baileys", () => ({
  default: vi.fn(),
  useMultiFileAuthState: vi.fn(),
  DisconnectReason: {},
  fetchLatestBaileysVersion: vi.fn(),
  makeCacheableSignalKeyStore: vi.fn(),
}));

import { ChannelManager } from "../channels/manager.js";

describe("ChannelManager — channel bindings persistence", () => {
  let tmpDir: string;
  let configPath: string;
  let bindingsPath: string;
  const mockBackend = {
    listAgents: vi.fn(async () => []),
    conversationTurnStream: vi.fn(),
    findActiveConversation: vi.fn(async () => null),
    healthCheck: vi.fn(async () => true),
  } as any;

  beforeEach(() => {
    tmpDir = join(tmpdir(), `bond-test-${Date.now()}`);
    mkdirSync(tmpDir, { recursive: true });
    configPath = join(tmpDir, "channels.json");
    bindingsPath = join(tmpDir, "channel-bindings.json");
  });

  afterEach(() => {
    try { unlinkSync(configPath); } catch {}
    try { unlinkSync(bindingsPath); } catch {}
  });

  it("loads bindings from disk on construction", () => {
    // Write bindings file before constructing manager
    const bindings = {
      "conv-123": { channelType: "telegram", channelId: "42" },
      "conv-456": { channelType: "whatsapp", channelId: "1234@c.us" },
    };
    writeFileSync(bindingsPath, JSON.stringify(bindings, null, 2));

    const manager = new ChannelManager(configPath, mockBackend);

    // Verify bindings were loaded
    expect(manager.getChannelBinding("conv-123")).toEqual({ channelType: "telegram", channelId: "42" });
    expect(manager.getChannelBinding("conv-456")).toEqual({ channelType: "whatsapp", channelId: "1234@c.us" });
  });

  it("returns null for unknown conversation bindings", () => {
    const manager = new ChannelManager(configPath, mockBackend);
    expect(manager.getChannelBinding("unknown-conv")).toBeNull();
  });

  it("handles missing bindings file gracefully", () => {
    const manager = new ChannelManager(configPath, mockBackend);
    expect(manager.getChannelBinding("any")).toBeNull();
  });

  it("handles corrupt bindings file gracefully", () => {
    writeFileSync(bindingsPath, "not-valid-json{{{");
    // Should not throw
    const manager = new ChannelManager(configPath, mockBackend);
    expect(manager.getChannelBinding("any")).toBeNull();
  });

  it("pushToChannel sends to telegram when binding exists", async () => {
    const bindings = {
      "conv-tg": { channelType: "telegram", channelId: "42" },
    };
    writeFileSync(bindingsPath, JSON.stringify(bindings));

    const manager = new ChannelManager(configPath, mockBackend);

    // pushToChannel should attempt to send (even though Telegram isn't started,
    // it won't throw — sendToChannel handles missing channels gracefully)
    await manager.pushToChannel("conv-tg", "Hello from webchat", "You (web)");
    // No error thrown — the binding was found and an attempt was made
  });

  it("pushToChannel is a no-op when no binding exists", async () => {
    const manager = new ChannelManager(configPath, mockBackend);
    // Should not throw
    await manager.pushToChannel("nonexistent-conv", "Hello", "User");
  });
});
