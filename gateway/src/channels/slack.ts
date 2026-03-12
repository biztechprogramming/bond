/**
 * Slack channel adapter using @slack/bolt (Socket Mode, no public URL required).
 */
import { App } from "@slack/bolt";
import type { ChannelAdapter, ChannelMessage } from "./base.js";
import { AllowList } from "./allowlist.js";

function chunkText(text: string, maxLen: number): string[] {
  const chunks: string[] = [];
  for (let i = 0; i < text.length; i += maxLen) {
    chunks.push(text.slice(i, i + maxLen));
  }
  return chunks;
}

export interface SlackChannelOptions {
  botToken: string;
  appToken: string;
  allowList: AllowList;
  onMessage: (msg: ChannelMessage) => void;
  persistFn?: (allowList: string[]) => void;
}

export class SlackChannel implements ChannelAdapter {
  readonly channelType = "slack";
  private app: App;
  private allowList: AllowList;
  private onMessageCb: (msg: ChannelMessage) => void;
  private persistFn?: (allowList: string[]) => void;
  private running = false;

  constructor(options: SlackChannelOptions) {
    this.allowList = options.allowList;
    this.onMessageCb = options.onMessage;
    this.persistFn = options.persistFn;

    this.app = new App({
      token: options.botToken,
      appToken: options.appToken,
      socketMode: true,
    });
  }

  /** Validate a bot token without starting Socket Mode. */
  static async validateToken(botToken: string): Promise<{ teamId: string; team: string; botId: string }> {
    const { WebClient } = await import("@slack/web-api");
    const client = new WebClient(botToken);
    const res = await client.auth.test();
    if (!res.ok) throw new Error("Slack auth.test failed");
    return {
      teamId: res.team_id as string,
      team: res.team as string,
      botId: res.bot_id as string,
    };
  }

  getAllowList(): AllowList {
    return this.allowList;
  }

  isRunning(): boolean {
    return this.running;
  }

  async start(): Promise<void> {
    if (this.running) return;

    // Handle @mentions in channels
    this.app.event("app_mention", async ({ event, say }) => {
      const senderId: string = event.user ?? "";
      if (!senderId) return;
      if (!this.handleAutoAdd(senderId)) {
        await say("Sorry, you're not on the allow list.");
        return;
      }
      const content = event.text.replace(/<@[A-Z0-9]+>/g, "").trim();
      this.onMessageCb({
        channelType: "slack",
        senderId,
        content,
        sessionId: event.channel,
        metadata: {
          channelId: event.channel,
          threadTs: event.thread_ts || event.ts,
        },
      });
    });

    // Handle DMs
    this.app.event("message", async ({ event, say }) => {
      const ev = event as any;
      if (ev.channel_type !== "im") return;
      if (ev.bot_id || ev.subtype) return;
      const senderId: string = ev.user ?? "";
      if (!senderId) return;

      if (!this.handleAutoAdd(senderId)) {
        await say("Sorry, you're not on the allow list.");
        return;
      }
      this.onMessageCb({
        channelType: "slack",
        senderId,
        content: ev.text || "",
        sessionId: ev.channel,
        metadata: {
          channelId: ev.channel,
          threadTs: ev.thread_ts || ev.ts,
          isDM: true,
        },
      });
    });

    await this.app.start();
    this.running = true;
    console.log("[slack] App started");
  }

  async stop(): Promise<void> {
    if (!this.running) return;
    await this.app.stop();
    this.running = false;
    console.log("[slack] App stopped");
  }

  async send(channelId: string, text: string, threadTs?: string): Promise<void> {
    for (const chunk of chunkText(text, 4000)) {
      await this.app.client.chat.postMessage({
        channel: channelId,
        text: chunk,
        thread_ts: threadTs,
      });
    }
  }

  /**
   * Auto-add first user (owner bootstrap) and check allow-list.
   * Returns true if the sender is allowed.
   */
  private handleAutoAdd(senderId: string): boolean {
    if (this.allowList.isEmpty()) {
      this.allowList.add(senderId);
      this.persistAllowList();
    }
    return this.allowList.isAllowed(senderId);
  }

  private persistAllowList(): void {
    if (this.persistFn) {
      this.persistFn(this.allowList.toArray());
    }
  }
}
