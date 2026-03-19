import { describe, it, expect, vi, beforeEach } from "vitest";
import { CompletionHandler } from "../completion/handler.js";
import type { SystemEventRow } from "../spacetimedb/subscription.js";
import type { GatewayConfig } from "../config/index.js";

// Mock callReducer
vi.mock("../spacetimedb/client.js", () => ({
  callReducer: vi.fn().mockResolvedValue(undefined),
}));

function makeConfig(): GatewayConfig {
  return {
    host: "0.0.0.0",
    port: 18789,
    backendUrl: "http://localhost:18790",
    frontendOrigin: "http://localhost:18788",
    spacetimedbUrl: "http://localhost:18787",
    spacetimedbModuleName: "bond-core-v2",
    spacetimedbToken: "test-token",
  };
}

function makeEvent(overrides: Partial<SystemEventRow> = {}): SystemEventRow {
  return {
    id: "evt-001",
    conversationId: "conv-001",
    agentId: "agent-001",
    eventType: "coding_agent_done",
    summary: "Coding agent (claude) completed in 42.1s",
    metadata: JSON.stringify({
      agent_type: "claude",
      exit_code: 0,
      elapsed_seconds: 42.1,
      git_stat: " 3 files changed, 120 insertions(+), 5 deletions(-)",
      baseline_commit: "abc12345",
      branch: "feature/test",
      working_directory: "/workspace/project",
    }),
    consumed: false,
    createdAt: BigInt(Date.now()),
    ...overrides,
  };
}

function makeMockBackendClient() {
  return {
    conversationTurnStream: vi.fn().mockImplementation(async function* () {
      yield { event: "chunk", data: { content: "The coding agent finished." } };
      yield { event: "done", data: { response: "The coding agent finished.", tool_calls_made: 0 } };
    }),
  } as any;
}

describe("CompletionHandler", () => {
  let handler: CompletionHandler;
  let broadcastFn: ReturnType<typeof vi.fn>;
  let backendClient: ReturnType<typeof makeMockBackendClient>;

  beforeEach(() => {
    broadcastFn = vi.fn();
    backendClient = makeMockBackendClient();
    handler = new CompletionHandler(makeConfig(), backendClient, broadcastFn);
  });

  describe("buildCompletionMessage", () => {
    it("builds a done message with git stat", () => {
      const event = makeEvent();
      const msg = handler.buildCompletionMessage(event);
      expect(msg).toContain("[System: Background coding agent completed successfully]");
      expect(msg).toContain("completed in 42.1s");
      expect(msg).toContain("3 files changed");
      expect(msg).toContain("you may spawn additional coding agents");
    });

    it("builds a failure message", () => {
      const event = makeEvent({
        eventType: "coding_agent_failed",
        summary: "Coding agent (claude) failed in 10.0s",
        metadata: JSON.stringify({ exit_code: 1, error: "Process crashed" }),
      });
      const msg = handler.buildCompletionMessage(event);
      expect(msg).toContain("[System: Background coding agent failed]");
      expect(msg).toContain("Exit code: 1");
      expect(msg).toContain("Process crashed");
    });

    it("handles generic event types", () => {
      const event = makeEvent({ eventType: "custom_event" });
      const msg = handler.buildCompletionMessage(event);
      expect(msg).toContain("[System: custom_event]");
    });
  });

  describe("rate limiting", () => {
    it("allows up to 3 events per minute", async () => {
      for (let i = 0; i < 3; i++) {
        await handler.handleEvent(makeEvent({ id: `evt-${i}` }));
      }
      // All 3 should have triggered turns
      expect(backendClient.conversationTurnStream).toHaveBeenCalledTimes(3);
    });

    it("rejects the 4th event in the same minute", async () => {
      for (let i = 0; i < 4; i++) {
        await handler.handleEvent(makeEvent({ id: `evt-${i}` }));
      }
      // Only 3 should have triggered turns
      expect(backendClient.conversationTurnStream).toHaveBeenCalledTimes(3);
    });

    it("allows events from different conversations independently", async () => {
      for (let i = 0; i < 3; i++) {
        await handler.handleEvent(makeEvent({ id: `evt-a-${i}`, conversationId: "conv-a" }));
      }
      for (let i = 0; i < 3; i++) {
        await handler.handleEvent(makeEvent({ id: `evt-b-${i}`, conversationId: "conv-b" }));
      }
      expect(backendClient.conversationTurnStream).toHaveBeenCalledTimes(6);
    });
  });

  describe("event deduplication", () => {
    it("does not process the same event ID twice concurrently", async () => {
      const event = makeEvent();
      // Fire two concurrent handles of the same event
      await Promise.all([
        handler.handleEvent(event),
        handler.handleEvent(event),
      ]);
      // Should only trigger one turn
      expect(backendClient.conversationTurnStream).toHaveBeenCalledTimes(1);
    });
  });

  describe("broadcasting", () => {
    it("broadcasts status(thinking), chunk, and done to the conversation", async () => {
      await handler.handleEvent(makeEvent());

      const calls = broadcastFn.mock.calls;
      // Should have: status(thinking), chunk, done
      expect(calls.some(([, msg]: any) => msg.type === "status" && msg.agentStatus === "thinking")).toBe(true);
      expect(calls.some(([, msg]: any) => msg.type === "chunk" && msg.content === "The coding agent finished.")).toBe(true);
      expect(calls.some(([, msg]: any) => msg.type === "done" && msg.agentStatus === "idle")).toBe(true);
    });

    it("done event includes response as fallback", async () => {
      await handler.handleEvent(makeEvent());

      const calls = broadcastFn.mock.calls;
      const doneCall = calls.find(([, msg]: any) => msg.type === "done");
      expect(doneCall).toBeDefined();
      expect(doneCall![1].response).toBe("The coding agent finished.");
    });
  });

  describe("error handling", () => {
    it("still consumes the event and sends done on backend error", async () => {
      backendClient.conversationTurnStream.mockImplementation(async function* () {
        throw new Error("Backend down");
      });

      await handler.handleEvent(makeEvent());

      const calls = broadcastFn.mock.calls;

      // Should have broadcast a chunk with the error info
      expect(calls.some(([, msg]: any) =>
        msg.type === "chunk" && msg.content.includes("Completion turn failed"),
      )).toBe(true);

      // Should still send a done event to clear the thinking state
      expect(calls.some(([, msg]: any) =>
        msg.type === "done" && msg.agentStatus === "idle",
      )).toBe(true);

      // Event should still be consumed (callReducer called with consume_system_event)
      const { callReducer } = await import("../spacetimedb/client.js");
      expect(callReducer).toHaveBeenCalledWith(
        expect.any(String),
        expect.any(String),
        "consume_system_event",
        ["evt-001"],
        expect.any(String),
      );
    });
  });
});
