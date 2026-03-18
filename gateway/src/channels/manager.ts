/**
 * Channel lifecycle manager.
 * Manages channel configs, start/stop, and routes inbound messages to the backend.
 * Supports multi-agent command routing via /help, /agents, /agent, /all, /new, /status.
 */
import { readFileSync, writeFileSync, mkdirSync, existsSync } from "fs";
import { dirname } from "path";
import { ulid } from "ulid";
import { AllowList } from "./allowlist.js";
import { TelegramChannel } from "./telegram.js";
import { WhatsAppChannel } from "./whatsapp.js";
import { DiscordChannel } from "./discord.js";
import { SlackChannel } from "./slack.js";
import type { ChannelMessage } from "./base.js";
import type { BackendClient, AgentInfo } from "../backend/client.js";
import type { MessagePipeline, PipelineContext, PipelineMessage } from "../pipeline/index.js";

interface ChannelConfig {
  type: string;
  enabled: boolean;
  token?: string;
  appToken?: string;
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
  private discord: DiscordChannel | null = null;
  private slack: SlackChannel | null = null;
  private configPath: string;
  private backendClient: BackendClient;
  private qrSubscribers = new Set<(qr: string) => void>();
  private statusSubscribers = new Set<(status: string) => void>();
  private whatsappAuthDir: string;
  private chatSessions = new Map<string, ChatSession>();
  /** Maps conversationId → channel chat that's watching it (for cross-channel push) */
  private channelBindings = new Map<string, { channelType: string; channelId: string }>();
  private pipeline: MessagePipeline | null = null;

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

    const discordConfig = this.configs.get("discord");
    if (discordConfig) {
      channels.push({
        type: "discord",
        status: this.discord?.isRunning() ? "linked" : "not_linked",
        enabled: discordConfig.enabled,
      });
    } else {
      channels.push({ type: "discord", status: "not_linked", enabled: false });
    }

    const slackConfig = this.configs.get("slack");
    if (slackConfig) {
      channels.push({
        type: "slack",
        status: this.slack?.isRunning() ? "linked" : "not_linked",
        enabled: slackConfig.enabled,
      });
    } else {
      channels.push({ type: "slack", status: "not_linked", enabled: false });
    }

