# 031 — Channels: Telegram & WhatsApp with Auto Allow-Lists

**Status:** Draft  
**Created:** 2025-01-27  
**Estimate:** 2–3 days focused work

---

## Summary

Add Telegram and WhatsApp channels to Bond with brain-dead simple setup: paste a token (Telegram) or scan a QR code (WhatsApp), and you're done. The allow-list is automatically configured to the owner's identity — no manual ID entry, no config files, no YAML.

---

## Architecture Comparison: Bond vs OpenClaw

| Aspect | Bond (today) | OpenClaw (reference) |
|---|---|---|
| **Channel abstraction** | `ChannelAdapter` interface with `start()`, `stop()`, `send()` — clean but only WebChat implemented | Rich plugin system with per-channel onboarding wizards, config schemas (Zod), account management |
| **Allow-lists** | Not implemented | `allowlist-match.ts` — cached Set matching by id/name/username/wildcard with `DmPolicy` enforcement |
| **Onboarding** | None — architecture doc describes "paste a token, Bond validates, encrypts, done" | Full CLI wizards per channel: Telegram = paste bot token + auto-detect user ID; WhatsApp = QR code scan via Baileys |
| **Storage** | `agent_channels_table` in SpacetimeDB (id, agentId, channel, enabled, sandboxOverride) | YAML config files with per-account overrides |
| **Session routing** | Exists (sessions/manager.ts) | DM → main session (if allowlisted), group → isolated session (mention-gated) |

## What Bond Already Has

- ✅ `ChannelAdapter` interface (`gateway/src/channels/base.ts`) — `start()`, `stop()`, `send(channelId, message)`
- ✅ `ChannelMessage` type — `channelId`, `senderId`, `text`, `channel` discriminator
- ✅ `agent_channels_table` in SpacetimeDB — ready to store channel configs
- ✅ Architecture doc (004) specifies grammY for Telegram, Baileys for WhatsApp
- ✅ WebChat channel as a working reference implementation

---

## Design Principles

1. **One-click setup** — Minimize steps to absolute minimum. No config files.
2. **Owner-only by default** — Auto-detect the owner's identity and lock the allow-list to them. Security first.
3. **No public URL required** — Bond runs locally. Use polling (Telegram) and Baileys multi-device (WhatsApp), not webhooks.
4. **Encrypted secrets** — Tokens and credentials stored encrypted in SpacetimeDB.

---

## Phase 1: Channel Infrastructure

### 1.1 Allow-List Module

**File:** `gateway/src/channels/allowlist.ts`

Port OpenClaw's `allowlist-match.ts` pattern (simplified):

```typescript
export class AllowList {
  private ids: Set<string>;

  constructor(ids: string[]) {
    this.ids = new Set(ids.map(id => id.toLowerCase()));
  }

  isAllowed(senderId: string): boolean {
    if (this.ids.has('*')) return true; // wildcard for dev/testing
    return this.ids.has(senderId.toLowerCase());
  }

  add(senderId: string): void {
    this.ids.add(senderId.toLowerCase());
  }

  remove(senderId: string): void {
    this.ids.delete(senderId.toLowerCase());
  }

  toArray(): string[] {
    return Array.from(this.ids);
  }
}
```

### 1.2 Extend SpacetimeDB Schema

Add to `agent_channels_table` (or a new `channel_config_table`):

| Column | Type | Description |
|---|---|---|
| `config` | `string` (JSON) | Encrypted channel config (token, creds, etc.) |
| `owner_identity` | `string` | Auto-populated sender ID of the channel owner |
| `allow_list` | `string` (JSON array) | List of allowed sender IDs |

---

## Phase 2: Telegram Channel

