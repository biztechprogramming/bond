/**
 * WebSocket client for connecting to the Bond gateway.
 */

export type MessageHandler = (msg: GatewayMessage) => void;

export interface ConversationSummary {
  id: string;
  title: string | null;
  message_count: number;
  updated_at: string;
  agent_id: string | null;
  agent_name: string | null;
}

export interface GatewayMessage {
  type: "response" | "chunk" | "error" | "connected" | "history" | "conversations_list"
    | "queued" | "status" | "tool_call" | "done" | "new_input"
    | "plan_created" | "plan_updated" | "item_updated" | "plan_completed";
  sessionId?: string;
  content?: string;
  error?: string;
  conversationId?: string;
  messageId?: string;
  agentStatus?: "idle" | "thinking" | "tool_calling" | "responding";
  agentName?: string;
  queuePosition?: number;
  queuedCount?: number;
  messages?: Array<{ role: string; content: string; id?: string; created_at?: string }>;
  conversations?: ConversationSummary[];
  // Plan event data
  planId?: string;
  planTitle?: string;
  planStatus?: string;
  itemId?: string;
  itemTitle?: string;
  itemStatus?: string;
}

export class GatewayWebSocket {
  private ws: WebSocket | null = null;
  private handlers: MessageHandler[] = [];
  private sessionId: string | null = null;
  private url: string;
  private shouldReconnect = true;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private maxReconnectDelay = 30000;

  constructor(url?: string) {
    this.url = url || `ws://localhost:18792/ws`;
  }

  connect(): void {
    this.shouldReconnect = true;
    this._connect();
  }

  private _connect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    this.ws = new WebSocket(this.url);

    this.ws.onopen = () => {
      console.log("[ws] Connected to gateway");
      this.reconnectAttempts = 0;
    };

    this.ws.onmessage = (event) => {
      try {
        const msg: GatewayMessage = JSON.parse(event.data);
        if (msg.type === "connected" && msg.sessionId) {
          this.sessionId = msg.sessionId;
        }
        this.handlers.forEach((h) => h(msg));
      } catch (err) {
        console.error("[ws] Failed to parse message:", err);
      }
    };




  }

  onMessage(handler: MessageHandler): () => void {
    this.handlers.push(handler);
    return () => {
      this.handlers = this.handlers.filter((h) => h !== handler);
    };
  }

  send(content: string, conversationId?: string, agentId?: string, planId?: string): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.error("[ws] Not connected");
      return;
    }

    this.ws.send(
      JSON.stringify({
        type: "message",
        sessionId: this.sessionId || "",
        content,
        conversationId,
        agentId,
        planId,
      })
    );
  }

  switchConversation(conversationId: string): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(
      JSON.stringify({
        type: "switch_conversation",
        conversationId,
      })
    );
  }

  newConversation(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ type: "new_conversation" }));
  }

  listConversations(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({ type: "list_conversations" }));
  }

  interrupt(conversationId?: string): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(
      JSON.stringify({
        type: "interrupt",
        conversationId,
      })
    );
  }

  pause(conversationId?: string): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(
      JSON.stringify({
        type: "pause",
        conversationId,
      })
    );
  }

  deleteConversation(conversationId: string): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(
      JSON.stringify({
        type: "delete_conversation",
        conversationId,
      })
    );
  }

  disconnect(): void {
    this.shouldReconnect = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  getSessionId(): string | null {
    return this.sessionId;
  }
}
