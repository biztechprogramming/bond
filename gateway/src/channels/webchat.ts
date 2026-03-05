/**
 * WebChat channel — handles messages from the web frontend via WebSocket.
 *
 * History is now managed server-side. The gateway forwards conversation_id
 * between frontend and backend.
 */

import type { WebSocket } from "ws";
import type { IncomingMessage, OutgoingMessage } from "../protocol/types.js";
import type { SessionManager } from "../sessions/manager.js";
import type { BackendClient } from "../backend/client.js";
import type { WorkerSSEEvent } from "../backend/worker-client.js";

export class WebChatChannel {
  constructor(
    private sessionManager: SessionManager,
    private backendClient: BackendClient
  ) {}

  async handleConnection(socket: WebSocket): Promise<void> {
    const session = this.sessionManager.createSession();
    this.sessionManager.registerClient(socket, session.id);

    // Send connected event with session info
    this.send(socket, {
      type: "connected",
      sessionId: session.id,
    });

    socket.on("message", async (data) => {
      try {
        const msg: IncomingMessage = JSON.parse(data.toString());
        await this.handleMessage(socket, msg);
      } catch (err) {
        this.send(socket, {
          type: "error",
          error: err instanceof Error ? err.message : "Unknown error",
        });
      }
    });

    socket.on("close", () => {
      this.sessionManager.removeClient(socket);
    });
  }

  private async handleMessage(
    socket: WebSocket,
    msg: IncomingMessage
  ): Promise<void> {
    const client = this.sessionManager.getClient(socket);
    if (!client) return;

    const session = this.sessionManager.getSession(client.sessionId);
    if (!session) return;

    switch (msg.type) {
      case "message":
        await this.handleChatMessage(socket, session.id, msg);
        break;
      case "interrupt":
        await this.handleInterrupt(socket, session.id, msg);
        break;
      case "pause":
        await this.handlePause(socket, session.id, msg);
        break;
      case "switch_conversation":
        await this.handleSwitchConversation(socket, session.id, msg);
        break;
      case "new_conversation":
        this.sessionManager.setConversationId(session.id, "");
        session.conversationId = null;
        session.agentBusy = false;
        this.send(socket, {
          type: "connected",
          sessionId: session.id,
          conversationId: undefined,
        });
        break;
      case "list_conversations":
        await this.handleListConversations(socket);
        break;
      case "delete_conversation":
        await this.handleDeleteConversation(socket, session.id, msg);
        break;
    }
  }

  private async handleChatMessage(
    socket: WebSocket,
    sessionId: string,
    msg: IncomingMessage
  ): Promise<void> {
    if (!msg.content) return;

    const session = this.sessionManager.getSession(sessionId);
    if (!session) return;

    const conversationId = msg.conversationId || session.conversationId || undefined;

    if (session.agentBusy && conversationId) {
      // Agent is busy — queue the message in DB
      try {
        const queueResult = await this.backendClient.queueMessage(conversationId, msg.content);
        this.send(socket, {
          type: "queued",
          sessionId,
          messageId: queueResult.message_id,
          queuePosition: queueResult.queue_position,
          conversationId,
        });
      } catch (err) {
        this.send(socket, {
          type: "error",
          sessionId,
          error: err instanceof Error ? err.message : "Failed to queue message",
        });
      }
      return;
    }

    // Agent is idle — start a new turn with SSE streaming
    await this.startStreamingTurn(socket, sessionId, msg.content, conversationId, msg.agentId, msg.planId);
  }

  private async handleInterrupt(
    socket: WebSocket,
    sessionId: string,
    msg: IncomingMessage
  ): Promise<void> {
    const session = this.sessionManager.getSession(sessionId);
    if (!session) return;

    const conversationId = msg.conversationId || session.conversationId;
    if (!conversationId) return;

    try {
      await this.backendClient.interrupt(conversationId);
      this.send(socket, { type: "status", sessionId, agentStatus: "idle", conversationId });
    } catch (err) {
      this.send(socket, { type: "error", sessionId, error: err instanceof Error ? err.message : "Failed to interrupt" });
    }
  }

  private async handlePause(
    socket: WebSocket,
    sessionId: string,
    msg: IncomingMessage
  ): Promise<void> {
    const session = this.sessionManager.getSession(sessionId);
    if (!session) return;

    const conversationId = msg.conversationId || session.conversationId;
    if (!conversationId) return;

    try {
      await this.backendClient.interrupt(conversationId);
      this.send(socket, { type: "status", sessionId, agentStatus: "idle", conversationId });
    } catch (err) {
      this.send(socket, { type: "error", sessionId, error: err instanceof Error ? err.message : "Failed to pause" });
    }
  }

  private async startStreamingTurn(
    socket: WebSocket,
    sessionId: string,
    message: string | undefined,
    conversationId: string | undefined,
    agentId?: string,
    planId?: string,
  ): Promise<void> {
    const session = this.sessionManager.getSession(sessionId);
    if (!session) return;

    if (!conversationId) {
      // No conversation yet — generate one; backend will create it with the right agent
      const { ulid } = await import("ulid");
      conversationId = ulid();
    }

    await this.startTurn(socket, sessionId, message, conversationId, agentId, planId);
  }