    return channels;
  }

  configureTelegram(token: string, botInfo?: { id: number; username: string; firstName: string }): void {
    const existing = this.configs.get("telegram");
    this.configs.set("telegram", {
      type: "telegram",
      enabled: true,
      token,
      allowList: existing?.allowList || [],
      botInfo: botInfo || existing?.botInfo,
    });
    this.saveConfigs();
  }

  configureDiscord(token: string, botInfo?: { id: string; username: string }): void {
    const existing = this.configs.get("discord");
    this.configs.set("discord", {
      type: "discord",
      enabled: true,
      token,
      allowList: existing?.allowList || [],
    });
    this.saveConfigs();
  }

  configureSlack(botToken: string, appToken: string): void {
    const existing = this.configs.get("slack");
    this.configs.set("slack", {
      type: "slack",
      enabled: true,
      token: botToken,
      appToken,
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
    } else if (type === "discord") {
      const config = this.configs.get("discord");
      if (!config?.token) throw new Error("Discord not configured — set up token first");

      if (this.discord?.isRunning()) return;

      const allowList = new AllowList(config.allowList);
      this.discord = new DiscordChannel({
        token: config.token,
        allowList,
        onMessage: (msg) => this.handleInboundMessage(msg),
        persistFn: (ids) => {
          config.allowList = ids;
          this.saveConfigs();
        },
      });
      await this.discord.start();
      config.enabled = true;
      this.saveConfigs();
    } else if (type === "slack") {
      const config = this.configs.get("slack");
      if (!config?.token) throw new Error("Slack not configured — set up tokens first");
      if (!config?.appToken) throw new Error("Slack not configured — app token missing");

      if (this.slack?.isRunning()) return;

      const allowList = new AllowList(config.allowList);
      this.slack = new SlackChannel({
        botToken: config.token,
        appToken: config.appToken,
        allowList,
        onMessage: (msg) => this.handleInboundMessage(msg),
        persistFn: (ids) => {
          config.allowList = ids;
          this.saveConfigs();
        },
      });
      await this.slack.start();
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
    } else if (type === "discord" && this.discord) {
      await this.discord.stop();
      this.discord = null;
      const config = this.configs.get("discord");
      if (config) { config.enabled = false; this.saveConfigs(); }
    } else if (type === "slack" && this.slack) {
      await this.slack.stop();
      this.slack = null;
      const config = this.configs.get("slack");
      if (config) { config.enabled = false; this.saveConfigs(); }
    }
  }

  async removeChannel(type: string): Promise<void> {
    await this.stopChannel(type);
    this.configs.delete(type);
    this.saveConfigs();

    // Clean up auth state on disk to prevent stale pre-key accumulation
    if (type === "whatsapp") {
      WhatsAppChannel.cleanAuthDir(this.whatsappAuthDir);
    }
  }

  /**
   * Force-disconnect WhatsApp: stop the channel, wipe auth state,
   * but keep the config entry so the user can re-link via QR.
   */
  async forceDisconnectWhatsApp(): Promise<void> {
    await this.stopChannel("whatsapp");
    WhatsAppChannel.cleanAuthDir(this.whatsappAuthDir);
    console.log("[channels] WhatsApp force-disconnected: auth state wiped");
  }

  isChannelRunning(type: string): boolean {
    if (type === "telegram") return this.telegram?.isRunning() ?? false;
    if (type === "whatsapp") return this.whatsapp?.isRunning() ?? false;
    if (type === "discord") return this.discord?.isRunning() ?? false;
    if (type === "slack") return this.slack?.isRunning() ?? false;
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

  /** Set the pipeline for message processing. */
  setPipeline(pipeline: MessagePipeline): void {
    this.pipeline = pipeline;
  }

  /** Get the AllowList for a running channel, or null. */
  getAllowListForChannel(channelType: string): AllowList | null {
    const config = this.configs.get(channelType);
    if (!config) return null;
    return new AllowList(config.allowList);
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
      case "/new":
        return this.cmdNew(chatKey, arg);
      case "/reset":
        return this.cmdNew(chatKey, arg);
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
/new — Start a new conversation with current agent
/new all — Start fresh with all agents
/new <name> — Start a new conversation with specific agent
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

  private async cmdNew(chatKey: string, arg: string): Promise<string> {
    const session = this.getOrCreateSession(chatKey);

    if (arg.toLowerCase() === "all") {
      const count = session.conversations.size;
      session.conversations.clear();
      return `✨ Starting fresh with all agents. Next message creates a new conversation.`;
    }

    if (arg) {
      const agents = await this.backendClient.listAgents();
      const match = agents.find(
        (a) =>
          a.name.toLowerCase() === arg.toLowerCase() ||
          a.display_name.toLowerCase() === arg.toLowerCase(),
      );
      if (!match) return `Agent "${arg}" not found. Use /agents to see available agents.`;
      session.conversations.delete(match.id);
      return `✨ Next message to ${match.display_name || match.name} starts a new conversation.`;
    }

    // New conversation for current agent
    const agentKey = session.currentAgentId || "__default__";
    session.conversations.delete(agentKey);
    return "✨ Next message starts a new conversation.";
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
      console.log(`[channels] New ${msg.channelType} conversation ${conversationId}`);
    }

    // Track which channel chats are watching which conversations
    this.channelBindings.set(conversationId, {
      channelType: msg.channelType,
      channelId: msg.sessionId || msg.senderId,
    });

    if (this.pipeline) {
      await this.routeViaPipeline(msg, conversationId, agentId);
    } else {
      await this.routeDirect(msg, conversationId, agentId);
    }
  }

  private async routeViaPipeline(msg: ChannelMessage, conversationId: string, agentId: string | null): Promise<void> {
    const pipelineMessage: PipelineMessage = {
      id: ulid(),
      channelType: msg.channelType,
      channelId: msg.sessionId || msg.senderId,
      content: msg.content,
      conversationId,
      agentId: agentId || undefined,
      timestamp: Date.now(),
      metadata: {
        agentId: agentId || undefined,
        conversationId,
      },
    };

    const context: PipelineContext = {
      aborted: false,
      respond: async (text: string) => {
        await this.sendToChannel(msg.channelType, msg.sessionId || msg.senderId, text);
      },
      broadcast: async (_text: string) => {},
      streamChunk: async (chunk: string) => {
        // For non-webchat channels, chunks are accumulated and sent as complete response
        // by the pipeline (response field on message). No streaming for Telegram/WhatsApp.
      },
      abort: async (reason: string) => {
        context.aborted = true;
        await this.sendToChannel(msg.channelType, msg.sessionId || msg.senderId, `Error: ${reason}`);
      },
      emit: async (_event: string, _data: Record<string, any>) => {
        // Non-webchat channels don't handle granular events
      },
    };

    try {
      await this.pipeline!.execute(pipelineMessage, context);
      if (pipelineMessage.response) {
        await this.sendToChannel(msg.channelType, msg.sessionId || msg.senderId, pipelineMessage.response);
      }
    } catch (err) {
      console.error(`[channels] Pipeline error for ${msg.channelType}:`, err);
      await this.sendToChannel(msg.channelType, msg.sessionId || msg.senderId, "An error occurred.");
    }
  }

  private async routeDirect(msg: ChannelMessage, conversationId: string, agentId: string | null): Promise<void> {
    let fullResponse = "";
    for await (const event of this.backendClient.conversationTurnStream(
      conversationId,
      msg.content,
      agentId || undefined,
      undefined,
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

  /**
   * Push a message to a channel if it's watching the given conversation.
   * Called by webchat when a user sends a message in a conversation that
   * originated from Telegram/WhatsApp (cross-channel).
   */
  async pushToChannel(conversationId: string, message: string, senderLabel?: string): Promise<void> {
    const binding = this.channelBindings.get(conversationId);
    if (!binding) return;

    const prefix = senderLabel ? `💬 ${senderLabel}:\n` : "";
    await this.sendToChannel(binding.channelType, binding.channelId, prefix + message);
  }

  /**
   * Check if a conversation has a channel binding (Telegram/WhatsApp watching it).
   */
  getChannelBinding(conversationId: string): { channelType: string; channelId: string } | null {
    return this.channelBindings.get(conversationId) || null;
  }

  private async sendToChannel(channelType: string, channelId: string, message: string): Promise<void> {
    if (channelType === "telegram" && this.telegram) {
      await this.telegram.send(channelId, message);
    } else if (channelType === "whatsapp" && this.whatsapp) {
      await this.whatsapp.send(channelId, message);
    } else if (channelType === "discord" && this.discord) {
      await this.discord.send(channelId, message);
    } else if (channelType === "slack" && this.slack) {
      await this.slack.send(channelId, message);
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
        // Don't auto-start WhatsApp with missing/corrupt credentials —
        // it would just spin in a reconnect loop generating pre-keys.
        if (type === "whatsapp" && !WhatsAppChannel.hasValidCreds(this.whatsappAuthDir)) {
          console.warn(`[channels] Skipping WhatsApp auto-start: no valid credentials. Re-link via QR.`);
          config.enabled = false;
          this.saveConfigs();
          continue;
        }

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
