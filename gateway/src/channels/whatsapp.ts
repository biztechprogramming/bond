/**
 * WhatsApp channel adapter using @whiskeysockets/baileys (multi-device, QR code).
 *
 * Connection lifecycle:
 *   start() → connect() → QR scan → pairing → 515 restart → connect() → open
 *
 * Key design decisions:
 *   - Track connection state explicitly (not derived from socket.user which is set from creds)
 *   - Clean up old socket before reconnecting to prevent event listener duplication
 *   - Don't count 515 "restart required" as a failure (it's expected after pairing)
 *   - Validate creds before auto-start to avoid spinning on corrupt auth state
 *   - Clean up auth dir on remove to prevent stale pre-key accumulation
 */
import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
  type WASocket,
  type ConnectionState,
} from "@whiskeysockets/baileys";
import { Boom } from "@hapi/boom";
import { existsSync, readFileSync, readdirSync, rmSync } from "fs";
import { join } from "path";
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
  private connected = false;
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
    this.connected = false;
    this.reconnectAttempts = 0;
    await this.connect();
  }

  private async connect(): Promise<void> {
    // Clean up any existing socket before creating a new one
    this.destroySocket();

    const { state, saveCreds } = await useMultiFileAuthState(this.authDir);

    // If we were stopped during async auth state load, bail out
    if (!this.running) return;

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
        this.connected = true;
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
        this.connected = false;
        this.onStatusChangeCb?.("close");

        const statusCode = (update.lastDisconnect?.error as Boom)?.output?.statusCode;

        if (statusCode === DisconnectReason.loggedOut) {
          console.log("[whatsapp] Logged out, not reconnecting");
          this.running = false;
          return;
        }

        if (!this.running) return;

        // 515 = "restart required" — expected after pairing or server-side update.
        // Don't count it as a failure, reconnect immediately.
        if (statusCode === DisconnectReason.restartRequired) {
          console.log("[whatsapp] Restart required, reconnecting immediately");
          this.connect().catch((err) => {
            console.error("[whatsapp] Reconnect after restart failed:", err);
            this.scheduleReconnect();
          });
          return;
        }

        this.scheduleReconnect();
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
    this.connected = false;
    this.cancelReconnect();
    this.destroySocket();
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
    return this.connected;
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

  /**
   * Check if the auth directory has valid credentials.
   * Used by the manager to decide whether auto-start should proceed.
   */
  static hasValidCreds(authDir: string): boolean {
    try {
      const credsPath = join(authDir, "creds.json");
      if (!existsSync(credsPath)) return false;
      const content = readFileSync(credsPath, "utf-8").trim();
      if (!content) return false;
      const creds = JSON.parse(content);
      // Must have at minimum the noise key pair to be usable
      return !!(creds.noiseKey?.private && creds.noiseKey?.public);
    } catch {
      return false;
    }
  }

  /**
   * Remove all auth state files. Call after removing the channel
   * to prevent stale pre-keys from accumulating on disk.
   */
  static cleanAuthDir(authDir: string): void {
    try {
      if (existsSync(authDir)) {
        const files = readdirSync(authDir);
        for (const file of files) {
          rmSync(join(authDir, file), { force: true });
        }
        console.log(`[whatsapp] Cleaned auth dir (${files.length} files removed)`);
      }
    } catch (err) {
      console.warn("[whatsapp] Failed to clean auth dir:", err);
    }
  }

  /** Destroy the current socket and remove all its event listeners. */
  private destroySocket(): void {
    if (this.socket) {
      try {
        this.socket.ev.removeAllListeners("connection.update");
        this.socket.ev.removeAllListeners("creds.update");
        this.socket.ev.removeAllListeners("messages.upsert");
        this.socket.end(undefined);
      } catch {
        // Socket may already be in a bad state
      }
      this.socket = null;
    }
  }

  private cancelReconnect(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private scheduleReconnect(): void {
    // Cancel any existing timer first
    this.cancelReconnect();

    if (!this.running) return;

    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.log("[whatsapp] Max reconnect attempts reached, giving up");
      this.running = false;
      return;
    }

    // Exponential backoff with jitter: base * 2^attempt + random jitter
    const baseDelay = 1000;
    const delay = baseDelay * Math.pow(2, this.reconnectAttempts) + Math.random() * 1000;
    this.reconnectAttempts++;

    console.log(`[whatsapp] Reconnecting in ${Math.round(delay)}ms (attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts})`);
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
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
