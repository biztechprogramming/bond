/**
 * WebSocket protocol types for Bond gateway <-> frontend communication.
 */

export interface IncomingMessage {
  type: "message";
  sessionId: string;
  content: string;
}

export interface OutgoingMessage {
  type: "response" | "chunk" | "error" | "connected";
  sessionId?: string;
  content?: string;
  error?: string;
}

export interface SessionInfo {
  sessionId: string;
  createdAt: string;
}
