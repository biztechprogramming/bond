import { describe, it, expect, vi, beforeEach } from "vitest";
import type { WebSocket } from "ws";
import { WebChatChannel } from "../channels/webchat.js";
import { SessionManager } from "../sessions/manager.js";
import type { BackendClient } from "../backend/client.js";

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

/**
 * Simulate receiving a message on a mock socket.
 * Finds the "message" event handler registered via socket.on("message", ...)
 * and calls it with the given data.
 */
function simulateMessage(socket: WebSocket, data: Record<string, unknown>): void {
  const onCalls = (socket.on as any).mock.calls;
  const messageHandler = onCalls.find((c: any[]) => c[0] === "message")?.[1];
  if (!messageHandler) throw new Error("No message handler registered on socket");
  messageHandler(Buffer.from(JSON.stringify(data)));
}

describe("WebChat ping/pong heartbeat", () => {
  let sessionManager: SessionManager;
  let backendClient: Partial<BackendClient>;
  let channel: WebChatChannel;

  beforeEach(() => {
    sessionManager = new SessionManager();
    backendClient = {
      resolveAgent: vi.fn(),
      conversationTurnStream: vi.fn(),
      getConversation: vi.fn(),
      interrupt: vi.fn(),
      saveAssistantMessage: vi.fn(),
      promoteMemory: vi.fn(),
      listConversations: vi.fn().mockResolvedValue([]),
    };
    channel = new WebChatChannel(sessionManager, backendClient as BackendClient);
  });

  it("responds with pong when client sends ping", async () => {
    const socket = mockSocket();
    await channel.handleConnection(socket);

    // Simulate a ping message from the client
    simulateMessage(socket, { type: "ping" });

    // Allow any async processing
    await new Promise((r) => setTimeout(r, 10));

    const messages = getSentMessages(socket);
    // First message is "connected", second should be "pong"
    const pong = messages.find((m: any) => m.type === "pong");
    expect(pong).toBeDefined();
    expect(pong.type).toBe("pong");
  });

  it("does not crash on unknown message types", async () => {
    const socket = mockSocket();
    await channel.handleConnection(socket);

    // Should not throw
    simulateMessage(socket, { type: "unknown_type_xyz" });
    await new Promise((r) => setTimeout(r, 10));

    // Socket should still have the connected message
    const messages = getSentMessages(socket);
    expect(messages[0].type).toBe("connected");
  });
});