  private async startTurn(
    socket: WebSocket,
    sessionId: string,
    message: string | undefined,
    conversationId: string,
    agentId?: string,
    planId?: string,
  ): Promise<void> {
    const session = this.sessionManager.getSession(sessionId);
    if (!session) return;

    const startTime = Date.now();
    session.agentBusy = true;

    this.send(socket, {
      type: "status",
      sessionId,
      agentStatus: "thinking",
      conversationId,
    });

    try {
      let responseMessageId = "";
      let agentName = "";

      for await (const event of this.backendClient.conversationTurnStream(
        conversationId, message, agentId, planId,
      )) {
        console.log(`[GATEWAY-WEBCHAT] Received SSE event: ${event.event}`, event.data ? `data keys: ${Object.keys(event.data)}` : 'no data');
        switch (event.event) {
          case "status":
            if (!agentName && event.data.agent_name) agentName = event.data.agent_name as string;
            console.log(`[GATEWAY-WEBCHAT] Sending status: ${event.data.state}`);
            this.send(socket, {
              type: "status",
              sessionId,
              agentStatus: event.data.state as "thinking" | "tool_calling" | "responding",
              conversationId,
            });
            break;

          case "chunk":
            const chunkContent = event.data.content as string;
            console.log(`[GATEWAY-WEBCHAT] Sending chunk: ${chunkContent.length} chars, first 50: ${chunkContent.substring(0, 50)}`);
            this.send(socket, {
              type: "chunk",
              sessionId,
              content: chunkContent,
              agentName,
              conversationId,
            });
            break;

          case "tool_call":
            this.send(socket, {
              type: "tool_call",
              sessionId,
              content: JSON.stringify(event.data),
              conversationId,
            });
            break;

          case "plan_created":
            this.broadcast({ type: "plan_created", sessionId,
              planId: event.data.plan_id as string,
              planTitle: event.data.title as string,
              planStatus: "active", conversationId });
            break;

          case "item_created":
            this.broadcast({ type: "item_updated", sessionId,
              planId: event.data.plan_id as string,
              itemId: event.data.item_id as string,
              itemStatus: "new",
              itemTitle: (event.data.title as string) || "", conversationId });
            break;

          case "item_updated":
            this.broadcast({ type: "item_updated", sessionId,
              planId: event.data.plan_id as string,
              itemId: event.data.item_id as string,
              itemStatus: event.data.status as string,
              itemTitle: (event.data.title as string) || "", conversationId });
            break;

          case "plan_completed":
            this.broadcast({ type: "plan_completed", sessionId,
              planId: event.data.plan_id as string,
              planStatus: event.data.status as string, conversationId });
            break;

          case "done":
            responseMessageId = (event.data.message_id as string) || "";
            console.log(`[GATEWAY-WEBCHAT] Received done event, message_id: ${responseMessageId}, conversationId: ${conversationId}`);
            if (conversationId) {
              this.sessionManager.setConversationId(sessionId, conversationId);
              session.conversationId = conversationId;
            }
            break;

          case "error":
            this.send(socket, {
              type: "error", sessionId,
              error: event.data.message as string, conversationId,
            });
            break;
        }
      }

      session.agentBusy = false;
      const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
      console.log(`[gateway] Turn complete: conversation=${conversationId} elapsed=${elapsed}s`);

      this.send(socket, {
        type: "done", sessionId, conversationId,
        messageId: responseMessageId, agentName,
        queuedCount: 0, agentStatus: "idle",
      });

      this.handleListConversations(socket).catch(() => {});
    } catch (err) {
      session.agentBusy = false;
      const msg = err instanceof Error ? err.message : "Agent error";
      this.send(socket, { type: "status", sessionId, agentStatus: "idle", conversationId });
      this.send(socket, { type: "error", sessionId, error: msg });
    }
  }

  private async handleSwitchConversation(
    socket: WebSocket,
    sessionId: string,
    msg: IncomingMessage
  ): Promise<void> {
    if (!msg.conversationId) return;

    try {
      const conv = await this.backendClient.getConversation(msg.conversationId);
      this.sessionManager.setConversationId(sessionId, msg.conversationId);

      this.send(socket, {
        type: "history",
        sessionId,
        conversationId: msg.conversationId,
        messages: conv.messages.map((m) => ({
          role: m.role,
          content: m.content,
          id: m.id,
          created_at: m.created_at,
        })),
      });
    } catch (err) {
      this.send(socket, {
        type: "error",
        sessionId,
        error: err instanceof Error ? err.message : "Failed to load conversation",
      });
    }
  }

  private async handleListConversations(socket: WebSocket): Promise<void> {
    try {
      const conversations = await this.backendClient.listConversations();
      this.send(socket, {
        type: "conversations_list",
        conversations,
      });
    } catch (err) {
      this.send(socket, {
        type: "error",
        error: err instanceof Error ? err.message : "Failed to list conversations",
      });
    }
  }

  private async handleDeleteConversation(
    socket: WebSocket,
    sessionId: string,
    msg: IncomingMessage
  ): Promise<void> {
    if (!msg.conversationId) return;

    try {
      await this.backendClient.deleteConversation(msg.conversationId);

      // If we deleted the active conversation, clear it
      const session = this.sessionManager.getSession(sessionId);
      if (session?.conversationId === msg.conversationId) {
        session.conversationId = null;
      }

      // Refresh conversation list
      await this.handleListConversations(socket);
    } catch (err) {
      this.send(socket, {
        type: "error",
        error: err instanceof Error ? err.message : "Failed to delete conversation",
      });
    }
  }

  public broadcast(msg: OutgoingMessage): void {
    const payload = JSON.stringify(msg);
    console.log(`[gateway] Broadcasting message: type=${msg.type} sessionId=${msg.sessionId || 'all'}`);
    for (const socket of this.sessionManager.getAllSockets()) {
      if (socket.readyState === 1) { // 1 is OPEN
        socket.send(payload);
      }
    }
  }

  private send(socket: WebSocket, msg: OutgoingMessage): void {
    if (socket.readyState === socket.OPEN) {
      socket.send(JSON.stringify(msg));
    }
  }
}
