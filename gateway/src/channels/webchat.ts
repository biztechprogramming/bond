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
    await this.startStreamingTurn(socket, sessionId, msg.content, conversationId);
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
      this.send(socket, {
        type: "status",
        sessionId,
        agentStatus: "idle",
        conversationId,
      });
    } catch (err) {
      this.send(socket, {
        type: "error",
        sessionId,
        error: err instanceof Error ? err.message : "Failed to interrupt",
      });
    }
  }

  private async startStreamingTurn(
    socket: WebSocket,
    sessionId: string,
    message: string | undefined,
    conversationId: string | undefined,
  ): Promise<void> {
    const session = this.sessionManager.getSession(sessionId);
    if (!session) return;

    session.agentBusy = true;
    this.send(socket, {
      type: "status",
      sessionId,
      agentStatus: "thinking",
      conversationId,
    });

    try {
      let responseContent = "";
      let responseConversationId = conversationId || "";
      let responseMessageId = "";
      let queuedCount = 0;

      for await (const event of this.backendClient.agentTurnStream({
        message: message || undefined,
        conversation_id: conversationId,
      })) {
        switch (event.event) {
          case "status":
            this.send(socket, {
              type: "status",
              sessionId,
              agentStatus: event.data.state as "thinking" | "tool_calling" | "responding",
              conversationId: (event.data.conversation_id as string) || responseConversationId,
            });
            if (event.data.conversation_id) {
              responseConversationId = event.data.conversation_id as string;
            }
            break;
          case "chunk":
            responseContent += (event.data.content as string) || "";
            this.send(socket, {
              type: "chunk",
              sessionId,
              content: event.data.content as string,
              conversationId: responseConversationId,
            });
            break;
          case "new_input":
            this.send(socket, {
              type: "new_input",
              sessionId,
              conversationId: responseConversationId,
              queuedCount: event.data.count as number,
            });
            break;
          case "done":
            responseMessageId = (event.data.message_id as string) || "";
            responseConversationId = (event.data.conversation_id as string) || responseConversationId;
            queuedCount = (event.data.queued_count as number) || 0;
            break;
        }
      }

      // Store conversation ID in session
      if (responseConversationId) {
        this.sessionManager.setConversationId(sessionId, responseConversationId);
        session.conversationId = responseConversationId;
      }

      session.agentBusy = false;

      // Send done + full response
      this.send(socket, {
        type: "done",
        sessionId,
        conversationId: responseConversationId,
        messageId: responseMessageId,
        queuedCount,
        agentStatus: "idle",
      });

      // Also send a response message for backward compatibility
      if (responseContent) {
        this.send(socket, {
          type: "response",
          sessionId,
          content: responseContent,
          conversationId: responseConversationId,
        });
      }

      // Refresh conversation list
      this.handleListConversations(socket).catch(() => {});

      // Auto-continue if there are queued messages
      if (queuedCount > 0) {
        setTimeout(() => {
          this.startStreamingTurn(socket, sessionId, undefined, responseConversationId);
        }, 500);
      }
    } catch (err) {
      session.agentBusy = false;
      this.send(socket, {
        type: "status",
        sessionId,
        agentStatus: "idle",
        conversationId,
      });
      this.send(socket, {
        type: "error",
        sessionId,
        error: err instanceof Error ? err.message : "Agent error",
      });
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

  private send(socket: WebSocket, msg: OutgoingMessage): void {
    if (socket.readyState === socket.OPEN) {
      socket.send(JSON.stringify(msg));
    }
  }
}
