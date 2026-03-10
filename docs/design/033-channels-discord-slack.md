# 033 — Channels: Discord & Slack

**Status:** Draft  
**Created:** 2025-07-11  
**Estimate:** 3–4 days focused work  
**Depends on:** 031 (Channel Infrastructure, AllowList), 032 (Message Pipeline)

---

## Summary

Add Discord and Slack as channel adapters to Bond, following the same patterns established by Telegram and WhatsApp (design doc 031). Both channels integrate via bot tokens, use the existing `ChannelAdapter` interface, `AllowList` module, and `ChannelManager` lifecycle. Discord uses the `discord.js` library; Slack uses `@slack/bolt`.

---

## Architecture Comparison

| Aspect | Discord | Slack |
|---|---|---|
| **Library** | [discord.js](https://discord.js.org/) v14+ | [@slack/bolt](https://slack.dev/bolt-js/) v3+ |
| **Auth model** | Bot token from Discord Developer Portal | Bot token + App-level token (Socket Mode) |
| **Connection** | WebSocket Gateway (always-on, no public URL) | Socket Mode (WebSocket, no public URL) |
| **Message limit** | 2000 chars | 4000 chars (text blocks) |
| **Threading** | Threads, replies | Threads (thread_ts) |
| **Identity** | User ID (snowflake) | User ID (e.g. `U01ABCDEF`) |
| **Mentions/Triggers** | `@BotName` or DM | `@BotName` or DM |
| **Public URL required?** | ❌ No (Gateway WebSocket) | ❌ No (Socket Mode) |

---

## Design Principles

1. **No public URL** — Both channels use WebSocket-based connections (Discord Gateway, Slack Socket Mode). Bond runs locally.
2. **Owner-only by default** — Auto-detect the installer's identity and lock the allow-list.
3. **One-click setup** — Paste a bot token, Bond validates, done.
4. **Consistent with existing channels** — Same `ChannelAdapter` interface, same `AllowList`, same `ChannelManager` registration.

---

## What Bond Already Has

- ✅ `ChannelAdapter` interface (`gateway/src/channels/base.ts`) — `start()`, `stop()`, `channelType`
- ✅ `ChannelMessage` type — `channelType`, `senderId`, `content`, `sessionId`, `metadata`
- ✅ `AllowList` module (`gateway/src/channels/allowlist.ts`)
- ✅ `ChannelManager` (`gateway/src/channels/manager.ts`) — lifecycle, config persistence, multi-agent routing
- ✅ Message pipeline (`gateway/src/pipeline/`) — unified inbound message processing
- ✅ Working reference implementations: Telegram (grammY) and WhatsApp (Baileys)

---

## Phase 1: Discord Channel

### Setup Flow

```
User clicks "Add Discord" in Settings
  → UI shows instructions: "Create a bot at discord.com/developers, copy the bot token"
  → User pastes bot token
  → Gateway calls client.login(token) to validate
  → UI shows: "✅ Connected as BotName#1234. Invite the bot to your server."
  → UI provides OAuth2 invite URL with minimal permissions (Send Messages, Read Messages)
  → User invites bot to their Discord server
  → User sends a DM or @mentions the bot
  → Bot auto-adds sender to allow-list
  → Done.
```

### Implementation

**File:** `gateway/src/channels/discord.ts`

```typescript
import { Client, GatewayIntentBits, Events, Message } from 'discord.js';
import type { ChannelAdapter, ChannelMessage } from './base.js';
import { AllowList } from './allowlist.js';

export class DiscordChannel implements ChannelAdapter {
  readonly channelType = 'discord';
  private client: Client;
  private allowList: AllowList;
  private running = false;
  private onMessage: (msg: ChannelMessage) => void;
  private token: string;

  constructor(
    token: string,
    allowList: AllowList,
    onMessage: (msg: ChannelMessage) => void,
  ) {
    this.token = token;
    this.allowList = allowList;
    this.onMessage = onMessage;
    this.client = new Client({
      intents: [
        GatewayIntentBits.Guilds,
        GatewayIntentBits.GuildMessages,
        GatewayIntentBits.DirectMessages,
        GatewayIntentBits.MessageContent,
      ],
    });
  }

  async start(): Promise<void> {
    this.client.on(Events.MessageCreate, (message: Message) => {
      if (message.author.bot) return;

      const senderId = message.author.id;
      const isDM = !message.guild;
      const isMentioned = message.mentions.has(this.client.user!);

      // Only respond to DMs or @mentions
      if (!isDM && !isMentioned) return;

      if (!this.allowList.isAllowed(senderId)) {
        message.reply("Sorry, you're not on the allow list.");
        return;
      }

      // Strip the bot mention from the message content
      let content = message.content;
      if (isMentioned) {
        content = content.replace(/<@!?\d+>/g, '').trim();
      }

      this.onMessage({
        channelType: 'discord',
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

  async send(channelId: string, message: string): Promise<void> {
    const channel = await this.client.channels.fetch(channelId);
    if (!channel?.isTextBased()) return;

    // Chunk at 2000 chars (Discord limit)
    const chunks = chunkText(message, 2000);
    for (const chunk of chunks) {
      await (channel as any).send(chunk);
    }
  }

  isRunning(): boolean {
    return this.running;
  }

  getUser() {
    const user = this.client.user;
    return user ? { id: user.id, name: user.username } : undefined;
  }

  getInviteUrl(): string | undefined {
    const clientId = this.client.user?.id;
    if (!clientId) return undefined;
    // Minimal permissions: Send Messages (2048) + Read Messages (1024) + Read Message History (65536)
    return `https://discord.com/oauth2/authorize?client_id=${clientId}&permissions=68608&scope=bot`;
  }
}

