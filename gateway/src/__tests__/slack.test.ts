import { describe, it, expect, vi, beforeEach } from "vitest";
import { AllowList } from "../channels/allowlist.js";
import type { ChannelMessage } from "../channels/base.js";

// Registry for Bolt event handlers
const eventHandlers: Record<string, Function[]> = {};
const mockPostMessage = vi.fn(async () => ({ ok: true }));

vi.mock("@slack/bolt", () => {
  class MockApp {
    client = {
      chat: { postMessage: mockPostMessage },
    };
    event(name: string, handler: Function) {
      eventHandlers[name] = eventHandlers[name] || [];
      eventHandlers[name].push(handler);
    }
    async start() {}
    async stop() {}
  }
  return { App: MockApp };
});

vi.mock("@slack/web-api", () => {
  class MockWebClient {
    auth = {
      test: vi.fn(async () => ({
        ok: true,
        team_id: "T12345",
        team: "TestWorkspace",
        bot_id: "B12345",
      })),
    };
  }
  return { WebClient: MockWebClient };
});

import { SlackChannel } from "../channels/slack.js";

describe("SlackChannel", () => {
  let channel: SlackChannel;
  let messages: ChannelMessage[];
  let persistedAllowList: string[];
  let allowList: AllowList;

  beforeEach(() => {
    vi.clearAllMocks();
    for (const key of Object.keys(eventHandlers)) delete eventHandlers[key];
    messages = [];
    persistedAllowList = [];
    allowList = new AllowList([]);

    channel = new SlackChannel({
      botToken: "xoxb-test",
      appToken: "xapp-test",
      allowList,
      onMessage: (msg) => messages.push(msg),
      persistFn: (ids) => { persistedAllowList = ids; },
    });
  });

  it("has channelType slack", () => {
    expect(channel.channelType).toBe("slack");
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

  it("validates token via static method", async () => {
    const info = await SlackChannel.validateToken("xoxb-test");
    expect(info.teamId).toBe("T12345");
    expect(info.team).toBe("TestWorkspace");
    expect(info.botId).toBe("B12345");
  });

  it("auto-adds first DM sender (owner bootstrap)", async () => {
    await channel.start();

    const dmHandlers = eventHandlers["message"];
    expect(dmHandlers).toBeDefined();

    const say = vi.fn();
    const event = {
      channel_type: "im",
      user: "U001",
      text: "hello",
      channel: "D001",
      ts: "12345.67890",
    };

    await dmHandlers[0]({ event, say });

    expect(allowList.isAllowed("U001")).toBe(true);
    expect(persistedAllowList).toContain("u001");
    expect(messages).toHaveLength(1);
    expect(messages[0].channelType).toBe("slack");
    expect(messages[0].senderId).toBe("U001");
    expect(messages[0].content).toBe("hello");
  });

  it("rejects DMs from non-allowed senders", async () => {
    allowList.add("U999");
    await channel.start();

    const dmHandlers = eventHandlers["message"];
    const say = vi.fn();
    const event = {
      channel_type: "im",
      user: "U_STRANGER",
      text: "let me in",
      channel: "D002",
      ts: "12345.11111",
    };

    await dmHandlers[0]({ event, say });

    expect(messages).toHaveLength(0);
    expect(say).toHaveBeenCalledWith("Sorry, you're not on the allow list.");
  });

  it("ignores non-DM messages from message handler", async () => {
    allowList.add("U001");
    await channel.start();

    const dmHandlers = eventHandlers["message"];
    const say = vi.fn();
    const event = {
      channel_type: "channel", // Not a DM
      user: "U001",
      text: "hello channel",
      channel: "C001",
      ts: "12345.22222",
    };

    await dmHandlers[0]({ event, say });

    expect(messages).toHaveLength(0);
  });

  it("ignores bot messages", async () => {
    await channel.start();

    const dmHandlers = eventHandlers["message"];
    const say = vi.fn();
    const event = {
      channel_type: "im",
      user: "U001",
      bot_id: "B999", // Bot message
      text: "i am a bot",
      channel: "D003",
      ts: "12345.33333",
    };

    await dmHandlers[0]({ event, say });

    expect(messages).toHaveLength(0);
  });

  it("handles app_mention events", async () => {
    allowList.add("U001");
    await channel.start();

    const mentionHandlers = eventHandlers["app_mention"];
    expect(mentionHandlers).toBeDefined();

    const say = vi.fn();
    const event = {
      user: "U001",
      text: "<@UBOT> help me",
      channel: "C001",
      ts: "12345.44444",
    };

    await mentionHandlers[0]({ event, say });

    expect(messages).toHaveLength(1);
    expect(messages[0].channelType).toBe("slack");
    expect(messages[0].content).toBe("help me");
    expect(messages[0].senderId).toBe("U001");
  });

  it("sends messages in chunks of 4000 chars", async () => {
    await channel.start();
    const longText = "x".repeat(5000);
    await channel.send("C001", longText);

    expect(mockPostMessage).toHaveBeenCalledTimes(2);
  });
});
