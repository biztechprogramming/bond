/**
 * Backend client — HTTP bridge from gateway to Python FastAPI backend.
 *
 * Calls the backend's agent turn endpoint and conversation APIs.
 */

import type { ConversationSummary } from "../protocol/types.js";
import { parseSSEStream, type SSEEvent } from "./sse-parser.js";

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

export type { SSEEvent } from "./sse-parser.js";

export interface AgentResolution {
  mode: "container" | "host";
  worker_url?: string;
  agent_id: string;
  agent_name?: string;
  agent_display_name?: string;
  conversation_id: string;
}

export interface MemoryPromotionEvent {
  agent_id: string;
  memory_id: string;
  type: string;
  content: string;
  summary?: string;
  source_type?: string;
  entities?: string[];
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

export interface AgentInfo {
  id: string;
  name: string;
  display_name: string;
  is_default: boolean;
}

export class BackendClient {
  private baseUrl: string;

  constructor(baseUrl: string) {
    this.baseUrl = baseUrl;
  }

  async getSetting(key: string): Promise<string | null> {
    try {
      const res = await fetch(`${this.baseUrl}/api/v1/settings`);
      if (!res.ok) return null;
      const settings = (await res.json()) as Record<string, string>;
      return settings[key] ?? null;
    } catch {
      return null;
    }
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

  async createConversation(id: string, agentId?: string, channel?: string, title?: string): Promise<void> {
    const res = await fetch(`${this.baseUrl}/api/v1/conversations`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, agent_id: agentId ?? null, channel: channel ?? "webchat", title: title ?? null }),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Backend error ${res.status}: ${text}`);
    }
  }

  async listConversations(): Promise<ConversationSummary[]> {
    const res = await fetch(`${this.baseUrl}/api/v1/conversations`);
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Backend error ${res.status}: ${text}`);
    }
    return (await res.json()) as ConversationSummary[];
  }

  async *conversationTurnStream(
    conversationId: string,
    message: string | undefined,
    agentId?: string,
    planId?: string,
  ): AsyncGenerator<SSEEvent> {
    console.log(`[BACKEND-CLIENT] Calling backend: ${this.baseUrl}/api/v1/conversations/${conversationId}/turn`);
    const res = await fetch(`${this.baseUrl}/api/v1/conversations/${conversationId}/turn`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: message ?? null,
        agent_id: agentId ?? null,
        plan_id: planId ?? null,
      }),
    });
    console.log(`[BACKEND-CLIENT] Backend response status: ${res.status}, ok: ${res.ok}, headers: ${JSON.stringify(Object.fromEntries(res.headers.entries()))}`);
    if (!res.ok) {
      const text = await res.text();
      console.error(`[BACKEND-CLIENT] Backend error ${res.status}: ${text}`);
      throw new Error(`Backend error ${res.status}: ${text}`);
    }
    console.log(`[BACKEND-CLIENT] Starting to parse SSE stream`);
    yield* parseSSEStream(res);
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

    yield* parseSSEStream(res);
  }



  async saveUserMessage(
    conversationId: string,
    content: string,
  ): Promise<{ message_id: string }> {
    const res = await fetch(`${this.baseUrl}/api/v1/conversations/${conversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: "user", content }),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Backend error ${res.status}: ${text}`);
    }
    return (await res.json()) as { message_id: string };
  }

  async saveAssistantMessage(
    conversationId: string,
    content: string,
    toolCallsMade: number,
  ): Promise<{ message_id: string }> {
    const res = await fetch(`${this.baseUrl}/api/v1/conversations/${conversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role: "assistant", content, tool_calls_made: toolCallsMade }),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Backend error ${res.status}: ${text}`);
    }
    return (await res.json()) as { message_id: string };
  }

  async promoteMemory(data: MemoryPromotionEvent): Promise<void> {
    const res = await fetch(`${this.baseUrl}/api/v1/shared-memories`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Backend error ${res.status}: ${text}`);
    }
  }

  async listAgents(): Promise<AgentInfo[]> {
    try {
      const res = await fetch(`${this.baseUrl}/api/v1/agents`);
      if (!res.ok) return [];
      const agents = await res.json();
      return (agents as any[]).map((a) => ({
        id: a.id,
        name: a.name,
        display_name: a.display_name,
        is_default: !!a.is_default,
      }));
    } catch {
      return [];
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
