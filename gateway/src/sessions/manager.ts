/**
 * Session manager — tracks sessions and connected clients.
 *
 * History is now stored server-side in the backend database.
 * The gateway only tracks the conversation ID per session.
 */

import { v4 as uuidv4 } from "uuid";
import type { WebSocket } from "ws";
import type { Session, ConnectedClient } from "./types.js";

export class SessionManager {
  private sessions = new Map<string, Session>();
  private clients = new Map<WebSocket, ConnectedClient>();

  createSession(conversationId?: string): Session {
    const session: Session = {
      id: uuidv4(),
      conversationId: conversationId || null,
      createdAt: new Date(),
    };
    this.sessions.set(session.id, session);
    return session;
  }

  getSession(id: string): Session | undefined {
    return this.sessions.get(id);
  }

  getOrCreateSession(id?: string): Session {
    if (id) {
      const existing = this.sessions.get(id);
      if (existing) return existing;
    }
    return this.createSession();
  }

  setConversationId(sessionId: string, conversationId: string): void {
    const session = this.sessions.get(sessionId);
    if (session) {
      session.conversationId = conversationId;
    }
  }

  registerClient(socket: WebSocket, sessionId: string): ConnectedClient {
    const client: ConnectedClient = {
      socket,
      sessionId,
      connectedAt: new Date(),
    };
    this.clients.set(socket, client);
    return client;
  }

  removeClient(socket: WebSocket): void {
    this.clients.delete(socket);
  }

  getClient(socket: WebSocket): ConnectedClient | undefined {
    return this.clients.get(socket);
  }
}
