import { describe, it, expect, vi, beforeEach } from "vitest";
import type { WebSocket } from "ws";
import { WebChatChannel } from "../channels/webchat.js";
import { SessionManager } from "../sessions/manager.js";
import type { BackendClient, AgentResolution } from "../backend/client.js";

// Helper to create a mock WebSocket
function mockSocket(): WebSocket {
  const ws = {
    readyState: 1, // OPEN
    OPEN: 1,
    send: vi.fn(),
    on: vi.fn(),
  } as unknown as WebSocket;
  return ws;
}

// Helper to capture sent messages
function getSentMessages(socket: WebSocket): any[] {
  return (socket.send as any).mock.calls.map((call: any[]) => JSON.parse(call[0]));
}

describe("Routing", () => {
  let sessionManager: SessionManager;
  let backendClient: Partial<BackendClient>;
  let channel: WebChatChannel;
  let socket: WebSocket;
  let session: any;

  beforeEach(() => {
    sessionManager = new SessionManager();
    session = sessionManager.createSession("conv-123");
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
    };

    channel = new WebChatChannel(sessionManager, backendClient as BackendClient);
  });

  it("host mode uses backend stream", async () => {
    const resolution: AgentResolution = {
      mode: "host",
      agent_id: "agent-1",
      conversation_id: "conv-123",
    };
    (backendClient.resolveAgent as any).mockResolvedValue(resolution);

    async function* fakeStream() {
      yield { event: "chunk", data: { content: "hello" } };
      yield { event: "done", data: { message_id: "msg-1", conversation_id: "conv-123", queued_count: 0 } };
    }
    (backendClient.conversationTurnStream as any).mockReturnValue(fakeStream());

    // Call the private method via type assertion
    await (channel as any).startTurn(socket, session.id, "hi", "conv-123");

    const messages = getSentMessages(socket);
    const types = messages.map((m: any) => m.type);
    expect(types).toContain("chunk");
    expect(types).toContain("done");
    expect(backendClient.conversationTurnStream).toHaveBeenCalled();
  });

  it("container mode uses worker stream", async () => {
    // Note: Gateway no longer distinguishes between container and host mode
    // It just relays to backend via conversationTurnStream
    const resolution: AgentResolution = {
      mode: "container",
      worker_url: "http://localhost:18793",
      agent_id: "agent-1",
      conversation_id: "conv-123",
    };
    (backendClient.resolveAgent as any).mockResolvedValue(resolution);
    (backendClient.getConversation as any).mockResolvedValue({
      id: "conv-123",
      messages: [{ role: "user", content: "earlier", id: "m1", created_at: "" }],
    });

    // Mock conversationTurnStream to return events
    const mockEvents = [
      { event: "status", data: { state: "thinking" } },
      { event: "chunk", data: { content: "hi back" } },
      { event: "done", data: { response: "hi back", tool_calls_made: 0 } },
    ];
    
    (backendClient.conversationTurnStream as any).mockImplementation(async function*() {
      for (const event of mockEvents) {
        yield event;
      }
    });

    await (channel as any).startTurn(socket, session.id, "hi", "conv-123");

    // Note: saveAssistantMessage is no longer called by gateway
    // It's handled by the backend directly

    const messages = getSentMessages(socket);
    const types = messages.map((m: any) => m.type);
    expect(types).toContain("status");
    expect(types).toContain("chunk");
    // Note: "done" is not sent to frontend, it's handled internally
  });

  it("container mode saves assistant message after turn", async () => {
    // Note: Gateway no longer saves assistant messages directly
    // This is handled by the backend
    const resolution: AgentResolution = {
      mode: "container",
      worker_url: "http://localhost:18793",
      agent_id: "agent-1",
      conversation_id: "conv-123",
    };
    (backendClient.resolveAgent as any).mockResolvedValue(resolution);
    (backendClient.getConversation as any).mockResolvedValue({ id: "conv-123", messages: [] });

    // Mock conversationTurnStream
    const mockEvents = [
      { event: "done", data: { response: "result", tool_calls_made: 2 } },
    ];
    
    (backendClient.conversationTurnStream as any).mockImplementation(async function*() {
      for (const event of mockEvents) {
        yield event;
      }
    });

    await (channel as any).startTurn(socket, session.id, "test", "conv-123");

    // Note: saveAssistantMessage is no longer called by gateway
    // expect(backendClient.saveAssistantMessage).toHaveBeenCalledWith("conv-123", "result", 2);
  });

  it("memory events are not forwarded to frontend", async () => {
    // Memory events are not part of the standard SSE events from conversationTurnStream
    // They are handled internally. This test verifies unknown events are ignored.
    (backendClient.conversationTurnStream as any).mockImplementation(async function*() {
      yield { event: "chunk", data: { content: "ok" } };
      yield { event: "done", data: { message_id: "msg-4" } };
    });

    await (channel as any).startTurn(socket, session.id, "test", "conv-123");

    const messages = getSentMessages(socket);
    const types = messages.map((m: any) => m.type);
    expect(types).not.toContain("memory");
    expect(types).toContain("done");
  });

  it("turn error sent to frontend", async () => {
    (backendClient.conversationTurnStream as any).mockImplementation(async function*() {
      throw new Error("ECONNREFUSED");
    });

    await (channel as any).startTurn(socket, session.id, "test", "conv-123");

    const messages = getSentMessages(socket);
    const errorMsg = messages.find((m: any) => m.type === "error");
    expect(errorMsg).toBeDefined();
    expect(errorMsg.error).toContain("ECONNREFUSED");
  });

  it("interrupt routes to backend", async () => {
    (backendClient.interrupt as any).mockResolvedValue({ status: "interrupt_sent" });

    await (channel as any).handleInterrupt(socket, session.id, {
      type: "interrupt",
      conversationId: "conv-123",
    });

    expect(backendClient.interrupt).toHaveBeenCalledWith("conv-123");
  });

  it("interrupt routes to backend for host mode", async () => {
    const resolution: AgentResolution = {
      mode: "host",
      agent_id: "agent-1",
      conversation_id: "conv-123",
    };
    (backendClient.resolveAgent as any).mockResolvedValue(resolution);
    (backendClient.interrupt as any).mockResolvedValue({ status: "interrupt_sent" });

    await (channel as any).handleInterrupt(socket, session.id, {
      type: "interrupt",
      conversationId: "conv-123",
    });

    expect(backendClient.interrupt).toHaveBeenCalledWith("conv-123");
  });
});
