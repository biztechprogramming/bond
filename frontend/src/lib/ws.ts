/**
 * WebSocket client for connecting to the Bond gateway.
 */

export type MessageHandler = (msg: GatewayMessage) => void;

export interface GatewayMessage {
  type: "response" | "chunk" | "error" | "connected";
  sessionId?: string;
  content?: string;
  error?: string;
}

export class GatewayWebSocket {
  private ws: WebSocket | null = null;
  private handlers: MessageHandler[] = [];
  private sessionId: string | null = null;
  private url: string;

  constructor(url?: string) {
    this.url = url || `ws://localhost:18789/ws`;
  }

  connect(): void {
    this.ws = new WebSocket(this.url);

    this.ws.onopen = () => {
      console.log("[ws] Connected to gateway");
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

    this.ws.onclose = () => {
      console.log("[ws] Disconnected from gateway");
    };

    this.ws.onerror = (err) => {
      console.error("[ws] WebSocket error:", err);
    };
  }

  onMessage(handler: MessageHandler): () => void {
    this.handlers.push(handler);
    return () => {
      this.handlers = this.handlers.filter((h) => h !== handler);
    };
  }

  send(content: string): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.error("[ws] Not connected");
      return;
    }

    this.ws.send(
      JSON.stringify({
        type: "message",
        sessionId: this.sessionId || "",
        content,
      })
    );
  }

  disconnect(): void {
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
