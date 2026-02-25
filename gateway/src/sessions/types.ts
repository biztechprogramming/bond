/**
 * Session types for the gateway.
 */

import type { WebSocket } from "ws";

export interface Session {
  id: string;
  conversationId: string | null;
  agentBusy: boolean;
  createdAt: Date;
}

export interface ConnectedClient {
  socket: WebSocket;
  sessionId: string;
  connectedAt: Date;
}
