import { describe, it, expect, vi, beforeEach } from "vitest";
import { AllowList } from "../channels/allowlist.js";
import type { ChannelMessage } from "../channels/base.js";

// Mock grammy before importing TelegramChannel
const handlers: Record<string, Function[]> = {};
const mockApi = {
  sendMessage: vi.fn(),
  getMe: vi.fn(async () => ({ id: 123, username: "testbot", first_name: "Test", is_bot: true })),
};

vi.mock("grammy", () => {
  class MockBot {
    api = mockApi;
    command(cmd: string, handler: Function) {
      const key = `command:${cmd}`;
      handlers[key] = handlers[key] || [];
      handlers[key].push(handler);
    }
    on(event: string, handler: Function) {
      handlers[event] = handlers[event] || [];
      handlers[event].push(handler);
    }
    async start(opts?: { onStart?: () => void }) {
      opts?.onStart?.();
    }
    async stop() {}
  }
  return { Bot: MockBot };
});

import { TelegramChannel } from "../channels/telegram.js";

describe("TelegramChannel", () => {
  let channel: TelegramChannel;
  let messages: ChannelMessage[];
  let persistedAllowList: string[];
  let allowList: AllowList;

  beforeEach(() => {
    vi.clearAllMocks();
    // Clear handlers
    for (const key of Object.keys(handlers)) delete handlers[key];
    messages = [];
    persistedAllowList = [];
    allowList = new AllowList([]);

    channel = new TelegramChannel({
      token: "test-token",
      allowList,
      onMessage: (msg) => messages.push(msg),
      persistFn: (ids) => { persistedAllowList = ids; },
    });
  });

  it("has channelType telegram", () => {
    expect(channel.channelType).toBe("telegram");
  });

  it("validates token via static method", async () => {
    const info = await TelegramChannel.validateToken("test-token");
    expect(info.username).toBe("testbot");
  });

  it("starts and stops without error", async () => {
    await channel.start();
    expect(channel.isRunning()).toBe(true);
    await channel.stop();
    expect(channel.isRunning()).toBe(false);
  });

  it("/start command adds sender to allow list", async () => {
    await channel.start();

    const startHandlers = handlers["command:start"];
    expect(startHandlers).toBeDefined();

    const ctx = {
      from: { id: 42, username: "testuser", first_name: "Test" },
      reply: vi.fn(),
    };
    await startHandlers[0](ctx);

    expect(allowList.isAllowed("42")).toBe(true);
    expect(ctx.reply).toHaveBeenCalledWith("You're connected! Only you can talk to me.");
    expect(persistedAllowList).toContain("42");
  });

  it("rejects messages from non-allowed senders", async () => {
    await channel.start();

    const messageHandlers = handlers["message:text"];
    expect(messageHandlers).toBeDefined();

    const ctx = {
      from: { id: 99 },
      chat: { id: 99 },
      message: { text: "hello" },
      reply: vi.fn(),
    };
    await messageHandlers[0](ctx);

    expect(messages).toHaveLength(0);
    expect(ctx.reply).toHaveBeenCalledWith("Sorry, you're not on the allow list.");
  });

  it("forwards messages from allowed senders", async () => {
    allowList.add("42");
    await channel.start();

    const messageHandlers = handlers["message:text"];
    const ctx = {
      from: { id: 42, username: "owner", first_name: "Owner" },
      chat: { id: 42 },
      message: { text: "hello bot" },
      reply: vi.fn(),
    };
    await messageHandlers[0](ctx);

    expect(messages).toHaveLength(1);
    expect(messages[0].channelType).toBe("telegram");
    expect(messages[0].senderId).toBe("42");
    expect(messages[0].content).toBe("hello bot");
  });

  it("chunks long messages when sending", async () => {
    await channel.start();
    const longMsg = "x".repeat(5000);
    await channel.send("42", longMsg);

    expect(mockApi.sendMessage).toHaveBeenCalledTimes(2);
  });
});
