/**
 * Backend client — HTTP bridge from gateway to Python FastAPI backend.
 *
 * Calls the backend's agent turn endpoint and conversation APIs.
 */

import type { ConversationSummary } from "../protocol/types.js";

export interface AgentTurnRequest {
  message?: string | null;
  conversation_id?: string | null;
  stream?: boolean;
}

export interface AgentTurnResponse {
  response: string;
  conversation_id: string;
  message_id: string;
  queued_count: number;
}

export interface QueueMessageResponse {
  message_id: string;
  status: string;
  queue_position: number;
}

export interface SSEEvent {
  event: string;
  data: Record<string, unknown>;
}

export interface ConversationMessage {
  id: string;
  role: string;
  content: string;
  tool_calls?: unknown;
  tool_call_id?: string;
  created_at: string;
}

export interface ConversationDetail {
  id: string;
  agent_id: string;
  agent_name: string | null;
  title: string | null;
  message_count: number;
  messages: ConversationMessage[];
}

export class BackendClient {
  private baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl;
  }

  async agentTurn(req: AgentTurnRequest): Promise<AgentTurnResponse> {
    const res = await fetch(`${this.baseUrl}/api/v1/agent/turn`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Backend error ${res.status}: ${text}`);
    }

    return (await res.json()) as AgentTurnResponse;
  }

  async getConversation(id: string): Promise<ConversationDetail> {
    const res = await fetch(`${this.baseUrl}/api/v1/conversations/${id}`);
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Backend error ${res.status}: ${text}`);
    }
    return (await res.json()) as ConversationDetail;
  }

  async listConversations(): Promise<ConversationSummary[]> {
    const res = await fetch(`${this.baseUrl}/api/v1/conversations`);
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Backend error ${res.status}: ${text}`);
    }
    return (await res.json()) as ConversationSummary[];
  }

  async deleteConversation(id: string): Promise<void> {
    const res = await fetch(`${this.baseUrl}/api/v1/conversations/${id}`, {
      method: "DELETE",
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Backend error ${res.status}: ${text}`);
    }
  }

  async queueMessage(conversationId: string, content: string): Promise<QueueMessageResponse> {
    const res = await fetch(`${this.baseUrl}/api/v1/conversations/${conversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content, role: "user" }),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Backend error ${res.status}: ${text}`);
    }
    return (await res.json()) as QueueMessageResponse;
  }

  async interrupt(conversationId: string): Promise<{ status: string }> {
    const res = await fetch(`${this.baseUrl}/api/v1/conversations/${conversationId}/interrupt`, {
      method: "POST",
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Backend error ${res.status}: ${text}`);
    }
    return (await res.json()) as { status: string };
  }

  async *agentTurnStream(req: AgentTurnRequest): AsyncGenerator<SSEEvent> {
    const res = await fetch(`${this.baseUrl}/api/v1/agent/turn`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...req, stream: true }),
    });

    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Backend error ${res.status}: ${text}`);
    }

    const reader = res.body?.getReader();
    if (!reader) throw new Error("No response body");

    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      let currentEvent = "";
      for (const line of lines) {
        if (line.startsWith("event: ")) {
          currentEvent = line.slice(7).trim();
        } else if (line.startsWith("data: ")) {
          const dataStr = line.slice(6);
          try {
            const data = JSON.parse(dataStr);
            yield { event: currentEvent || "message", data };
          } catch {
            // Skip malformed data
          }
          currentEvent = "";
        }
      }
    }
  }

  async healthCheck(): Promise<boolean> {
    try {
      const res = await fetch(`${this.baseUrl}/api/v1/health`);
      return res.ok;
    } catch {
      return false;
    }
  }
}
