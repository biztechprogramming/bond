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

describe("Broadcast streaming to all conversation clients", () => {
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

  it("sends streaming chunks to all sockets watching the same conversation", async () => {
    // Socket A connects (handleConnection creates its own session)
    const socketA = mockSocket();
    await channel.handleConnection(socketA);

    // Socket B connects (board view — different tab/page)
    const socketB = mockSocket();
    await channel.handleConnection(socketB);

    // Socket B switches to conv-1 (simulating board view loading a plan's conversation)
    (backendClient.getConversation as any).mockResolvedValue({
      id: "conv-1",
      messages: [],
    });
    const onMessageB = (socketB.on as any).mock.calls.find(
      (c: any[]) => c[0] === "message"
    )[1];
    await onMessageB(
      JSON.stringify({ type: "switch_conversation", conversationId: "conv-1" })
    );

    // Now set up the stream for socket A's message
    async function* fakeStream() {
      yield { event: "chunk", data: { content: "hello " } };
      yield { event: "chunk", data: { content: "world" } };
      yield { event: "done", data: { message_id: "msg-1", conversation_id: "conv-1", queued_count: 0 } };
    }
    (backendClient.conversationTurnStream as any).mockReturnValue(fakeStream());

    // Socket A sends a message to conv-1
    const onMessageA = (socketA.on as any).mock.calls.find(
      (c: any[]) => c[0] === "message"
    )[1];
    await onMessageA(
      JSON.stringify({
        type: "message",
        content: "hi",
        conversationId: "conv-1",
      })
    );

    await new Promise((r) => setTimeout(r, 50));

    const msgsA = getSentMessages(socketA);
    const msgsB = getSentMessages(socketB);

    // Both sockets should have received the chunks
    const chunksA = msgsA.filter((m) => m.type === "chunk");
    const chunksB = msgsB.filter((m) => m.type === "chunk");

    expect(chunksA.length).toBe(2);
    expect(chunksB.length).toBe(2);
    expect(chunksA[0].content).toBe("hello ");
    expect(chunksB[0].content).toBe("hello ");
    expect(chunksA[1].content).toBe("world");
    expect(chunksB[1].content).toBe("world");

    // Both should get the done event
    const doneA = msgsA.filter((m) => m.type === "done");
    const doneB = msgsB.filter((m) => m.type === "done");
    expect(doneA.length).toBe(1);
    expect(doneB.length).toBe(1);
  });

  it("sends buffered content to late-joining client on switch_conversation", async () => {
    // Socket A starts a turn
    const sessionA = sessionManager.createSession("conv-1");
    const socketA = mockSocket();
    sessionManager.registerClient(socketA, sessionA.id);

    let resolveStream: () => void;
    const streamDone = new Promise<void>((r) => (resolveStream = r));

    async function* fakeStream() {
      yield { event: "chunk", data: { content: "partial " } };
      yield { event: "chunk", data: { content: "response" } };
      // Stream hangs — simulates agent still working
      await streamDone;
      yield { event: "done", data: { message_id: "msg-1" } };
    }
    (backendClient.conversationTurnStream as any).mockReturnValue(fakeStream());

    await channel.handleConnection(socketA);
    const onMessageA = (socketA.on as any).mock.calls.find(
      (c: any[]) => c[0] === "message"
    )[1];

    // Start the turn (don't await — it'll hang until resolveStream)
    const turnPromise = onMessageA(
      JSON.stringify({
        type: "message",
        content: "work on this",
        conversationId: "conv-1",
      })
    );

    // Wait for chunks to be sent
    await new Promise((r) => setTimeout(r, 50));

    // Now Socket B joins and switches to the same conversation
    const sessionB = sessionManager.createSession();
    const socketB = mockSocket();
    sessionManager.registerClient(socketB, sessionB.id);

    // Mock getConversation for the switch
    (backendClient.getConversation as any).mockResolvedValue({
      id: "conv-1",
      messages: [{ role: "user", content: "work on this", id: "u1" }],
    });

    await channel.handleConnection(socketB);
    const onMessageB = (socketB.on as any).mock.calls.find(
      (c: any[]) => c[0] === "message"
    )[1];

    await onMessageB(
      JSON.stringify({
        type: "switch_conversation",
        conversationId: "conv-1",
      })
    );

    await new Promise((r) => setTimeout(r, 50));

    const msgsB = getSentMessages(socketB);

    // Should have received history
    const historyMsg = msgsB.find((m) => m.type === "history");
    expect(historyMsg).toBeDefined();

    // Should have received current status
    const statusMsg = msgsB.find((m) => m.type === "status");
    expect(statusMsg).toBeDefined();

    // Should have received buffered content as a single chunk
    const chunkMsgs = msgsB.filter((m) => m.type === "chunk");
    expect(chunkMsgs.length).toBe(1);
    expect(chunkMsgs[0].content).toBe("partial response");

    // Finish the stream
    resolveStream!();
    await turnPromise;
  });

  it("does not send buffer when no active stream on conversation", async () => {
    const sessionA = sessionManager.createSession("conv-1");
    const socketA = mockSocket();
    sessionManager.registerClient(socketA, sessionA.id);

    (backendClient.getConversation as any).mockResolvedValue({
      id: "conv-1",
      messages: [],
    });

    await channel.handleConnection(socketA);
    const onMessage = (socketA.on as any).mock.calls.find(
      (c: any[]) => c[0] === "message"
    )[1];

    await onMessage(
      JSON.stringify({
        type: "switch_conversation",
        conversationId: "conv-1",
      })
    );

    const msgs = getSentMessages(socketA);
    // Should have connected + history, but no status or chunk from buffer
    const statusMsgs = msgs.filter((m) => m.type === "status");
    const chunkMsgs = msgs.filter((m) => m.type === "chunk");
    expect(statusMsgs.length).toBe(0);
    expect(chunkMsgs.length).toBe(0);
  });

  it("socket not watching conversation does not receive chunks", async () => {
    const sessionA = sessionManager.createSession("conv-1");
    const socketA = mockSocket();
    sessionManager.registerClient(socketA, sessionA.id);

    // Socket C is watching a different conversation
    const sessionC = sessionManager.createSession("conv-other");
    const socketC = mockSocket();
    sessionManager.registerClient(socketC, sessionC.id);

    async function* fakeStream() {
      yield { event: "chunk", data: { content: "private" } };
      yield { event: "done", data: { message_id: "msg-1" } };
    }
    (backendClient.conversationTurnStream as any).mockReturnValue(fakeStream());

    await channel.handleConnection(socketA);
    const onMessage = (socketA.on as any).mock.calls.find(
      (c: any[]) => c[0] === "message"
    )[1];

    await onMessage(
      JSON.stringify({
        type: "message",
        content: "secret",
        conversationId: "conv-1",
      })
    );

    await new Promise((r) => setTimeout(r, 50));

    const msgsC = getSentMessages(socketC);
    // Socket C should not have received any chunks or done events
    const chunks = msgsC.filter((m) => m.type === "chunk");
    const dones = msgsC.filter((m) => m.type === "done");
    expect(chunks.length).toBe(0);
    expect(dones.length).toBe(0);
  });
});
