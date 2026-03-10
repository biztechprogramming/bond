/**
 * Telegram channel adapter using grammY (long-polling, no webhooks).
 */
import { Bot } from "grammy";
import type { ChannelAdapter, ChannelMessage } from "./base.js";
import { AllowList } from "./allowlist.js";

function chunkText(text: string, maxLen: number): string[] {
  const chunks: string[] = [];
  for (let i = 0; i < text.length; i += maxLen) {
    chunks.push(text.slice(i, i + maxLen));
  }
  return chunks;
}

export interface TelegramChannelOptions {
  token: string;
  allowList: AllowList;
  onMessage: (msg: ChannelMessage) => void;
  persistFn?: (allowList: string[]) => void;
}

export class TelegramChannel implements ChannelAdapter {
  readonly channelType = "telegram";
  private bot: Bot;
  private allowList: AllowList;
  private onMessageCb: (msg: ChannelMessage) => void;
  private persistFn?: (allowList: string[]) => void;
  private running = false;

  constructor(options: TelegramChannelOptions) {
    this.bot = new Bot(options.token);
    this.allowList = options.allowList;
    this.onMessageCb = options.onMessage;
    this.persistFn = options.persistFn;
  }

  async start(): Promise<void> {
    if (this.running) return;

    this.bot.command("start", async (ctx) => {
      const senderId = String(ctx.from?.id);
      if (!senderId || senderId === "undefined") return;
      this.allowList.add(senderId);
      this.persistAllowList();
      await ctx.reply("You're connected! Only you can talk to me.");
    });

    this.bot.on("message:text", async (ctx) => {
      const senderId = String(ctx.from?.id);
      if (!this.allowList.isAllowed(senderId)) {
        await ctx.reply("Sorry, you're not on the allow list.");
        return;
      }
      this.onMessageCb({
        channelType: "telegram",
        senderId,
        content: ctx.message.text,
        sessionId: String(ctx.chat.id),
        metadata: {
          chatId: ctx.chat.id,
          username: ctx.from?.username,
          firstName: ctx.from?.first_name,
        },
      });
    });

    this.running = true;
    // Start long-polling (non-blocking — grammY handles this internally)
    this.bot.start({
      onStart: () => {
        console.log("[telegram] Bot started polling");
      },
    });
  }

  async stop(): Promise<void> {
    if (!this.running) return;
    this.running = false;
    await this.bot.stop();
    console.log("[telegram] Bot stopped");
  }

  async send(channelId: string, message: string): Promise<void> {
    const chunks = chunkText(message, 4096);
    for (const chunk of chunks) {
      try {
        await this.bot.api.sendMessage(Number(channelId), chunk, {
          parse_mode: "Markdown",
        });
      } catch {
        // Fallback: send without Markdown if parsing fails
        await this.bot.api.sendMessage(Number(channelId), chunk);
      }
    }
  }

  isRunning(): boolean {
    return this.running;
  }

  getAllowList(): AllowList {
    return this.allowList;
  }

  /**
   * Validate a bot token by calling getMe(). Returns bot info on success.
   */
  static async validateToken(token: string): Promise<{ id: number; username: string; firstName: string }> {
    const bot = new Bot(token);
    const me = await bot.api.getMe();
    return { id: me.id, username: me.username || "", firstName: me.first_name };
  }

  private persistAllowList(): void {
    if (this.persistFn) {
      this.persistFn(this.allowList.toArray());
    }
  }
}
