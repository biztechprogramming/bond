# 033 — Channels: Discord & Slack

**Status:** Draft
**Created:** 2025-07-11
**Estimate:** 3–4 days
**Depends on:** 031 (Channel Infrastructure, AllowList), 032 (Message Pipeline)

---

## Summary

Add Discord and Slack as channel adapters, following the patterns established by Telegram and WhatsApp (design doc 031). Both channels connect via WebSocket (no public URL required), use the existing `ChannelAdapter` interface, `AllowList` module, and `ChannelManager` lifecycle.

---

## Architecture Comparison

| Aspect | Discord | Slack |
|---|---|---|
| **Library** | [discord.js](https://discord.js.org/) v14+ | [@slack/bolt](https://slack.dev/bolt-js/) v3+ |
| **Auth model** | Bot token | Bot token + App-Level token (Socket Mode) |
| **Connection** | WebSocket Gateway (always-on) | Socket Mode (WebSocket) |
| **Message limit** | 2 000 chars | 4 000 chars (text blocks) |
| **Identity** | User ID (snowflake) | User ID (e.g. `U01ABCDEF`) |
| **Triggers** | DM or `@BotName` mention | DM or `@BotName` mention |
| **Public URL required?** | ❌ No | ❌ No |

---

## Design Principles

1. **No public URL** — Both channels use outbound WebSocket connections. Bond runs locally.
2. **Owner-only by default** — First DM auto-adds the sender to the allow-list (same as Telegram `/start`).
3. **One-click setup** — Paste token(s), Bond validates, done.
4. **Parity with Telegram** — Same options-object constructor, `validateToken()`, `persistFn`, `getAllowList()`.

---

## What Bond Already Has

- `ChannelAdapter` interface (`gateway/src/channels/base.ts`) — `start()`, `stop()`, `channelType`
- `ChannelMessage` type — `channelType`, `senderId`, `content`, `sessionId`, `metadata`
- `AllowList` module (`gateway/src/channels/allowlist.ts`)
- `ChannelManager` (`gateway/src/channels/manager.ts`) — lifecycle, config persistence, multi-agent routing
- Message pipeline (`gateway/src/pipeline/`) — unified inbound message processing
- Reference implementations: Telegram (grammY) and WhatsApp (Baileys)

---

## Discord Channel

### Setup Flow

```
User clicks "Add Discord" in Settings
  → UI shows setup steps (see Setup Guide below)
  → User pastes bot token
  → Gateway calls DiscordChannel.validateToken(token) → returns bot user info
  → UI shows: "✅ Connected as BondBot#1234"
  → UI provides OAuth2 invite URL with minimal permissions
  → User invites bot to their server
  → User sends a DM to the bot
  → Bot auto-adds sender to allow-list, calls persistFn
  → Done.
```

> **⚠️ Intent requirement:** The bot **must** have the `MESSAGE_CONTENT` privileged intent enabled in the [Discord Developer Portal → Bot → Privileged Gateway Intents](https://discord.com/developers/applications). Without this, the bot receives empty message bodies. The setup UI must call this out as **step 1** before the user copies the token.

### Implementation

**File:** `gateway/src/channels/discord.ts`

```typescript
import { Client, GatewayIntentBits, Events, Message } from "discord.js";
import type { ChannelAdapter, ChannelMessage } from "./base.js";
import { AllowList } from "./allowlist.js";

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
  static async validateToken(
    token: string,
  ): Promise<{ id: string; username: string }> {
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

  async start(): Promise<void> {
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
  }

  async stop(): Promise<void> {
    this.client.destroy();
    this.running = false;
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

function chunkText(text: string, maxLen: number): string[] {
  const chunks: string[] = [];
  for (let i = 0; i < text.length; i += maxLen) {
    chunks.push(text.slice(i, i + maxLen));
  }
  return chunks;
}
```

### Key Details

- **WebSocket Gateway** — No webhook, no public URL, works behind NAT/firewall.
- **Privileged intents** — `MessageContent` must be toggled ON in the Developer Portal.
- **DM + @mention** — Responds to DMs and @mentions; ignores other messages.
- **Auto-add first user** — First DM auto-adds the sender to the allow-list and persists it, matching Telegram's `/start` behaviour.
- **Message chunking** — 2 000-char limit.
- **Rate limits / reconnection** — discord.js handles both automatically.

---

## Slack Channel

### Setup Flow (with App Manifest)

Slack supports [App Manifests](https://api.slack.com/reference/manifests) that pre-configure all scopes and Socket Mode. This reduces manual clicking to near-zero.

```
User clicks "Add Slack" in Settings
  → UI shows:
    1. Go to api.slack.com/apps → "Create New App" → "From a manifest"
    2. Paste the manifest below
    3. Click "Install to Workspace"
    4. Copy the Bot Token (xoxb-...) and App-Level Token (xapp-...)
    5. Paste both tokens into Bond
  → Gateway calls SlackChannel.validateToken(botToken) → returns workspace info
  → UI shows: "✅ Connected to workspace: MyWorkspace"
  → User sends a DM to the bot
  → Bot auto-adds sender to allow-list, calls persistFn
  → Done.
```

### App Manifest

Users paste this when creating the Slack app to auto-configure everything:

```yaml
display_information:
  name: Bond Agent
  description: Personal AI assistant
features:
  bot_user:
    display_name: Bond
    always_online: true
oauth_config:
  scopes:
    bot:
      - chat:write
      - app_mentions:read
      - im:history
      - im:read
      - im:write
settings:
  event_subscriptions:
    bot_events:
      - app_mention
      - message.im
  socket_mode_enabled: true
```

### Implementation

**File:** `gateway/src/channels/slack.ts`

```typescript
import { App } from "@slack/bolt";
import type { ChannelAdapter, ChannelMessage } from "./base.js";
import { AllowList } from "./allowlist.js";

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
  static async validateToken(
    botToken: string,
  ): Promise<{ teamId: string; team: string; botId: string }> {
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

  async start(): Promise<void> {
    // Handle @mentions in channels
    this.app.event("app_mention", async ({ event, say }) => {
      const senderId = event.user;
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
      const senderId: string | undefined = ev.user;
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
  }

  async stop(): Promise<void> {
    await this.app.stop();
    this.running = false;
  }

  async send(
    channelId: string,
    text: string,
    threadTs?: string,
  ): Promise<void> {
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

function chunkText(text: string, maxLen: number): string[] {
  const chunks: string[] = [];
  for (let i = 0; i < text.length; i += maxLen) {
    chunks.push(text.slice(i, i + maxLen));
  }
  return chunks;
}
```

### Key Details

- **Socket Mode** — WebSocket connection, no public URL, works behind NAT/firewall.
- **Two tokens** — Bot Token (API calls) + App-Level Token (Socket Mode connection).
- **App Manifest** — Pre-configures all scopes and Socket Mode; eliminates manual scope clicking.
- **Auto-add first user** — First DM auto-adds the sender, matching Telegram `/start`.
- **Thread support** — Replies in-thread using `thread_ts`.
- **Rate limits** — Bolt handles rate limiting automatically.

---

## ChannelManager Integration

### New Methods

Following the existing `configureTelegram` / `configureWhatsApp` pattern in `manager.ts`:

```typescript
configureDiscord(token: string): void {
  this.configs.set("discord", { type: "discord", token, enabled: true, allowList: [] });
  this.persist();
}

configureSlack(botToken: string, appToken: string): void {
  this.configs.set("slack", { type: "slack", token: botToken, appToken, enabled: true, allowList: [] });
  this.persist();
}
```

### startChannel Extensions

Inside `startChannel(type)`, add cases for `"discord"` and `"slack"`:

```typescript
case "discord": {
  const adapter = new DiscordChannel({
    token: config.token!,
    allowList: new AllowList(config.allowList),
    onMessage: this.handleInbound,
    persistFn: (ids) => {
      config.allowList = ids;
      this.persist();
    },
  });
  await adapter.start();
  this.adapters.set("discord", adapter);
  break;
}

case "slack": {
  const adapter = new SlackChannel({
    botToken: config.token!,
    appToken: config.appToken!,
    allowList: new AllowList(config.allowList),
    onMessage: this.handleInbound,
    persistFn: (ids) => {
      config.allowList = ids;
      this.persist();
    },
  });
  await adapter.start();
  this.adapters.set("slack", adapter);
  break;
}
```

### Config Schema Extension

```typescript
interface ChannelConfig {
  type: "telegram" | "whatsapp" | "discord" | "slack";
  enabled: boolean;
  token?: string;       // Bot token (Telegram, Discord, Slack)
  appToken?: string;    // Slack App-Level Token (Socket Mode only)
  allowList: string[];
  botInfo?: { id: string; username: string };
}
```

---

## API Routes

Add to `gateway/src/channels/routes.ts`, following the existing pattern:

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/v1/channels/discord/setup` | Validate token via `DiscordChannel.validateToken()`, save config |
| `POST` | `/api/v1/channels/discord/start` | Start Discord adapter |
| `POST` | `/api/v1/channels/discord/stop` | Stop Discord adapter |
| `POST` | `/api/v1/channels/slack/setup` | Validate tokens via `SlackChannel.validateToken()`, save config |
| `POST` | `/api/v1/channels/slack/start` | Start Slack adapter |
| `POST` | `/api/v1/channels/slack/stop` | Stop Slack adapter |
| `DELETE` | `/api/v1/channels/:channel` | Remove channel (existing, works for all types) |

---

## Dependencies

| Package | Version | Size | Purpose |
|---|---|---|---|
| `discord.js` | ^14.x | ~1.5 MB | Discord Gateway WebSocket client |
| `@slack/bolt` | ^3.x | ~800 KB | Slack Socket Mode client |
| `@slack/web-api` | (peer of bolt) | — | Used by `validateToken()` |

---

## Security

- **Token storage** — Encrypted in channel config, same as Telegram.
- **Allow-list enforcement** — All inbound messages checked before processing.
- **No public endpoints** — Both channels use outbound WebSocket connections.
- **Minimal permissions** — Discord: Send/Read Messages only. Slack: 5 scopes via manifest.
- **Rate limiting** — Both libraries handle API rate limits automatically.

---

## Rollout Plan

| Phase | Scope | Estimate |
|---|---|---|
| 1 | Discord adapter + tests | 1 day |
| 2 | Slack adapter + tests | 1 day |
| 3 | ChannelManager integration + API routes | 0.5 day |
| 4 | Settings UI panels + setup guide | 0.5–1 day |

**Total: 3–4 days**

---

## Future Enhancements

- **Discord slash commands** — Register `/bond` command for cleaner UX
- **Discord embeds** — Rich formatting for code blocks, tables, images
- **Slack Block Kit** — Rich message formatting with buttons, dropdowns
- **Slack mrkdwn conversion** — Auto-convert standard Markdown → Slack mrkdwn
- **Reaction-based feedback** — 👍/👎 reactions for response quality tracking
- **Group/channel isolation** — Separate sessions per Discord server or Slack workspace
