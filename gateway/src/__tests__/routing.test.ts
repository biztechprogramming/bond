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
      agentTurnStream: vi.fn(),
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
    (backendClient.agentTurnStream as any).mockReturnValue(fakeStream());

    // Call the private method via type assertion
    await (channel as any).startStreamingTurn(socket, session.id, "hi", "conv-123");

    const messages = getSentMessages(socket);
    const types = messages.map((m: any) => m.type);
    expect(types).toContain("chunk");
    expect(types).toContain("done");
    expect(backendClient.agentTurnStream).toHaveBeenCalled();
  });

  it("container mode uses worker stream", async () => {
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
    (backendClient.saveAssistantMessage as any).mockResolvedValue({ message_id: "msg-2" });

    // Mock fetch for worker client
    const sseBody =
      'event: status\ndata: {"state":"thinking"}\n\n' +
      'event: chunk\ndata: {"content":"hi back"}\n\n' +
      'event: done\ndata: {"response":"hi back","tool_calls_made":0}\n\n';

    const encoder = new TextEncoder();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        new ReadableStream({
          start(c) { c.enqueue(encoder.encode(sseBody)); c.close(); },
        }),
        { status: 200 },
      ),
    );

    await (channel as any).startStreamingTurn(socket, session.id, "hi", "conv-123");

    expect(backendClient.saveAssistantMessage).toHaveBeenCalledWith(
      "conv-123", "hi back", 0,
    );

    const messages = getSentMessages(socket);
    const types = messages.map((m: any) => m.type);
    expect(types).toContain("status");
    expect(types).toContain("chunk");
    expect(types).toContain("done");
  });

  it("container mode saves assistant message after turn", async () => {
    const resolution: AgentResolution = {
      mode: "container",
      worker_url: "http://localhost:18793",
      agent_id: "agent-1",
      conversation_id: "conv-123",
    };
    (backendClient.resolveAgent as any).mockResolvedValue(resolution);
    (backendClient.getConversation as any).mockResolvedValue({ id: "conv-123", messages: [] });
    (backendClient.saveAssistantMessage as any).mockResolvedValue({ message_id: "msg-3" });

    const sseBody = 'event: done\ndata: {"response":"result","tool_calls_made":2}\n\n';
    const encoder = new TextEncoder();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        new ReadableStream({ start(c) { c.enqueue(encoder.encode(sseBody)); c.close(); } }),
        { status: 200 },
      ),
    );

    await (channel as any).startStreamingTurn(socket, session.id, "test", "conv-123");

    expect(backendClient.saveAssistantMessage).toHaveBeenCalledWith("conv-123", "result", 2);
  });

  it("container mode promotes memory events", async () => {
    const resolution: AgentResolution = {
      mode: "container",
      worker_url: "http://localhost:18793",
      agent_id: "agent-1",
      conversation_id: "conv-123",
    };
    (backendClient.resolveAgent as any).mockResolvedValue(resolution);
    (backendClient.getConversation as any).mockResolvedValue({ id: "conv-123", messages: [] });
    (backendClient.saveAssistantMessage as any).mockResolvedValue({ message_id: "msg-4" });
    (backendClient.promoteMemory as any).mockResolvedValue(undefined);

    const sseBody =
      'event: memory\ndata: {"type":"fact","content":"user likes blue","memory_id":"mem-1","entities":[]}\n\n' +
      'event: done\ndata: {"response":"ok","tool_calls_made":0}\n\n';
    const encoder = new TextEncoder();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        new ReadableStream({ start(c) { c.enqueue(encoder.encode(sseBody)); c.close(); } }),
        { status: 200 },
      ),
    );

    await (channel as any).startStreamingTurn(socket, session.id, "test", "conv-123");

    // Wait for async promotion
    await new Promise(r => setTimeout(r, 10));

    expect(backendClient.promoteMemory).toHaveBeenCalledWith(
      expect.objectContaining({
        agent_id: "agent-1",
        memory_id: "mem-1",
        type: "fact",
        content: "user likes blue",
      }),
    );

    // Memory events should NOT be forwarded to frontend
    const messages = getSentMessages(socket);
    const types = messages.map((m: any) => m.type);
    expect(types).not.toContain("memory");
  });

  it("container mode memory promotion failure is non-fatal", async () => {
    const resolution: AgentResolution = {
      mode: "container",
      worker_url: "http://localhost:18793",
      agent_id: "agent-1",
      conversation_id: "conv-123",
    };
    (backendClient.resolveAgent as any).mockResolvedValue(resolution);
    (backendClient.getConversation as any).mockResolvedValue({ id: "conv-123", messages: [] });
    (backendClient.saveAssistantMessage as any).mockResolvedValue({ message_id: "msg-5" });
    (backendClient.promoteMemory as any).mockRejectedValue(new Error("500 Internal Server Error"));

    const sseBody =
      'event: memory\ndata: {"type":"fact","content":"test","memory_id":"mem-2","entities":[]}\n\n' +
      'event: done\ndata: {"response":"ok","tool_calls_made":0}\n\n';
    const encoder = new TextEncoder();
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(
        new ReadableStream({ start(c) { c.enqueue(encoder.encode(sseBody)); c.close(); } }),
        { status: 200 },
      ),
    );

    // Should not throw even though promoteMemory fails
    await (channel as any).startStreamingTurn(socket, session.id, "test", "conv-123");

    const messages = getSentMessages(socket);
    const doneMsg = messages.find((m: any) => m.type === "done");
    expect(doneMsg).toBeDefined();
  });

  it("container mode worker error sent to frontend", async () => {
    const resolution: AgentResolution = {
      mode: "container",
      worker_url: "http://localhost:18793",
      agent_id: "agent-1",
      conversation_id: "conv-123",
    };
    (backendClient.resolveAgent as any).mockResolvedValue(resolution);
    (backendClient.getConversation as any).mockResolvedValue({ id: "conv-123", messages: [] });

    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("ECONNREFUSED"));

    await (channel as any).startStreamingTurn(socket, session.id, "test", "conv-123");

    const messages = getSentMessages(socket);
    const errorMsg = messages.find((m: any) => m.type === "error");
    expect(errorMsg).toBeDefined();
    expect(errorMsg.error).toContain("ECONNREFUSED");
  });

  it("interrupt routes to container worker", async () => {
    const resolution: AgentResolution = {
      mode: "container",
      worker_url: "http://localhost:18793",
      agent_id: "agent-1",
      conversation_id: "conv-123",
    };
    (backendClient.resolveAgent as any).mockResolvedValue(resolution);

    // Mock fetch for worker interrupt
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ acknowledged: true }), { status: 200 }),
    );

    await (channel as any).handleInterrupt(socket, session.id, {
      type: "interrupt",
      conversationId: "conv-123",
    });

    expect(globalThis.fetch).toHaveBeenCalledWith(
      "http://localhost:18793/interrupt",
      expect.objectContaining({ method: "POST" }),
    );
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
