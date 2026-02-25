/**
 * Backend client — HTTP bridge from gateway to Python FastAPI backend.
 *
 * Calls the backend's agent turn endpoint and conversation APIs.
 */

import type { ConversationSummary } from "../protocol/types.js";

export interface AgentTurnRequest {
  message: string;
  conversation_id?: string | null;
  stream?: boolean;
}

export interface AgentTurnResponse {
  response: string;
  conversation_id: string;
  message_id: string;
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

  async healthCheck(): Promise<boolean> {
    try {
      const res = await fetch(`${this.baseUrl}/api/v1/health`);
      return res.ok;
    } catch {
      return false;
    }
  }
}