**Library:** [grammY](https://grammy.dev/) — lightweight, TypeScript-native, excellent docs.

### Setup Flow (One-Click)

```
User clicks "Add Telegram" in Settings
  → UI shows single text field: "Paste your bot token from @BotFather"
  → User pastes token
  → Gateway calls bot.getMe() to validate
  → UI shows: "✅ Connected as @YourBotName. Now send /start to your bot."
  → User sends /start to the bot on Telegram
  → Bot receives chat.id → auto-adds to allow-list
  → Bot replies: "You're connected! Only you can talk to me."
  → Done.
```

### Implementation

**File:** `gateway/src/channels/telegram.ts`

```typescript
import { Bot } from 'grammy';
import { ChannelAdapter, ChannelMessage } from './base';
import { AllowList } from './allowlist';

export class TelegramChannel implements ChannelAdapter {
  private bot: Bot;
  private allowList: AllowList;
  private onMessage: (msg: ChannelMessage) => void;

  async start(): Promise<void> {
    // Register handlers
    this.bot.command('start', async (ctx) => {
      const senderId = String(ctx.from.id);
      this.allowList.add(senderId);
      await this.persistAllowList();
      await ctx.reply("You're connected! Only you can talk to me.");
    });

    this.bot.on('message:text', async (ctx) => {
      const senderId = String(ctx.from.id);
      if (!this.allowList.isAllowed(senderId)) {
        await ctx.reply("Sorry, you're not on the allow list.");
        return;
      }
      this.onMessage({
        channel: 'telegram',
        channelId: String(ctx.chat.id),
        senderId,
        text: ctx.message.text,
      });
    });

    // Long-polling (no public URL needed)
    await this.bot.start();
  }

  async stop(): Promise<void> {
    await this.bot.stop();
  }

  async send(channelId: string, message: string): Promise<void> {
    // Chunk at 4096 chars (Telegram limit)
    const chunks = chunkText(message, 4096);
    for (const chunk of chunks) {
      await this.bot.api.sendMessage(Number(channelId), chunk, {
        parse_mode: 'Markdown',
      });
    }
  }
}
```

### Key Details

- **Polling mode** — No webhook, no public URL, works behind NAT/firewall
- **Auto allow-list** — `/start` command auto-registers the sender
- **Message chunking** — Telegram has a 4096-char limit; split long responses
- **Markdown support** — grammY handles Telegram's MarkdownV2 formatting

---

## Phase 3: WhatsApp Channel

**Library:** [@whiskeysockets/baileys](https://github.com/WhiskeySockets/Baileys) — multi-device, no Business API, no Meta approval needed.

### Setup Flow (QR Code Scan)

```
User clicks "Add WhatsApp" in Settings
  → Gateway creates Baileys socket, generates QR code
  → UI displays live-updating QR code
  → User scans QR with WhatsApp on their phone
  → Baileys completes multi-device linking
  → The linked phone number is auto-set as the only allowed sender
  → UI shows: "✅ Connected as +1234567890"
  → Done.
```

### Implementation

**File:** `gateway/src/channels/whatsapp.ts`

```typescript
import makeWASocket, {
  useMultiFileAuthState,
  DisconnectReason,
} from '@whiskeysockets/baileys';
import { ChannelAdapter, ChannelMessage } from './base';
import { AllowList } from './allowlist';

export class WhatsAppChannel implements ChannelAdapter {
  private socket: ReturnType<typeof makeWASocket>;
  private allowList: AllowList;
  private onMessage: (msg: ChannelMessage) => void;
  private qrCallback?: (qr: string) => void;

  async start(): Promise<void> {
    const { state, saveCreds } = await useMultiFileAuthState('./whatsapp-auth');

    this.socket = makeWASocket({
      auth: state,
      printQRInTerminal: false,
    });

    // QR code for UI display
    this.socket.ev.on('connection.update', (update) => {
      if (update.qr && this.qrCallback) {
        this.qrCallback(update.qr);
      }
      if (update.connection === 'open') {
        // Auto allow-list: the linked phone number is the owner
        const ownerId = this.socket.user?.id;
        if (ownerId) {
          this.allowList.add(ownerId);
          this.persistAllowList();
        }
      }
      if (update.connection === 'close') {
        // Reconnect with exponential backoff
        this.reconnect();
      }
    });

    this.socket.ev.on('creds.update', saveCreds);

    // Inbound messages
    this.socket.ev.on('messages.upsert', ({ messages }) => {
      for (const msg of messages) {
        if (!msg.message || msg.key.fromMe) continue;
        const senderId = msg.key.remoteJid!;
        if (!this.allowList.isAllowed(senderId)) continue;

        const text =
          msg.message.conversation ||
          msg.message.extendedTextMessage?.text ||
          '';

        this.onMessage({
          channel: 'whatsapp',
          channelId: senderId,
          senderId,
          text,
        });
      }
    });
  }

  async stop(): Promise<void> {
    this.socket?.end(undefined);
  }

  async send(channelId: string, message: string): Promise<void> {
    await this.socket.sendMessage(channelId, { text: message });
  }
}
```

### QR Code API Endpoint

```typescript
// GET /api/channels/whatsapp/qr
// Returns Server-Sent Events stream with QR updates
router.get('/api/channels/whatsapp/qr', (req, res) => {
  res.setHeader('Content-Type', 'text/event-stream');
  whatsappChannel.onQR((qr) => {
    res.write(`data: ${JSON.stringify({ qr })}\n\n`);
  });
});
```

### Key Details

- **No Business API** — Uses multi-device web linking (same as WhatsApp Web)
- **No Meta approval** — Personal use, no application process
- **QR code via SSE** — Live-updating QR streamed to the Settings UI
- **Auto allow-list** — The linked phone number is the owner by definition
- **Reconnection** — Baileys connections are flaky; need exponential backoff with jitter
- **Auth persistence** — `useMultiFileAuthState` stores creds to disk; encrypt at rest

---

## Phase 4: Settings UI

Add a **Channels** tab in the Bond settings panel:

```
┌─────────────────────────────────────┐
│  Channels                           │
├─────────────────────────────────────┤
│  ✅ WebChat          Always on      │
│                                     │
│  ☐ Telegram         [Set Up →]     │
│     Paste bot token, send /start    │
│                                     │
│  ☐ WhatsApp         [Set Up →]     │
│     Scan QR code from your phone    │
│                                     │
│  ☐ Discord          Coming soon     │
│  ☐ Slack            Coming soon     │
└─────────────────────────────────────┘
```

Each channel card shows:
- **Status:** linked / not linked / connecting
- **Identity:** the allow-listed user (e.g., `@username` or `+1234567890`)
- **Actions:** Disconnect, Edit allow-list (advanced)

### Telegram Setup Wizard

```
┌─────────────────────────────────────┐
│  Set Up Telegram                    │
├─────────────────────────────────────┤
│                                     │
│  1. Open @BotFather on Telegram     │
│  2. Create a bot with /newbot       │
│  3. Paste the token below:          │
│                                     │
│  ┌─────────────────────────────┐    │
│  │ 123456:ABC-DEF...           │    │
│  └─────────────────────────────┘    │
│                                     │
│  [Validate & Connect]               │
│                                     │
│  Then send /start to your bot       │
│  to complete the connection.        │
│                                     │
└─────────────────────────────────────┘
```

### WhatsApp Setup Wizard

```
┌─────────────────────────────────────┐
│  Set Up WhatsApp                    │
├─────────────────────────────────────┤
│                                     │
│  Scan this QR code with WhatsApp:   │
│                                     │
│       ┌───────────────┐             │
│       │  ▄▄▄ ▀▄▀ ▄▄▄ │             │
│       │  █▄█ ▀▄▀ █▄█ │             │
│       │  ▀▀▀ ▄▀▄ ▀▀▀ │             │
│       └───────────────┘             │
│                                     │
│  Open WhatsApp → Settings →         │
│  Linked Devices → Link a Device     │
│                                     │
│  ⏳ Waiting for scan...             │
│                                     │
└─────────────────────────────────────┘
```

---

## Implementation Stories

| # | Story | Size | Dependencies |
|---|---|---|---|
| 1 | `allowlist.ts` — Core allow-list module | S | None |
| 2 | Extend SpacetimeDB schema — channel config + owner_identity + allow_list columns | S | None |
| 3 | `telegram.ts` — grammY adapter, polling mode, `/start` auto-allowlist | M | 1, 2 |
| 4 | `whatsapp.ts` — Baileys adapter, QR endpoint, auto-allowlist from linked number | M | 1, 2 |
| 5 | Settings UI — Channels tab, setup wizards, QR display component | M | 3, 4 |
| 6 | E2E tests — Mock adapters, verify allowlist enforcement, verify message routing | S | 3, 4 |

---

## Key Design Decisions

| Decision | Choice | Rationale |
|---|---|---|
| Allow-list default | Owner-only (auto-detected) | Security first — no accidental exposure to strangers |
| Telegram auth | Bot token + `/start` auto-detect | Truly one-click; no manual user ID lookup |
| WhatsApp auth | Baileys multi-device QR | No Business API, no Meta approval, scan and done |
| Secret storage | Encrypt tokens in SpacetimeDB | Bond already has settings infrastructure |
| Telegram transport | Long-polling (not webhooks) | Bond runs locally — can't assume public URL |
| WhatsApp transport | Baileys socket | Handles its own connection; no webhook needed |

---

## Security Considerations

- **Token encryption at rest** — Bot tokens and Baileys creds must be encrypted before storage
- **Allow-list enforcement** — Every inbound message MUST pass the allow-list check before reaching the agent
- **No wildcard in production** — The `*` wildcard is for dev/testing only; warn if enabled
- **Rate limiting** — Add per-sender rate limits to prevent abuse if allow-list is expanded
- **Credential rotation** — Provide a way to revoke and re-link channels

---

## Future Extensions

- **Discord** — discord.js, OAuth2 bot flow, similar allow-list pattern
- **Slack** — Bolt.js, workspace install, channel-based allow-list
- **Group chat support** — Mention-gated responses in Telegram/WhatsApp groups
- **Multi-user allow-lists** — UI for managing additional allowed users
- **Webhook mode** — Optional for users with public URLs (faster than polling)
