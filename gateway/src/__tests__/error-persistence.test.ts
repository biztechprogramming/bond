import { describe, it, expect, vi, beforeEach } from "vitest";
import type { WebSocket } from "ws";
import { WebChatChannel } from "../channels/webchat.js";
import { SessionManager } from "../sessions/manager.js";
import type { BackendClient } from "../backend/client.js";
import type { GatewayConfig } from "../config/index.js";

// Mock the SpacetimeDB client module
vi.mock("../spacetimedb/client.js", () => ({
  callReducer: vi.fn().mockResolvedValue(undefined),
  sqlQuery: vi.fn(),
  encodeOption: vi.fn(),
}));

import { callReducer } from "../spacetimedb/client.js";

function mockSocket(): WebSocket {
  return {
    readyState: 1,
    OPEN: 1,
    send: vi.fn(),
    on: vi.fn(),
  } as unknown as WebSocket;
}

function getSentMessages(socket: WebSocket): any[] {
  return (socket.send as any).mock.calls.map((call: any[]) => JSON.parse(call[0]));
}

const fakeConfig: GatewayConfig = {
  port: 18792,
  host: "0.0.0.0",
  backendUrl: "http://localhost:18790",
  spacetimedbUrl: "http://localhost:18787",
  spacetimedbModuleName: "bond-core-v2",
  spacetimedbToken: "fake-token-123",
};

describe("Error Persistence", () => {
  let sessionManager: SessionManager;
  let backendClient: Partial<BackendClient>;
  let channel: WebChatChannel;
  let socket: WebSocket;
  let session: any;

  beforeEach(() => {
    vi.clearAllMocks();
    sessionManager = new SessionManager();
    session = sessionManager.createSession("conv-err-1");
    socket = mockSocket();
    sessionManager.registerClient(socket, session.id);

    backendClient = {
      resolveAgent: vi.fn(),
      conversationTurnStream: vi.fn(),
      getConversation: vi.fn(),
      interrupt: vi.fn(),
      saveAssistantMessage: vi.fn(),
      promoteMemory: vi.fn(),
      listConversations: vi.fn().mockResolvedValue([]),
      queueMessage: vi.fn(),
    };

    channel = new WebChatChannel(sessionManager, backendClient as BackendClient);
    channel.setConfig(fakeConfig);
  });

  it("persists turn errors to SpacetimeDB", async () => {
    // Make the turn stream throw an error
    (backendClient.conversationTurnStream as any).mockImplementation(async function* () {
      throw new Error("LLM provider unavailable");
    });

    // Trigger a message through the legacy path (no pipeline set)
    await (channel as any).handleChatMessage(socket, session.id, {
      type: "message",
      content: "hello",
      conversationId: "conv-err-1",
    });

    // Wait for async persistError to complete
    await new Promise((r) => setTimeout(r, 50));

    // Should have called callReducer with add_conversation_message
    expect(callReducer).toHaveBeenCalledWith(
      "http://localhost:18787",
      "bond-core-v2",
      "add_conversation_message",
      expect.arrayContaining([
        expect.any(String),  // id (ulid)
        "conv-err-1",        // conversationId
        "system",            // role
        "Error: LLM provider unavailable",
        "",                  // tool_calls
        "",                  // tool_call_id
        0,                   // token_count
        "delivered",
      ]),
      "fake-token-123",
    );

    // Should also send the error over WebSocket
    const messages = getSentMessages(socket);
    const errorMsg = messages.find((m: any) => m.type === "error");
    expect(errorMsg).toBeDefined();
    expect(errorMsg.error).toBe("LLM provider unavailable");
  });

  it("sends error to WebSocket even if persistence fails", async () => {
    // Make persistence fail
    (callReducer as any).mockRejectedValueOnce(new Error("SpacetimeDB down"));

    (backendClient.conversationTurnStream as any).mockImplementation(async function* () {
      throw new Error("Backend exploded");
    });

    await (channel as any).handleChatMessage(socket, session.id, {
      type: "message",
      content: "test",
      conversationId: "conv-err-2",
    });

    await new Promise((r) => setTimeout(r, 50));

    // Error should still reach the client even though persistence failed
    const messages = getSentMessages(socket);
    const errorMsg = messages.find((m: any) => m.type === "error");
    expect(errorMsg).toBeDefined();
    expect(errorMsg.error).toBe("Backend exploded");
  });

  it("does not persist transient errors (interrupt failure)", async () => {
    (backendClient.interrupt as any).mockRejectedValue(new Error("interrupt failed"));

    await (channel as any).handleInterrupt(socket, session.id, {
      type: "interrupt",
      conversationId: "conv-err-1",
    });

    await new Promise((r) => setTimeout(r, 50));

    // Interrupt errors are transient — should NOT persist
    expect(callReducer).not.toHaveBeenCalled();
  });
});
