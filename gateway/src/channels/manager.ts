/**
 * Channel lifecycle manager.
 * Manages channel configs, start/stop, and routes inbound messages to the backend.
 * Supports multi-agent command routing via /help, /agents, /agent, /all, /reset, /status.
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import { dirname } from "path";
import { ulid } from "ulid";
import { AllowList } from "./allowlist.js";
import { TelegramChannel } from "./telegram.js";
import { WhatsAppChannel } from "./whatsapp.js";
import type { ChannelMessage } from "./base.js";
import type { BackendClient, AgentInfo } from "../backend/client.js";

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

interface ChatSession {
  currentAgentId: string | null;
  currentAgentName: string | null;
  conversations: Map<string, string>;
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
  private chatSessions = new Map<string, ChatSession>();

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
   * Route inbound channel messages — parse commands first, then route to agent.
   */
  private async handleInboundMessage(msg: ChannelMessage): Promise<void> {
    console.log(`[channels] Inbound ${msg.channelType} message from ${msg.senderId}: ${msg.content.substring(0, 50)}`);

    try {
      const text = msg.content.trim();
      const chatKey = `${msg.channelType}-${msg.sessionId || msg.senderId}`;

      if (text.startsWith("/")) {
        const response = await this.handleCommand(chatKey, text, msg);
        if (response) {
          await this.sendToChannel(msg.channelType, msg.sessionId || msg.senderId, response);
          return;
        }
      }

      await this.routeToAgent(chatKey, msg);
    } catch (err) {
      console.error(`[channels] Error handling ${msg.channelType} message:`, err);
    }
  }

  private getOrCreateSession(chatKey: string): ChatSession {
    let session = this.chatSessions.get(chatKey);
    if (!session) {
      session = { currentAgentId: null, currentAgentName: null, conversations: new Map() };
      this.chatSessions.set(chatKey, session);
    }
    return session;
  }

  private async handleCommand(chatKey: string, text: string, msg: ChannelMessage): Promise<string | null> {
    const parts = text.split(/\s+/);
    const cmd = parts[0].toLowerCase();
    const arg = parts.slice(1).join(" ").trim();

    switch (cmd) {
      case "/help":
        return this.cmdHelp();
      case "/agents":
        return this.cmdAgents(chatKey);
      case "/agent":
        return this.cmdAgent(chatKey, arg);
      case "/all":
        return arg ? this.cmdAll(chatKey, arg, msg) : "Usage: /all <message>";
      case "/reset":
        return this.cmdReset(chatKey, arg);
      case "/status":
        return this.cmdStatus(chatKey, msg);
      default:
        return null; // Not a recognized command — pass through to agent
    }
  }

  private cmdHelp(): string {
    return `📋 Bond Commands

/help — Show this help message
/agents — List available agents
/agent <name> — Switch to an agent (no arg = show current)
/all <message> — Send message to all agents
/reset — Clear conversation with current agent
/reset all — Clear all agent conversations
/reset <name> — Clear conversation with specific agent
/status — Show current agent and session info`;
  }

  private async cmdAgents(chatKey: string): Promise<string> {
    const agents = await this.backendClient.listAgents();
    if (!agents.length) return "No agents available.";

    const session = this.getOrCreateSession(chatKey);
    const currentId = session.currentAgentId;

    const lines = agents.map((a) => {
      const isCurrent = currentId ? a.id === currentId : a.is_default;
      const prefix = isCurrent ? "✅" : "  ";
      const suffix = a.is_default ? " (default)" : "";
      return `${prefix} ${a.display_name || a.name}${suffix}`;
    });

    return `🤖 Available Agents\n\n${lines.join("\n")}`;
  }

  private async cmdAgent(chatKey: string, name: string): Promise<string> {
    const session = this.getOrCreateSession(chatKey);

    if (!name) {
      const label = session.currentAgentName || "default agent";
      return `Currently talking to: ${label}`;
    }

    const agents = await this.backendClient.listAgents();
    const match = agents.find(
      (a) =>
        a.name.toLowerCase() === name.toLowerCase() ||
        a.display_name.toLowerCase() === name.toLowerCase(),
    );

    if (!match) {
      return `Agent "${name}" not found. Use /agents to see available agents.`;
    }

    session.currentAgentId = match.id;
    session.currentAgentName = match.display_name || match.name;
    const suffix = match.is_default ? " (default)" : "";
    return `Switched to: ${session.currentAgentName}${suffix}`;
  }

  private async cmdAll(chatKey: string, message: string, msg: ChannelMessage): Promise<string> {
    const agents = await this.backendClient.listAgents();
    if (!agents.length) return "No agents available.";

    const session = this.getOrCreateSession(chatKey);

    const results = await Promise.allSettled(
      agents.map(async (agent) => {
        const agentKey = agent.id;
        let conversationId = session.conversations.get(agentKey);
        if (!conversationId) {
          conversationId = ulid();
          session.conversations.set(agentKey, conversationId);
        }

        let fullResponse = "";
        for await (const event of this.backendClient.conversationTurnStream(
          conversationId,
          message,
          agent.id,
        )) {
          if (event.event === "chunk" && event.data.content) {
            fullResponse += event.data.content as string;
          }
        }
        return { agent, response: fullResponse || "(no response)" };
      }),
    );

    const lines: string[] = [];
    for (const result of results) {
      if (result.status === "fulfilled") {
        const { agent, response } = result.value;
        lines.push(`🤖 ${agent.display_name || agent.name}:\n${response}`);
      } else {
        lines.push(`❌ Error: ${result.reason}`);
      }
    }

    return lines.join("\n\n");
  }

  private async cmdReset(chatKey: string, arg: string): Promise<string> {
    const session = this.getOrCreateSession(chatKey);

    if (arg.toLowerCase() === "all") {
      const count = session.conversations.size;
      session.conversations.clear();
      return `🔄 Cleared all ${count} conversation(s). Next message starts fresh.`;
    }

    if (arg) {
      const agents = await this.backendClient.listAgents();
      const match = agents.find(
        (a) =>
          a.name.toLowerCase() === arg.toLowerCase() ||
          a.display_name.toLowerCase() === arg.toLowerCase(),
      );
      if (!match) return `Agent "${arg}" not found. Use /agents to see available agents.`;
      const deleted = session.conversations.delete(match.id);
      return deleted
        ? `🔄 Cleared conversation with ${match.display_name || match.name}. Next message starts fresh.`
        : `No active conversation with ${match.display_name || match.name}.`;
    }

    // Reset current agent
    const agentKey = session.currentAgentId || "__default__";
    const deleted = session.conversations.delete(agentKey);
    return deleted
      ? "🔄 Cleared current conversation. Next message starts fresh."
      : "No active conversation to clear.";
  }

  private cmdStatus(chatKey: string, msg: ChannelMessage): string {
    const session = this.getOrCreateSession(chatKey);
    const agentLabel = session.currentAgentName || "default agent";
    const convCount = session.conversations.size;

    return `📊 Status

Channel: ${msg.channelType}
Current agent: ${agentLabel}
Active conversations: ${convCount}
Session: ${chatKey}`;
  }

  private async routeToAgent(chatKey: string, msg: ChannelMessage): Promise<void> {
    const session = this.getOrCreateSession(chatKey);
    const agentId = session.currentAgentId;

    const agentKey = agentId || "__default__";
    let conversationId = session.conversations.get(agentKey);
    if (!conversationId) {
      conversationId = ulid();
      session.conversations.set(agentKey, conversationId);
    }

    let fullResponse = "";
    for await (const event of this.backendClient.conversationTurnStream(
      conversationId,
      msg.content,
      agentId || undefined,
      undefined, // planId
      msg.channelType,
    )) {
      if (event.event === "chunk" && event.data.content) {
        fullResponse += event.data.content as string;
      }
    }

    if (fullResponse) {
      await this.sendToChannel(msg.channelType, msg.sessionId || msg.senderId, fullResponse);
    }
  }

  private async sendToChannel(channelType: string, channelId: string, message: string): Promise<void> {
    if (channelType === "telegram" && this.telegram) {
      await this.telegram.send(channelId, message);
    } else if (channelType === "whatsapp" && this.whatsapp) {
      await this.whatsapp.send(channelId, message);
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
