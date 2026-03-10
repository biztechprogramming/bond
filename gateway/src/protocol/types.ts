/**
 * WebSocket protocol types for Bond gateway <-> frontend communication.
 */

export interface IncomingMessage {
  type: "message" | "interrupt" | "pause" | "switch_conversation" | "new_conversation" | "list_conversations" | "delete_conversation";
  sessionId?: string;
  content?: string;
  conversationId?: string;
  agentId?: string;
  planId?: string;
}

export interface OutgoingMessage {
  type: "response" | "chunk" | "error" | "connected" | "history" | "conversations_list"
    | "queued" | "status" | "tool_call" | "tool_result" | "new_input" | "done"
    | "plan_created" | "item_updated" | "plan_completed"
    | "user_message";
  sessionId?: string;
  content?: string;
  error?: string;
  conversationId?: string;
  messageId?: string;
  agentStatus?: "idle" | "thinking" | "tool_calling" | "responding";
  agentName?: string;
  queuePosition?: number;
  queuedCount?: number;
  agentId?: string;
  messages?: Array<{ role: string; content: string; id?: string; created_at?: string }>;
  conversations?: Array<ConversationSummary>;
  planId?: string;
  planTitle?: string;
  planStatus?: string;
  itemId?: string;
  itemStatus?: string;
  itemTitle?: string;
}

export interface ConversationSummary {
  id: string;
  title: string | null;
  message_count: number;
  updated_at: string;
  agent_id: string | null;
  agent_name: string | null;
}

export interface SessionInfo {
  sessionId: string;
  createdAt: string;
}
