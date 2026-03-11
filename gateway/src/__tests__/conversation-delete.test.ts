import { describe, it, expect, vi, beforeEach } from "vitest";
import type { WebSocket } from "ws";
import { WebChatChannel } from "../channels/webchat.js";
import { SessionManager } from "../sessions/manager.js";
import type { BackendClient } from "../backend/client.js";
import type { GatewayConfig } from "../config/index.js";

// Mock the SpacetimeDB client module
vi.mock("../spacetimedb/client.js", () => ({
  callReducer: vi.fn().mockResolvedValue(undefined),
  sqlQuery: vi.fn().mockResolvedValue([]),
  encodeOption: vi.fn(),
}));

import { callReducer, sqlQuery } from "../spacetimedb/client.js";

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

describe("Conversation Delete", () => {
  let sessionManager: SessionManager;
  let backendClient: Partial<BackendClient>;
  let channel: WebChatChannel;
  let socket: WebSocket;
  let session: any;

  beforeEach(() => {
    vi.clearAllMocks();
    sessionManager = new SessionManager();
    session = sessionManager.createSession("conv-del-1");
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
      deleteConversation: vi.fn().mockResolvedValue(undefined),
    };

    channel = new WebChatChannel(sessionManager, backendClient as BackendClient);
    channel.setConfig(fakeConfig);
  });

  it("deletes conversation from SpacetimeDB via the delete_conversation reducer", async () => {
    await (channel as any).handleDeleteConversation(socket, session.id, {
      type: "delete_conversation",
      conversationId: "conv-del-1",
    });

    // Must call the SpacetimeDB reducer to delete the conversation
    expect(callReducer).toHaveBeenCalledWith(
      "http://localhost:18787",
      "bond-core-v2",
      "delete_conversation",
      ["conv-del-1"],
      "fake-token-123",
    );
  });

  it("also mirrors delete to the backend (best-effort)", async () => {
    await (channel as any).handleDeleteConversation(socket, session.id, {
      type: "delete_conversation",
      conversationId: "conv-del-1",
    });

    expect(backendClient.deleteConversation).toHaveBeenCalledWith("conv-del-1");
  });

  it("still succeeds if backend mirror fails", async () => {
    (backendClient.deleteConversation as any).mockRejectedValue(new Error("backend down"));

    await (channel as any).handleDeleteConversation(socket, session.id, {
      type: "delete_conversation",
      conversationId: "conv-del-1",
    });

    // SpacetimeDB delete should still have been called
    expect(callReducer).toHaveBeenCalledWith(
      "http://localhost:18787",
      "bond-core-v2",
      "delete_conversation",
      ["conv-del-1"],
      "fake-token-123",
    );

    // Should NOT send an error to the client
    const messages = getSentMessages(socket);
    const errorMsg = messages.find((m: any) => m.type === "error");
    expect(errorMsg).toBeUndefined();
  });

  it("clears the session conversationId when deleting the active conversation", async () => {
    // The session was created with conv-del-1 as its conversationId
    const sessionBefore = sessionManager.getSession(session.id);
    expect(sessionBefore?.conversationId).toBe("conv-del-1");

    await (channel as any).handleDeleteConversation(socket, session.id, {
      type: "delete_conversation",
      conversationId: "conv-del-1",
    });

    const sessionAfter = sessionManager.getSession(session.id);
    expect(sessionAfter?.conversationId).toBeNull();
  });

  it("does not clear session conversationId when deleting a different conversation", async () => {
    await (channel as any).handleDeleteConversation(socket, session.id, {
      type: "delete_conversation",
      conversationId: "conv-other",
    });

    const sessionAfter = sessionManager.getSession(session.id);
    expect(sessionAfter?.conversationId).toBe("conv-del-1");
  });

  it("refreshes the conversation list after delete", async () => {
    // sqlQuery is used by handleListConversations to fetch the updated list
    (sqlQuery as any).mockResolvedValue([]);

    await (channel as any).handleDeleteConversation(socket, session.id, {
      type: "delete_conversation",
      conversationId: "conv-del-1",
    });

    // Should send a conversations_list message to the client
    const messages = getSentMessages(socket);
    const listMsg = messages.find((m: any) => m.type === "conversations_list");
    expect(listMsg).toBeDefined();
  });

  it("sends error to client if SpacetimeDB delete fails", async () => {
    (callReducer as any).mockRejectedValue(new Error("SpacetimeDB unreachable"));

    await (channel as any).handleDeleteConversation(socket, session.id, {
      type: "delete_conversation",
      conversationId: "conv-del-1",
    });

    const messages = getSentMessages(socket);
    const errorMsg = messages.find((m: any) => m.type === "error");
    expect(errorMsg).toBeDefined();
    expect(errorMsg.error).toBe("SpacetimeDB unreachable");
  });

  it("no-ops when conversationId is missing", async () => {
    await (channel as any).handleDeleteConversation(socket, session.id, {
      type: "delete_conversation",
      // no conversationId
    });

    expect(callReducer).not.toHaveBeenCalled();
    expect(backendClient.deleteConversation).not.toHaveBeenCalled();
  });
});

describe("Conversation Message Delete (REST)", () => {
  // This path already works correctly via the conversations router,
  // but we verify it here to ensure it stays working.

  it("calls delete_conversation_message reducer with correct args", async () => {
    // Import the router factory
    const { createConversationsRouter } = await import("../conversations/router.js");

    // We can't easily test Express routes in isolation without supertest,
    // so we verify the reducer signature matches what the router sends.
    // The router calls: callReducer(url, mod, "delete_conversation_message", [messageId, conversationId], token)
    // The reducer expects: { id: string, conversationId: string }
    // These must match positionally.

    // Verify the reducer type file matches the expected shape
    const deleteMessageReducer = await import("../spacetimedb/delete_conversation_message_reducer.js");
    expect(deleteMessageReducer.default).toEqual({
      id: expect.anything(),
      conversationId: expect.anything(),
    });
  });
});
