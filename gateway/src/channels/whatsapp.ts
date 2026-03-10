/**
 * WhatsApp channel adapter using @whiskeysockets/baileys (multi-device, QR code).
 */
import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  type WASocket,
  type ConnectionState,
} from "@whiskeysockets/baileys";
import { Boom } from "@hapi/boom";
import type { ChannelAdapter, ChannelMessage } from "./base.js";
import { AllowList } from "./allowlist.js";

export interface WhatsAppChannelOptions {
  authDir: string;
  allowList: AllowList;
  onMessage: (msg: ChannelMessage) => void;
  onQR?: (qr: string) => void;
  onStatusChange?: (status: "connecting" | "open" | "close") => void;
  persistFn?: (allowList: string[]) => void;
}

export class WhatsAppChannel implements ChannelAdapter {
  readonly channelType = "whatsapp";
  private socket: WASocket | null = null;
  private allowList: AllowList;
  private onMessageCb: (msg: ChannelMessage) => void;
  private onQRCb?: (qr: string) => void;
  private onStatusChangeCb?: (status: "connecting" | "open" | "close") => void;
  private persistFn?: (allowList: string[]) => void;
  private authDir: string;
  private running = false;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 10;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(options: WhatsAppChannelOptions) {
    this.authDir = options.authDir;
    this.allowList = options.allowList;
    this.onMessageCb = options.onMessage;
    this.onQRCb = options.onQR;
    this.onStatusChangeCb = options.onStatusChange;
    this.persistFn = options.persistFn;
  }

  async start(): Promise<void> {
    if (this.running) return;
    this.running = true;
    this.reconnectAttempts = 0;
    await this.connect();
  }

  private async connect(): Promise<void> {
    const { state, saveCreds } = await useMultiFileAuthState(this.authDir);

    this.socket = makeWASocket({
      auth: state,
      printQRInTerminal: false,
    });

    this.socket.ev.on("connection.update", (update: Partial<ConnectionState>) => {
      if (update.qr && this.onQRCb) {
        this.onQRCb(update.qr);
      }

      if (update.connection === "open") {
        console.log("[whatsapp] Connected");
        this.reconnectAttempts = 0;
        this.onStatusChangeCb?.("open");

        // Auto allow-list: the linked phone number is the owner
        const ownerId = this.socket?.user?.id;
        if (ownerId) {
          this.allowList.add(ownerId);
          // Also add the bare JID (without device suffix)
          const bareJid = ownerId.split(":")[0] + "@s.whatsapp.net";
          this.allowList.add(bareJid);
          this.persistAllowList();
        }
      }

      if (update.connection === "close") {
        this.onStatusChangeCb?.("close");
        const statusCode = (update.lastDisconnect?.error as Boom)?.output?.statusCode;
        const shouldReconnect = statusCode !== DisconnectReason.loggedOut;

        if (shouldReconnect && this.running) {
          this.scheduleReconnect();
        } else {
          console.log("[whatsapp] Logged out or stopped, not reconnecting");
          this.running = false;
        }
      }

      if (update.connection === "connecting") {
        this.onStatusChangeCb?.("connecting");
      }
    });

    this.socket.ev.on("creds.update", saveCreds);

    this.socket.ev.on("messages.upsert", ({ messages }) => {
      for (const msg of messages) {
        if (!msg.message || msg.key.fromMe) continue;
        const senderId = msg.key.remoteJid;
        if (!senderId) continue;
        if (!this.allowList.isAllowed(senderId)) continue;

        const text =
          msg.message.conversation ||
          msg.message.extendedTextMessage?.text ||
          "";

        if (!text) continue;

        this.onMessageCb({
          channelType: "whatsapp",
          senderId,
          content: text,
          sessionId: senderId,
          metadata: {
            remoteJid: senderId,
            pushName: msg.pushName,
          },
        });
      }
    });
  }

  async stop(): Promise<void> {
    this.running = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.socket) {
      this.socket.end(undefined);
      this.socket = null;
    }
    console.log("[whatsapp] Stopped");
  }

  async send(channelId: string, message: string): Promise<void> {
    if (!this.socket) throw new Error("WhatsApp not connected");
    await this.socket.sendMessage(channelId, { text: message });
  }

  isRunning(): boolean {
    return this.running;
  }

  isConnected(): boolean {
    return this.socket?.user != null;
  }

  getUser(): { id: string; name?: string } | null {
    if (!this.socket?.user) return null;
    return { id: this.socket.user.id, name: this.socket.user.name };
  }

  getAllowList(): AllowList {
    return this.allowList;
  }

  /** Set a QR callback (replaces any previous one). */
  setOnQR(cb: (qr: string) => void): void {
    this.onQRCb = cb;
  }

  private scheduleReconnect(): void {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.log("[whatsapp] Max reconnect attempts reached");
      this.running = false;
      return;
    }

    // Exponential backoff with jitter: base * 2^attempt + random jitter
    const baseDelay = 1000;
    const delay = baseDelay * Math.pow(2, this.reconnectAttempts) + Math.random() * 1000;
    this.reconnectAttempts++;

    console.log(`[whatsapp] Reconnecting in ${Math.round(delay)}ms (attempt ${this.reconnectAttempts})`);
    this.reconnectTimer = setTimeout(() => {
      if (this.running) {
        this.connect().catch((err) => {
          console.error("[whatsapp] Reconnect failed:", err);
          this.scheduleReconnect();
        });
      }
    }, delay);
  }

  private persistAllowList(): void {
    if (this.persistFn) {
      this.persistFn(this.allowList.toArray());
    }
  }
}
