/**
 * Discord channel adapter using discord.js (WebSocket Gateway, no webhooks).
 */
import { Client, GatewayIntentBits, Events, Message } from "discord.js";
import type { ChannelAdapter, ChannelMessage } from "./base.js";
import { AllowList } from "./allowlist.js";

function chunkText(text: string, maxLen: number): string[] {
  const chunks: string[] = [];
  for (let i = 0; i < text.length; i += maxLen) {
    chunks.push(text.slice(i, i + maxLen));
  }
  return chunks;
}

export interface DiscordChannelOptions {
  token: string;
  allowList: AllowList;
  onMessage: (msg: ChannelMessage) => void;
  persistFn?: (allowList: string[]) => void;
}

export class DiscordChannel implements ChannelAdapter {
  readonly channelType = "discord";
  private client: Client;
  private allowList: AllowList;
  private onMessageCb: (msg: ChannelMessage) => void;
  private persistFn?: (allowList: string[]) => void;
  private token: string;
  private running = false;

  constructor(options: DiscordChannelOptions) {
    this.token = options.token;
    this.allowList = options.allowList;
    this.onMessageCb = options.onMessage;
    this.persistFn = options.persistFn;
    this.client = new Client({
      intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildMessages,
        GatewayIntentBits.DirectMessages,
        GatewayIntentBits.MessageContent,
      ],
    });
  }

  /** Validate a bot token without starting the full adapter. */
  static async validateToken(token: string): Promise<{ id: string; username: string }> {
    const client = new Client({ intents: [] });
    try {
      await client.login(token);
      const user = client.user;
      if (!user) throw new Error("Login succeeded but no user returned");
      return { id: user.id, username: user.username };
    } finally {
      client.destroy();
    }
  }

  getAllowList(): AllowList {
    return this.allowList;
  }

  isRunning(): boolean {
    return this.running;
  }

  async start(): Promise<void> {
    if (this.running) return;

    this.client.on(Events.MessageCreate, (message: Message) => {
      if (message.author.bot) return;

      const senderId = message.author.id;
      const isDM = !message.guild;
      const isMentioned = message.mentions.has(this.client.user!);

      // Only respond to DMs or @mentions
      if (!isDM && !isMentioned) return;

      // Auto-add first user (owner bootstrap — like Telegram /start)
      if (this.allowList.isEmpty()) {
        this.allowList.add(senderId);
        this.persistAllowList();
      }

      if (!this.allowList.isAllowed(senderId)) {
        message.reply("Sorry, you're not on the allow list.");
        return;
      }

      // Strip the bot mention from content
      let content = message.content;
      if (isMentioned) {
        content = content.replace(/<@!?\d+>/g, "").trim();
      }

      this.onMessageCb({
        channelType: "discord",
        senderId,
        content,
        sessionId: message.channel.id,
        metadata: {
          guildId: message.guild?.id,
          channelId: message.channel.id,
          isDM,
          username: message.author.username,
        },
      });
    });

    await this.client.login(this.token);
    this.running = true;
    console.log("[discord] Bot started");
  }

  async stop(): Promise<void> {
    if (!this.running) return;
    this.client.destroy();
    this.running = false;
    console.log("[discord] Bot stopped");
  }

  async send(channelId: string, text: string): Promise<void> {
    const channel = await this.client.channels.fetch(channelId);
    if (!channel?.isTextBased()) return;
    for (const chunk of chunkText(text, 2000)) {
      await (channel as any).send(chunk);
    }
  }

  getInviteUrl(): string | undefined {
    const id = this.client.user?.id;
    if (!id) return undefined;
    // Send Messages (2048) + Read Messages (1024) + Read Message History (65536)
    return `https://discord.com/oauth2/authorize?client_id=${id}&permissions=68608&scope=bot`;
  }

  private persistAllowList(): void {
    if (this.persistFn) {
      this.persistFn(this.allowList.toArray());
    }
  }
}
