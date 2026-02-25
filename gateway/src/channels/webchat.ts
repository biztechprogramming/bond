/**
 * WebChat channel — handles messages from the web frontend via WebSocket.
 *
 * This is the primary channel for Sprint 1. The frontend connects via WS,
 * sends messages, and receives responses streamed back.
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
    if (msg.type !== "message") return;

    const client = this.sessionManager.getClient(socket);
    if (!client) return;

    const session = this.sessionManager.getSession(client.sessionId);
    if (!session) return;

    // Add user message to history
    this.sessionManager.addToHistory(session.id, "user", msg.content);

    // Call backend agent
    try {
      const result = await this.backendClient.agentTurn({
        message: msg.content,
        history: session.history.slice(0, -1), // Exclude last (it's in the message)
      });

      // Add assistant response to history
      this.sessionManager.addToHistory(session.id, "assistant", result.response);

      // Send response back to client
      this.send(socket, {
        type: "response",
        sessionId: session.id,
        content: result.response,
      });
    } catch (err) {
      this.send(socket, {
        type: "error",
        sessionId: session.id,
        error: err instanceof Error ? err.message : "Agent error",
      });
    }
  }

  private send(socket: WebSocket, msg: OutgoingMessage): void {
    if (socket.readyState === socket.OPEN) {
      socket.send(JSON.stringify(msg));
    }
  }
}
