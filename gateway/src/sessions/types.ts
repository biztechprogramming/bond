/**
 * Session types for the gateway.
 */

import type { WebSocket } from "ws";

export interface Session {
  id: string;
  createdAt: Date;
  history: Array<{ role: string; content: string }>;
}

export interface ConnectedClient {
  socket: WebSocket;
  sessionId: string;
  connectedAt: Date;
}