function chunkText(text: string, maxLen: number): string[] {
  if (text.length <= maxLen) return [text];
  const chunks: string[] = [];
  for (let i = 0; i < text.length; i += maxLen) {
    chunks.push(text.slice(i, i + maxLen));
  }
  return chunks;
}
```

### Key Details

- **WebSocket Gateway** — No webhook, no public URL, works behind NAT/firewall
- **Intents** — Requires `MessageContent` privileged intent (must be enabled in Developer Portal)
- **DM + @mention** — Responds to DMs and @mentions in servers; ignores other messages
- **Message chunking** — Discord has a 2000-char limit
- **OAuth2 invite URL** — Auto-generated with minimal permissions

### Discord-Specific Considerations

- **Rate limits** — discord.js handles rate limiting automatically
- **Reconnection** — discord.js has built-in reconnection with exponential backoff
- **Privileged intents** — `MessageContent` intent must be toggled ON in the Developer Portal; the setup UI should mention this
- **Slash commands** — Future enhancement; not needed for MVP (DM + @mention is sufficient)

---

## Phase 2: Slack Channel

### Setup Flow

```
User clicks "Add Slack" in Settings
  → UI shows instructions:
    1. Create a Slack App at api.slack.com/apps
    2. Enable Socket Mode (generates App-Level Token)
    3. Add Bot Token Scopes: chat:write, app_mentions:read, im:history, im:read
    4. Install to workspace (generates Bot Token)
    5. Paste both tokens into Bond
  → Gateway initializes Bolt app in Socket Mode
  → UI shows: "✅ Connected to workspace: YourWorkspace"
  → User sends a DM or @mentions the bot
  → Bot auto-adds sender to allow-list
  → Done.
```

### Implementation

**File:** `gateway/src/channels/slack.ts`

```typescript
import { App } from '@slack/bolt';
import type { ChannelAdapter, ChannelMessage } from './base.js';
import { AllowList } from './allowlist.js';

export class SlackChannel implements ChannelAdapter {
  readonly channelType = 'slack';
  private app: App;
  private allowList: AllowList;
  private running = false;
  private onMessage: (msg: ChannelMessage) => void;

  constructor(
    botToken: string,
    appToken: string,
    allowList: AllowList,
    onMessage: (msg: ChannelMessage) => void,
  ) {
    this.allowList = allowList;
    this.onMessage = onMessage;

    this.app = new App({
      token: botToken,
      appToken,
      socketMode: true, // No public URL needed
    });
  }

