/**
 * WebSocket protocol types for Bond gateway <-> frontend communication.
 */

export interface IncomingMessage {
  type: "message" | "interrupt" | "switch_conversation" | "new_conversation" | "list_conversations" | "delete_conversation";
  sessionId?: string;
  content?: string;
  conversationId?: string;
  agentId?: string;
}

export interface OutgoingMessage {
  type: "response" | "chunk" | "error" | "connected" | "history" | "conversations_list"
    | "queued" | "status" | "tool_call" | "tool_result" | "new_input" | "done";
  sessionId?: string;
  content?: string;
  error?: string;
  conversationId?: string;
  messageId?: string;
  agentStatus?: "idle" | "thinking" | "tool_calling" | "responding";
  queuePosition?: number;
  queuedCount?: number;
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
