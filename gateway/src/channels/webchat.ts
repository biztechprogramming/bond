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
      case "switch_conversation":
        await this.handleSwitchConversation(socket, session.id, msg);
        break;
      case "new_conversation":
        this.sessionManager.setConversationId(session.id, "");
        session.conversationId = null;
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

    try {
      const result = await this.backendClient.agentTurn({
        message: msg.content,
        conversation_id: msg.conversationId || session.conversationId || undefined,
      });

      // Store conversation ID in session
      this.sessionManager.setConversationId(sessionId, result.conversation_id);

      // Send response back to client
      this.send(socket, {
        type: "response",
        sessionId,
        content: result.response,
        conversationId: result.conversation_id,
      });
    } catch (err) {
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
