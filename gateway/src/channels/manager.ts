/**
 * Channel lifecycle manager.
 * Manages channel configs, start/stop, and routes inbound messages to the backend.
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import { dirname } from "path";
import { AllowList } from "./allowlist.js";
import { TelegramChannel } from "./telegram.js";
import { WhatsAppChannel } from "./whatsapp.js";
import type { ChannelMessage } from "./base.js";
import type { BackendClient } from "../backend/client.js";

interface ChannelConfig {
  type: string;
  enabled: boolean;
  token?: string;
  allowList: string[];
  botInfo?: { id: number; username: string; firstName: string };
}

interface ChannelStatus {
  type: string;
  status: "linked" | "not_linked" | "connecting";
  enabled: boolean;
  botInfo?: { id: number; username: string; firstName: string };
  user?: { id: string; name?: string };
}

export class ChannelManager {
  private configs = new Map<string, ChannelConfig>();
  private telegram: TelegramChannel | null = null;
  private whatsapp: WhatsAppChannel | null = null;
  private configPath: string;
  private backendClient: BackendClient;
  private qrSubscribers = new Set<(qr: string) => void>();
  private statusSubscribers = new Set<(status: string) => void>();
  private whatsappAuthDir: string;

  constructor(configPath: string, backendClient: BackendClient) {
    this.configPath = configPath;
    this.backendClient = backendClient;
    this.whatsappAuthDir = configPath.replace(/[^/]+$/, "whatsapp-auth");
    this.loadConfigs();
  }

  listChannels(): ChannelStatus[] {
    const channels: ChannelStatus[] = [
      { type: "webchat", status: "linked", enabled: true },
    ];

    const telegramConfig = this.configs.get("telegram");
    if (telegramConfig) {
      channels.push({
        type: "telegram",
        status: this.telegram?.isRunning() ? "linked" : "not_linked",
        enabled: telegramConfig.enabled,
        botInfo: telegramConfig.botInfo,
      });
    } else {
      channels.push({ type: "telegram", status: "not_linked", enabled: false });
    }

    const whatsappConfig = this.configs.get("whatsapp");
    if (whatsappConfig) {
      const user = this.whatsapp?.getUser();
      channels.push({
        type: "whatsapp",
        status: this.whatsapp?.isConnected() ? "linked" : this.whatsapp?.isRunning() ? "connecting" : "not_linked",
        enabled: whatsappConfig.enabled,
        user: user || undefined,
      });
    } else {
      channels.push({ type: "whatsapp", status: "not_linked", enabled: false });
    }

    return channels;
  }

  configureTelegram(token: string): void {
    const existing = this.configs.get("telegram");
    this.configs.set("telegram", {
      type: "telegram",
      enabled: true,
      token,
      allowList: existing?.allowList || [],
    });
    this.saveConfigs();
  }

  async startChannel(type: string): Promise<void> {
    if (type === "telegram") {
      const config = this.configs.get("telegram");
      if (!config?.token) throw new Error("Telegram not configured — set up token first");

      if (this.telegram?.isRunning()) return;

      const allowList = new AllowList(config.allowList);
      this.telegram = new TelegramChannel({
        token: config.token,
        allowList,
        onMessage: (msg) => this.handleInboundMessage(msg),
        persistFn: (ids) => {
          config.allowList = ids;
          this.saveConfigs();
        },
      });
      await this.telegram.start();
      config.enabled = true;
      this.saveConfigs();
    } else if (type === "whatsapp") {
      if (this.whatsapp?.isRunning()) return;

      const config = this.configs.get("whatsapp") || {
        type: "whatsapp",
        enabled: true,
        allowList: [],
      };
      this.configs.set("whatsapp", config);

      const allowList = new AllowList(config.allowList);
      this.whatsapp = new WhatsAppChannel({
        authDir: this.whatsappAuthDir,
        allowList,
        onMessage: (msg) => this.handleInboundMessage(msg),
        onQR: (qr) => {
          for (const sub of this.qrSubscribers) {
            sub(qr);
          }
        },
        onStatusChange: (status) => {
          for (const sub of this.statusSubscribers) {
            sub(status);
          }
        },
        persistFn: (ids) => {
          config.allowList = ids;
          this.saveConfigs();
        },
      });
      await this.whatsapp.start();
      config.enabled = true;
      this.saveConfigs();
    } else {
      throw new Error(`Unknown channel type: ${type}`);
    }
  }

  async stopChannel(type: string): Promise<void> {
    if (type === "telegram" && this.telegram) {
      await this.telegram.stop();
      this.telegram = null;
      const config = this.configs.get("telegram");
      if (config) { config.enabled = false; this.saveConfigs(); }
    } else if (type === "whatsapp" && this.whatsapp) {
      await this.whatsapp.stop();
      this.whatsapp = null;
      const config = this.configs.get("whatsapp");
      if (config) { config.enabled = false; this.saveConfigs(); }
    }
  }

  async removeChannel(type: string): Promise<void> {
    await this.stopChannel(type);
    this.configs.delete(type);
    this.saveConfigs();
  }

  isChannelRunning(type: string): boolean {
    if (type === "telegram") return this.telegram?.isRunning() ?? false;
    if (type === "whatsapp") return this.whatsapp?.isRunning() ?? false;
    return false;
  }

  subscribeWhatsAppQR(cb: (qr: string) => void): () => void {
    this.qrSubscribers.add(cb);
    return () => { this.qrSubscribers.delete(cb); };
  }

  subscribeWhatsAppStatus(cb: (status: string) => void): () => void {
    this.statusSubscribers.add(cb);
    return () => { this.statusSubscribers.delete(cb); };
  }

  /**
   * Route inbound channel messages to the backend.
   * Creates a conversation turn similar to webchat flow.
   */
  private async handleInboundMessage(msg: ChannelMessage): Promise<void> {
    console.log(`[channels] Inbound ${msg.channelType} message from ${msg.senderId}: ${msg.content.substring(0, 50)}`);

    try {
      // Use the backend client to start a conversation turn.
      // The sessionId from the channel message serves as the conversation routing key.
      const conversationId = `${msg.channelType}-${msg.sessionId || msg.senderId}`;

      // Stream the response and collect it for sending back
      let fullResponse = "";
      for await (const event of this.backendClient.conversationTurnStream(
        conversationId,
        msg.content,
      )) {
        if (event.event === "chunk" && event.data.content) {
          fullResponse += event.data.content as string;
        }
      }

      // Send the collected response back through the channel
      if (fullResponse) {
        if (msg.channelType === "telegram" && this.telegram) {
          await this.telegram.send(msg.sessionId || msg.senderId, fullResponse);
        } else if (msg.channelType === "whatsapp" && this.whatsapp) {
          await this.whatsapp.send(msg.senderId, fullResponse);
        }
      }
    } catch (err) {
      console.error(`[channels] Error handling ${msg.channelType} message:`, err);
    }
  }

  private loadConfigs(): void {
    try {
      if (existsSync(this.configPath)) {
        const data = JSON.parse(readFileSync(this.configPath, "utf-8"));
        for (const [key, value] of Object.entries(data)) {
          this.configs.set(key, value as ChannelConfig);
        }
        console.log(`[channels] Loaded ${this.configs.size} channel configs`);
      }
    } catch (err) {
      console.warn("[channels] Failed to load channel configs:", err);
    }
  }

  private saveConfigs(): void {
    try {
      const dir = dirname(this.configPath);
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
      const data: Record<string, ChannelConfig> = {};
      for (const [key, value] of this.configs) {
        data[key] = value;
      }
      writeFileSync(this.configPath, JSON.stringify(data, null, 2));
    } catch (err) {
      console.warn("[channels] Failed to save channel configs:", err);
    }
  }

  /**
   * Auto-start channels that were previously enabled.
   */
  async autoStart(): Promise<void> {
    for (const [type, config] of this.configs) {
      if (config.enabled) {
        try {
          console.log(`[channels] Auto-starting ${type}`);
          await this.startChannel(type);
        } catch (err) {
          console.warn(`[channels] Failed to auto-start ${type}:`, err);
        }
      }
    }
  }
}
