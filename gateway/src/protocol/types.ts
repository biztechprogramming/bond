/**
 * WebSocket protocol types for Bond gateway <-> frontend communication.
 */

export interface IncomingMessage {
  type: "message" | "switch_conversation" | "new_conversation" | "list_conversations" | "delete_conversation";
  sessionId?: string;
  content?: string;
  conversationId?: string;
}

export interface OutgoingMessage {
  type: "response" | "chunk" | "error" | "connected" | "history" | "conversations_list";
  sessionId?: string;
  content?: string;
  error?: string;
  conversationId?: string;
  messages?: Array<{ role: string; content: string; id?: string; created_at?: string }>;
  conversations?: Array<ConversationSummary>;
}

export interface ConversationSummary {
  id: string;
  title: string | null;
  message_count: number;
  updated_at: string;
  agent_name: string | null;
}

export interface SessionInfo {
  sessionId: string;
  createdAt: string;
}
