import { describe, it, expect, vi } from "vitest";
import {
  MessagePipeline,
  RateLimitHandler,
  AuthHandler,
  AllowListHandler,
  AgentResolver,
  ContextLoader,
  TurnExecutor,
  Persister,
  ResponseFanOut,
} from "../pipeline/index.js";
import type { PipelineMessage, PipelineContext, PipelineHandler } from "../pipeline/index.js";
import { AllowList } from "../channels/allowlist.js";

function createTestMessage(overrides: Partial<PipelineMessage> = {}): PipelineMessage {
  return {
    id: "test-id",
    channelType: "webchat",
    channelId: "session-1",
    content: "Hello",
    timestamp: Date.now(),
    metadata: {},
    ...overrides,
  };
}

function createTestContext(overrides: Partial<PipelineContext> = {}): PipelineContext {
  return {
    aborted: false,
    respond: vi.fn().mockResolvedValue(undefined),
    broadcast: vi.fn().mockResolvedValue(undefined),
    streamChunk: vi.fn().mockResolvedValue(undefined),
    abort: vi.fn().mockImplementation(async function (this: PipelineContext) {
      this.aborted = true;
    }),
    emit: vi.fn().mockResolvedValue(undefined),
    ...overrides,
  };
}

describe("MessagePipeline", () => {
  it("executes handlers in order", async () => {
    const order: string[] = [];
    const pipeline = new MessagePipeline();

    const makeHandler = (name: string): PipelineHandler => ({
      name,
      async handle(_msg, _ctx, next) {
        order.push(name);
        await next();
      },
    });

    pipeline.use(makeHandler("first"));
    pipeline.use(makeHandler("second"));
    pipeline.use(makeHandler("third"));

    const msg = createTestMessage();
    const ctx = createTestContext();
    await pipeline.execute(msg, ctx);

    expect(order).toEqual(["first", "second", "third"]);
  });

  it("short-circuits when handler does not call next", async () => {
    const order: string[] = [];
    const pipeline = new MessagePipeline();

    pipeline.use({
      name: "blocker",
      async handle(_msg, _ctx, _next) {
        order.push("blocker");
        // Don't call next — short-circuit
      },
    });

    pipeline.use({
      name: "never-reached",
      async handle(_msg, _ctx, next) {
        order.push("never-reached");
        await next();
      },
    });

    const msg = createTestMessage();
    const ctx = createTestContext();
    await pipeline.execute(msg, ctx);

    expect(order).toEqual(["blocker"]);
  });

  it("stops executing after abort", async () => {
    const order: string[] = [];
    const pipeline = new MessagePipeline();

    pipeline.use({
      name: "aborter",
      async handle(_msg, ctx, next) {
        order.push("aborter");
        await ctx.abort("test abort");
        await next(); // next should be a no-op after abort
      },
    });

    pipeline.use({
      name: "after-abort",
      async handle(_msg, _ctx, next) {
        order.push("after-abort");
        await next();
      },
    });

    const msg = createTestMessage();
    const ctx = createTestContext();
    // Wire up abort to set aborted flag
    ctx.abort = vi.fn().mockImplementation(async () => { ctx.aborted = true; });

    await pipeline.execute(msg, ctx);

    expect(order).toEqual(["aborter"]);
    expect(ctx.aborted).toBe(true);
  });
});

describe("RateLimitHandler", () => {
  it("allows messages under the limit", async () => {
    const handler = new RateLimitHandler({ perMinute: 3, perHour: 100 });
    const msg = createTestMessage();
    const ctx = createTestContext();
    const next = vi.fn().mockResolvedValue(undefined);

    await handler.handle(msg, ctx, next);
    expect(next).toHaveBeenCalled();
    expect(ctx.respond).not.toHaveBeenCalled();
  });

  it("blocks after exceeding per-minute limit", async () => {
    const handler = new RateLimitHandler({ perMinute: 2, perHour: 100 });
    const msg = createTestMessage();
    const next = vi.fn().mockResolvedValue(undefined);

    // First two should pass
    await handler.handle(msg, createTestContext(), next);
    await handler.handle(msg, createTestContext(), next);
    expect(next).toHaveBeenCalledTimes(2);

    // Third should be blocked
    const ctx3 = createTestContext();
    const next3 = vi.fn().mockResolvedValue(undefined);
    await handler.handle(msg, ctx3, next3);
    expect(next3).not.toHaveBeenCalled();
    expect(ctx3.respond).toHaveBeenCalledWith(expect.stringContaining("Too many messages"));
  });
});

describe("AuthHandler", () => {
  it("sets userId to owner", async () => {
    const handler = new AuthHandler();
    const msg = createTestMessage();
    const ctx = createTestContext();
    const next = vi.fn().mockResolvedValue(undefined);

    await handler.handle(msg, ctx, next);

    expect(msg.userId).toBe("owner");
    expect(next).toHaveBeenCalled();
  });
});