  async start(): Promise<void> {
    // Handle @mentions in channels
    this.app.event('app_mention', async ({ event, say }) => {
      const senderId = event.user;
      if (!this.allowList.isAllowed(senderId)) {
        await say("Sorry, you're not on the allow list.");
        return;
      }

      // Strip the bot mention
      const content = event.text.replace(/<@[A-Z0-9]+>/g, '').trim();

      this.onMessage({
        channelType: 'slack',
        senderId,
        content,
        sessionId: event.channel,
        metadata: {
          channelId: event.channel,
          threadTs: event.thread_ts || event.ts,
          username: senderId,
        },
      });
    });

    // Handle DMs
    this.app.event('message', async ({ event, say }) => {
      // Only handle DMs (channel type 'im'), skip bot messages
      if ((event as any).channel_type !== 'im') return;
      if ((event as any).bot_id) return;
      if (event.subtype) return; // skip edits, deletes, etc.

      const senderId = (event as any).user;
      if (!senderId) return;

      if (!this.allowList.isAllowed(senderId)) {
        await say("Sorry, you're not on the allow list.");
        return;
      }

      this.onMessage({
        channelType: 'slack',
        senderId,
        content: (event as any).text || '',
        sessionId: (event as any).channel,
        metadata: {
          channelId: (event as any).channel,
          threadTs: (event as any).thread_ts || (event as any).ts,
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

  async send(channelId: string, message: string, threadTs?: string): Promise<void> {
    // Chunk at 4000 chars (Slack block limit)
    const chunks = chunkText(message, 4000);
    for (const chunk of chunks) {
      await this.app.client.chat.postMessage({
        channel: channelId,
        text: chunk,
        thread_ts: threadTs, // Reply in thread if applicable
      });
    }
  }

  isRunning(): boolean {
    return this.running;
  }
}

function chunkText(text: string, maxLen: number): string[] {
  if (text.length <= maxLen) return [text];
  const chunks: string[] = [];
  for (let i = 0; i < text.length; i += maxLen) {
    chunks.push(text.slice(i, i + maxLen));
  }
  return chunks;
}
```

### Key Details

- **Socket Mode** — WebSocket connection, no public URL, works behind NAT/firewall
- **Two tokens required** — Bot Token (for API calls) + App-Level Token (for Socket Mode)
- **Thread support** — Replies in the same thread using `thread_ts`
- **DM + @mention** — Listens for DMs and @mentions; ignores other channel messages

### Slack-Specific Considerations

- **Rate limits** — Bolt handles rate limiting automatically (Tier 1: 1 msg/sec)
- **Rich formatting** — Slack uses mrkdwn (similar to Markdown but with differences). Future enhancement: convert standard Markdown → mrkdwn
- **Block Kit** — Future enhancement: use Slack Block Kit for richer message formatting
- **Scopes** — Minimal required: `chat:write`, `app_mentions:read`, `im:history`, `im:read`, `im:write`

---

## Phase 3: ChannelManager Integration

### Changes to `gateway/src/channels/manager.ts`

Add Discord and Slack alongside existing Telegram/WhatsApp:

```typescript
import { DiscordChannel } from './discord.js';
import { SlackChannel } from './slack.js';

// In ChannelManager class:
private discord: DiscordChannel | null = null;
private slack: SlackChannel | null = null;

// listChannels() — add discord and slack entries
// startChannel() — handle 'discord' and 'slack' types
// stopChannel() — handle 'discord' and 'slack' types
```

### Config Schema Extension

```typescript
interface ChannelConfig {
  type: 'telegram' | 'whatsapp' | 'discord' | 'slack';
  enabled: boolean;
  token?: string;        // Bot token (Telegram, Discord, Slack)
  appToken?: string;     // Slack App-Level Token (Socket Mode)
  allowList: string[];
  botInfo?: { id: string; username: string };
}
```

### API Routes Extension

Add to `gateway/src/channels/routes.ts`:

| Method | Path | Description |
|---|---|---|
| `POST` | `/api/channels/discord/setup` | Validate Discord bot token, save config |
| `DELETE` | `/api/channels/discord` | Remove Discord channel |
| `POST` | `/api/channels/slack/setup` | Validate Slack tokens, save config |
| `DELETE` | `/api/channels/slack` | Remove Slack channel |

---

## Phase 4: Settings UI

### Discord Settings Panel

```
┌─────────────────────────────────────┐
│ Discord                    [Toggle] │
│                                     │
│ Bot Token: [••••••••••••••]  [Edit] │
│ Status: ✅ Connected as BondBot     │
│ Invite URL: [Copy Link]            │
│                                     │
│ Allow List: [@user1, @user2]        │
│ [+ Add User]                        │
└─────────────────────────────────────┘
```

### Slack Settings Panel

```
┌─────────────────────────────────────┐
│ Slack                      [Toggle] │
│                                     │
│ Bot Token: [••••••••••••••]  [Edit] │
│ App Token: [••••••••••••••]  [Edit] │
│ Status: ✅ Connected to MyWorkspace │
│                                     │
│ Allow List: [@user1, @user2]        │
│ [+ Add User]                        │
└─────────────────────────────────────┘
```

---

## Dependencies

| Package | Version | Size | Purpose |
|---|---|---|---|
| `discord.js` | ^14.x | ~1.5 MB | Discord Gateway WebSocket client |
| `@slack/bolt` | ^3.x | ~800 KB | Slack Socket Mode client |

---

## Multi-Agent Routing

Both channels inherit the existing multi-agent command routing from `ChannelManager`:

| Command | Description |
|---|---|
| `/agents` | List available agents |
| `/agent <name>` | Switch to a specific agent |
| `/all <message>` | Broadcast to all agents |
| `/new` | Start a new conversation |
| `/status` | Show current agent and session info |
| `/help` | Show available commands |

---

## Security Considerations

1. **Token storage** — Bot tokens stored encrypted in channel config (same as Telegram)
2. **Allow-list enforcement** — All inbound messages checked against AllowList before processing
3. **No public endpoints** — Both channels use WebSocket connections; no HTTP webhooks exposed
4. **Minimal permissions** — Request only the bot permissions/scopes actually needed
5. **Rate limiting** — Both libraries handle API rate limits automatically

---

## Rollout Plan

| Phase | Scope | Estimate |
|---|---|---|
| 1 | Discord adapter + tests | 1 day |
| 2 | Slack adapter + tests | 1 day |
| 3 | ChannelManager integration + API routes | 0.5 day |
| 4 | Settings UI panels | 0.5–1 day |

**Total: 3–4 days**

---

## Future Enhancements

- **Discord slash commands** — Register `/bond` slash command for cleaner UX
- **Slack Block Kit** — Rich message formatting with buttons, dropdowns
- **Slack Markdown conversion** — Auto-convert standard Markdown → Slack mrkdwn
- **Discord embeds** — Rich embeds for code blocks, tables, images
- **Group/channel isolation** — Separate sessions per Discord server or Slack workspace
- **Reaction-based feedback** — 👍/👎 reactions for response quality tracking
