import { describe, it, expect, vi, beforeEach } from "vitest";
import { AllowList } from "../channels/allowlist.js";
import type { ChannelMessage } from "../channels/base.js";

// Message handler registry for the mock Client
const messageHandlers: Function[] = [];
let mockUser: { id: string; username: string } | null = { id: "bot123", username: "TestBot" };

const mockClientInstance = {
  user: null as { id: string; username: string } | null,
  mentions: { has: vi.fn() },
  on: vi.fn((event: string, handler: Function) => {
    if (event === "messageCreate") messageHandlers.push(handler);
  }),
  login: vi.fn(async (_token: string) => {
    mockClientInstance.user = mockUser;
  }),
  destroy: vi.fn(),
  channels: {
    fetch: vi.fn(async (id: string) => ({
      isTextBased: () => true,
      send: vi.fn(),
    })),
  },
};

vi.mock("discord.js", () => {
  class MockClient {
    user: typeof mockUser = null;
    on = mockClientInstance.on;
    login = vi.fn(async (token: string) => {
      this.user = mockUser;
    });
    destroy = vi.fn();
    channels = mockClientInstance.channels;
  }
  return {
    Client: MockClient,
    GatewayIntentBits: {
      Guilds: 1,
      GuildMessages: 2,
      DirectMessages: 4,
      MessageContent: 8,
    },
    Events: {
      MessageCreate: "messageCreate",
    },
  };
});

import { DiscordChannel } from "../channels/discord.js";

describe("DiscordChannel", () => {
  let channel: DiscordChannel;
  let messages: ChannelMessage[];
  let persistedAllowList: string[];
  let allowList: AllowList;

  beforeEach(() => {
    vi.clearAllMocks();
    messageHandlers.length = 0;
    messages = [];
    persistedAllowList = [];
    allowList = new AllowList([]);

    channel = new DiscordChannel({
      token: "test-token",
      allowList,
      onMessage: (msg) => messages.push(msg),
      persistFn: (ids) => { persistedAllowList = ids; },
    });
  });

  it("has channelType discord", () => {
    expect(channel.channelType).toBe("discord");
  });

  it("returns the allow list", () => {
    expect(channel.getAllowList()).toBe(allowList);
  });

  it("is not running before start", () => {
    expect(channel.isRunning()).toBe(false);
  });

  it("is running after start and not running after stop", async () => {
    await channel.start();
    expect(channel.isRunning()).toBe(true);
    await channel.stop();
    expect(channel.isRunning()).toBe(false);
  });

  it("auto-adds first user (owner bootstrap) on DM", async () => {
    await channel.start();

    // Simulate a DM message from a new user
    const message = {
      author: { id: "user123", username: "owner", bot: false },
      guild: null, // DM — no guild
      content: "hello",
      channel: { id: "dm-channel-1" },
      mentions: { has: () => false },
      reply: vi.fn(),
    };

    // Trigger the messageCreate handler registered during start()
    for (const handler of messageHandlers) {
      await handler(message);
    }

    expect(allowList.isAllowed("user123")).toBe(true);
    expect(persistedAllowList).toContain("user123");
    expect(messages).toHaveLength(1);
    expect(messages[0].channelType).toBe("discord");
    expect(messages[0].senderId).toBe("user123");
    expect(messages[0].content).toBe("hello");
  });

  it("rejects messages from non-allowed senders", async () => {
    allowList.add("owner999");
    await channel.start();

    const message = {
      author: { id: "stranger", username: "stranger", bot: false },
      guild: null,
      content: "let me in",
      channel: { id: "dm-channel-2" },
      mentions: { has: () => false },
      reply: vi.fn(),
    };

    for (const handler of messageHandlers) {
      await handler(message);
    }

    expect(messages).toHaveLength(0);
    expect(message.reply).toHaveBeenCalledWith("Sorry, you're not on the allow list.");
  });

  it("ignores messages from bots", async () => {
    await channel.start();

    const message = {
      author: { id: "otherbot", username: "OtherBot", bot: true },
      guild: null,
      content: "i am a bot",
      channel: { id: "dm-channel-3" },
      mentions: { has: () => false },
      reply: vi.fn(),
    };

    for (const handler of messageHandlers) {
      await handler(message);
    }

    expect(messages).toHaveLength(0);
  });

  it("strips @mention from content", async () => {
    allowList.add("user456");
    await channel.start();

    const message = {
      author: { id: "user456", username: "mentioner", bot: false },
      guild: { id: "guild1" }, // In a guild (not a DM)
      content: "<@!123456789> hello there",
      channel: { id: "channel-1" },
      mentions: { has: () => true },
      reply: vi.fn(),
    };

    for (const handler of messageHandlers) {
      await handler(message);
    }

    expect(messages).toHaveLength(1);
    expect(messages[0].content).toBe("hello there");
  });

  it("ignores guild messages without @mention", async () => {
    allowList.add("user789");
    await channel.start();

    const message = {
      author: { id: "user789", username: "chatter", bot: false },
      guild: { id: "guild1" },
      content: "just chatting",
      channel: { id: "channel-2" },
      mentions: { has: () => false },
      reply: vi.fn(),
    };

    for (const handler of messageHandlers) {
      await handler(message);
    }

    expect(messages).toHaveLength(0);
  });
});