describe("AllowListHandler", () => {
  it("always allows webchat messages", async () => {
    const handler = new AllowListHandler({
      getAllowList: () => new AllowList([]), // empty list
    });
    const msg = createTestMessage({ channelType: "webchat" });
    const ctx = createTestContext();
    const next = vi.fn().mockResolvedValue(undefined);

    await handler.handle(msg, ctx, next);
    expect(next).toHaveBeenCalled();
  });

  it("rejects unknown telegram senders", async () => {
    const handler = new AllowListHandler({
      getAllowList: () => new AllowList(["allowed-user"]),
    });
    const msg = createTestMessage({ channelType: "telegram", channelId: "unknown-user" });
    const ctx = createTestContext();
    const next = vi.fn().mockResolvedValue(undefined);

    await handler.handle(msg, ctx, next);
    expect(next).not.toHaveBeenCalled();
    // Silent reject — no response sent
    expect(ctx.respond).not.toHaveBeenCalled();
  });

  it("allows listed telegram senders", async () => {
    const handler = new AllowListHandler({
      getAllowList: () => new AllowList(["allowed-user"]),
    });
    const msg = createTestMessage({ channelType: "telegram", channelId: "allowed-user" });
    const ctx = createTestContext();
    const next = vi.fn().mockResolvedValue(undefined);

    await handler.handle(msg, ctx, next);
    expect(next).toHaveBeenCalled();
  });

  it("passes through when no allow list configured", async () => {
    const handler = new AllowListHandler({
      getAllowList: () => null,
    });
    const msg = createTestMessage({ channelType: "telegram", channelId: "anyone" });
    const ctx = createTestContext();
    const next = vi.fn().mockResolvedValue(undefined);

    await handler.handle(msg, ctx, next);
    expect(next).toHaveBeenCalled();
  });
});

describe("AgentResolver", () => {
  it("populates agentId and conversationId", async () => {
    const handler = new AgentResolver({
      getSelectedAgentId: () => "agent-1",
      getConversationId: () => null,
      generateConversationId: () => "conv-new",
      setConversationId: vi.fn(),
    });

    const msg = createTestMessage();
    const ctx = createTestContext();
    const next = vi.fn().mockResolvedValue(undefined);

    await handler.handle(msg, ctx, next);

    expect(msg.agentId).toBe("agent-1");
    expect(msg.conversationId).toBe("conv-new");
    expect(next).toHaveBeenCalled();
  });

  it("uses existing conversation ID from metadata", async () => {
    const handler = new AgentResolver({
      getSelectedAgentId: () => null,
      getConversationId: () => null,
      generateConversationId: () => "should-not-use",
      setConversationId: vi.fn(),
    });

    const msg = createTestMessage({ metadata: { conversationId: "existing-conv" } });
    const ctx = createTestContext();
    const next = vi.fn().mockResolvedValue(undefined);

    await handler.handle(msg, ctx, next);

    expect(msg.conversationId).toBe("existing-conv");
  });
});

describe("Full pipeline integration", () => {
  it("routes a message through all handlers with mock backend", async () => {
    const pipeline = new MessagePipeline();

    pipeline.use(new RateLimitHandler());
    pipeline.use(new AuthHandler());
    pipeline.use(new AllowListHandler({ getAllowList: () => null }));
    pipeline.use(new AgentResolver({
      getSelectedAgentId: () => null,
      getConversationId: () => "conv-123",
      generateConversationId: () => "conv-new",
      setConversationId: vi.fn(),
    }));
    pipeline.use(new ContextLoader());

    // Mock TurnExecutor that produces a fake response
    pipeline.use({
      name: "mock-turn-executor",
      async handle(msg, ctx, next) {
        msg.response = "Hello from agent!";
        await ctx.streamChunk("Hello from agent!");
        await next();
      },
    });

    pipeline.use(new Persister());
    pipeline.use(new ResponseFanOut({
      getWatchers: () => [],
      sendToChannel: vi.fn().mockResolvedValue(undefined),
    }));

    const msg = createTestMessage({ metadata: { conversationId: "conv-123" } });
    const ctx = createTestContext();

    await pipeline.execute(msg, ctx);

    expect(msg.userId).toBe("owner");
    expect(msg.conversationId).toBe("conv-123");
    expect(msg.response).toBe("Hello from agent!");
    expect(ctx.streamChunk).toHaveBeenCalledWith("Hello from agent!");
  });

  it("rate limits block the entire pipeline", async () => {
    const pipeline = new MessagePipeline();
    pipeline.use(new RateLimitHandler({ perMinute: 1, perHour: 100 }));
    pipeline.use(new AuthHandler());

    const msg1 = createTestMessage();
    const ctx1 = createTestContext();
    await pipeline.execute(msg1, ctx1);
    expect(msg1.userId).toBe("owner");

    // Second message should be rate limited
    const msg2 = createTestMessage();
    const ctx2 = createTestContext();
    await pipeline.execute(msg2, ctx2);
    expect(msg2.userId).toBeUndefined(); // Auth never ran
    expect(ctx2.respond).toHaveBeenCalled();
  });
});
