/**
 * WebSocket client for connecting to the Bond gateway.
 * Supports automatic reconnection with exponential backoff.
 */

import { GATEWAY_WS } from "./config";

export type MessageHandler = (msg: GatewayMessage) => void;
export type ConnectionHandler = (state: ConnectionState) => void;

export type ConnectionState = "connecting" | "connected" | "disconnected" | "reconnecting";

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
    | "plan_created" | "plan_updated" | "item_updated" | "plan_completed"
    | "user_message" | "pong"
    | "coding_agent_started" | "coding_agent_diff" | "coding_agent_done" | "coding_agent_output"
    | "webhook_push";
  sessionId?: string;
  content?: string;
  error?: string;
  conversationId?: string;
  messageId?: string;
  agentStatus?: "idle" | "thinking" | "tool_calling" | "responding" | "stopping" | "interrupted";
  agentId?: string;
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
  private connectionHandlers: ConnectionHandler[] = [];
  private sessionId: string | null = null;
  private url: string;
  private shouldReconnect = true;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private maxReconnectDelay = 30000;
  private baseReconnectDelay = 1000;
  private _connectionState: ConnectionState = "disconnected";

  // Heartbeat
  private pingInterval: ReturnType<typeof setInterval> | null = null;
  private pongTimeout: ReturnType<typeof setTimeout> | null = null;
  private readonly pingIntervalMs = 25000;
  private readonly pongTimeoutMs = 10000;

  // Track visibility for smarter reconnection
  private visibilityHandler: (() => void) | null = null;

  constructor(url?: string) {
    this.url = url || `${GATEWAY_WS}/ws`;
  }

  connect(): void {
    this.shouldReconnect = true;
    this.setConnectionState("connecting");
    this._connect();
    this._setupVisibilityHandler();
  }

  private _connect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    // Clean up any existing socket
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.onerror = null;
      this.ws.onopen = null;
      this.ws.onmessage = null;
      try { this.ws.close(); } catch { /* ignore */ }
      this.ws = null;
    }

    try {
      console.log("[ws] Connecting to:", this.url);
      this.ws = new WebSocket(this.url);
    } catch (err) {
      console.error("[ws] Failed to create WebSocket:", err);
      this._scheduleReconnect();
      return;
    }

    this.ws.onopen = () => {
      console.log("[ws] Connected to gateway");
      this.reconnectAttempts = 0;
      this._startPing();
    };

    this.ws.onmessage = (event) => {
      try {
        const msg: GatewayMessage = JSON.parse(event.data);

        // Handle pong (heartbeat response)
        if (msg.type === "pong") {
          this._handlePong();
          return;
        }

        if (msg.type === "connected" && msg.sessionId) {
          this.sessionId = msg.sessionId;
          this.setConnectionState("connected");
        }
        this.handlers.forEach((h) => h(msg));
      } catch (err) {
        console.error("[ws] Failed to parse message:", err);
      }
    };

    this.ws.onclose = (event) => {
      console.log(`[ws] Connection closed (code: ${event.code}, reason: ${event.reason || "none"})`);
      this._stopPing();
      this.ws = null;

      if (this.shouldReconnect) {
        this._scheduleReconnect();
      } else {
        this.setConnectionState("disconnected");
      }
    };

    this.ws.onerror = (event) => {
      console.error("[ws] WebSocket error:", event);
      // onclose will fire after onerror, so reconnection is handled there
    };
  }

  private _scheduleReconnect(): void {
    if (!this.shouldReconnect) return;
    if (this.reconnectTimer) return; // already scheduled

    this.reconnectAttempts++;
    // Exponential backoff with jitter: base * 2^attempts + random jitter
    const delay = Math.min(
      this.baseReconnectDelay * Math.pow(2, this.reconnectAttempts - 1) + Math.random() * 1000,
      this.maxReconnectDelay,
    );

    console.log(`[ws] Reconnecting in ${Math.round(delay)}ms (attempt ${this.reconnectAttempts})`);
    this.setConnectionState("reconnecting");

    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.shouldReconnect) {
        this._connect();
      }
    }, delay);
  }

  // --- Heartbeat (client-side ping) ---

  private _startPing(): void {
    this._stopPing();
    this.pingInterval = setInterval(() => {
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: "ping" }));
        // If we don't get a pong back in time, force-close and reconnect
        this.pongTimeout = setTimeout(() => {
          console.warn("[ws] Pong timeout — closing connection");
          this.ws?.close(4000, "pong timeout");
        }, this.pongTimeoutMs);
      }
    }, this.pingIntervalMs);
  }

  private _stopPing(): void {
    if (this.pingInterval) {
      clearInterval(this.pingInterval);
      this.pingInterval = null;
    }
    if (this.pongTimeout) {
      clearTimeout(this.pongTimeout);
      this.pongTimeout = null;
    }
  }

  private _handlePong(): void {
    if (this.pongTimeout) {
      clearTimeout(this.pongTimeout);
      this.pongTimeout = null;
    }
  }

  // --- Visibility handling ---
  // When the tab becomes visible again, check connection health

  private _setupVisibilityHandler(): void {
    if (this.visibilityHandler) return;
    this.visibilityHandler = () => {
      if (document.visibilityState === "visible" && this.shouldReconnect) {
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
          console.log("[ws] Tab visible — triggering reconnect");
          this._scheduleReconnect();
        }
      }
    };
    document.addEventListener("visibilitychange", this.visibilityHandler);
  }

  private _teardownVisibilityHandler(): void {
    if (this.visibilityHandler) {
      document.removeEventListener("visibilitychange", this.visibilityHandler);
      this.visibilityHandler = null;
    }
  }

  // --- Connection state ---

  private setConnectionState(state: ConnectionState): void {
    if (this._connectionState === state) return;
    this._connectionState = state;
    this.connectionHandlers.forEach((h) => h(state));
  }

  onConnectionChange(handler: ConnectionHandler): () => void {
    this.connectionHandlers.push(handler);
    // Immediately fire with current state
    handler(this._connectionState);
    return () => {
      this.connectionHandlers = this.connectionHandlers.filter((h) => h !== handler);
    };
  }

  get connectionState(): ConnectionState {
    return this._connectionState;
  }

  // --- Message handlers ---

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

  /**
   * Inject context mid-turn (037 §5.3.1).
   * Sends a message that interrupts the current LLM call and injects
   * the content into the agent's context immediately.
   */
  inject(conversationId: string, content: string): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
    this.ws.send(
      JSON.stringify({
        type: "inject",
        conversationId,
        content,
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
    this._stopPing();
    this._teardownVisibilityHandler();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.onclose = null; // prevent reconnect on intentional close
      this.ws.close();
      this.ws = null;
    }
    this.setConnectionState("disconnected");
  }

  get connected(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  getSessionId(): string | null {
    return this.sessionId;
  }
}
