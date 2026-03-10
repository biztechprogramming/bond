# Channel Setup Guide

Bond supports multiple messaging channels so you can talk to your agent from Telegram, WhatsApp, or the built-in web chat. This guide walks through setting up each one.

---

## Web Chat

Web chat is always on — no setup needed. Open the Bond frontend at `http://localhost:18788` and start talking.

---

## Telegram

### Prerequisites

- A Telegram account
- The Telegram app on your phone or desktop

### Step 1: Create a Bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Follow the prompts:
   - Choose a **display name** for your bot (e.g., "My Bond Agent")
   - Choose a **username** — must end in `bot` (e.g., `my_bond_agent_bot`)
4. BotFather will reply with a **bot token** that looks like:
   ```
   123456789:ABCdefGHIjklMNOpqrsTUVwxyz
   ```
5. Copy the token — you'll need it in the next step.

### Step 2: Connect in Bond Settings

1. Open Bond Settings → **Channels** tab
2. Find the **Telegram** card and click **Set Up**
3. Paste your bot token into the text field
4. Click **Validate & Connect**
5. Bond will verify the token and show your bot's name (e.g., "✅ Connected as @my_bond_agent_bot")

### Step 3: Register Yourself

1. Open Telegram and find your new bot by searching its username
2. Send `/start` to the bot
3. The bot will reply: **"You're connected! Only you can talk to me."**

That's it. Your Telegram chat ID is automatically added to the allow-list. Only you can message the bot — anyone else gets rejected.

### How It Works

- Bond uses **long-polling** (not webhooks), so no public URL or port forwarding is needed
- Messages longer than 4,096 characters are automatically chunked
- The bot supports Markdown formatting in responses
- Your bot token is stored encrypted in `data/channels.json`

### Troubleshooting

| Problem | Solution |
|---|---|
| "Invalid token" during setup | Double-check the token from BotFather. Make sure you copied the entire string. |
| Bot doesn't respond | Make sure you sent `/start` first. Check that the gateway is running (`http://localhost:18789/health`). |
| "Not on the allow list" | Send `/start` to the bot to register. |
| Bot was working, now it's not | Restart the channel from Settings → Channels → Telegram → Stop/Start. |

---

## WhatsApp

### Prerequisites

- A WhatsApp account on your phone
- WhatsApp must be updated to a recent version (multi-device support required)

### Step 1: Start the Connection

1. Open Bond Settings → **Channels** tab
2. Find the **WhatsApp** card and click **Set Up**
3. A QR code will appear in the setup wizard

### Step 2: Scan the QR Code

1. Open WhatsApp on your phone
2. Go to **Settings → Linked Devices → Link a Device**
3. Scan the QR code shown in Bond
4. Wait a few seconds — the wizard will update to show **"✅ Connected"** with your phone number

That's it. Your phone number is automatically added as the only allowed sender.

### How It Works

- Bond uses **Baileys** (multi-device web protocol) — the same protocol as WhatsApp Web
- No WhatsApp Business API or Meta approval needed
- No public URL required — the connection is outbound from your machine
- Auth credentials are stored in `data/whatsapp-auth/` and persist across restarts
- Your phone number is auto-detected and set as the sole allowed sender
- If the connection drops, Bond reconnects automatically with exponential backoff

### QR Code Expires?

QR codes refresh automatically. If the wizard shows a stale code, close and reopen the setup wizard to get a fresh stream.

### Troubleshooting

| Problem | Solution |
|---|---|
| QR code doesn't appear | Make sure the gateway is running. Check browser console for SSE connection errors. |
| QR code keeps refreshing but won't link | Update WhatsApp on your phone. Try unlinking other devices first (WhatsApp has a device limit). |
| Connected but messages don't go through | Check that the gateway and backend are both running. Look at gateway logs for errors. |
| "Logged out" after a while | WhatsApp may unlink devices that are inactive for 14+ days. Re-scan the QR code. |
| Connection drops repeatedly | This is a known Baileys behavior. Bond auto-reconnects with backoff. If it persists, try deleting `data/whatsapp-auth/` and re-linking. |

---

## Managing Channels

### Checking Status

Open Settings → **Channels** to see all channels and their current status:
- **Linked** — connected and receiving messages
- **Not linked** — not configured yet
- **Connecting** — in the process of establishing a connection

You can also check programmatically:
```bash
curl http://localhost:18789/api/v1/channels
```

### Stopping a Channel

Click **Disconnect** on any active channel card in Settings, or use the API:
```bash
# Stop Telegram
curl -X POST http://localhost:18789/api/v1/channels/telegram/stop

# Stop WhatsApp
curl -X POST http://localhost:18789/api/v1/channels/whatsapp/stop
```

### Removing a Channel

To fully disconnect and remove stored credentials:
```bash
curl -X DELETE http://localhost:18789/api/v1/channels/telegram
curl -X DELETE http://localhost:18789/api/v1/channels/whatsapp
```

### Channel API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/v1/channels` | GET | List all channels with status |
| `/api/v1/channels/telegram/setup` | POST | Validate bot token (`{ "token": "..." }`) |
| `/api/v1/channels/telegram/start` | POST | Start Telegram polling |
| `/api/v1/channels/telegram/stop` | POST | Stop Telegram polling |
| `/api/v1/channels/whatsapp/qr` | GET | SSE stream of QR codes for linking |
| `/api/v1/channels/whatsapp/start` | POST | Start WhatsApp connection |
| `/api/v1/channels/whatsapp/stop` | POST | Stop WhatsApp connection |
| `/api/v1/channels/:channel` | DELETE | Remove channel and credentials |

---

## Security

- **Owner-only by default** — Each channel's allow-list is locked to the person who set it up. No one else can message your bot.
- **Credentials encrypted** — Bot tokens and auth state are stored locally, never sent to external services.
- **No public exposure** — All channels connect outbound (Telegram long-polling, WhatsApp Baileys, Discord Gateway, Slack Socket Mode). No ports need to be opened.
- **Wildcard (`*`) is dev-only** — The allow-list supports `*` to allow all senders, but this is intended for local development/testing only.

---

## Discord

### Prerequisites

- A Discord account
- Access to the [Discord Developer Portal](https://discord.com/developers/applications)

### Step 1: Create a Bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application** → give it a name (e.g. "Bond Agent") → **Create**
3. Go to **Bot** in the left sidebar
4. **⚠️ Enable the `MESSAGE_CONTENT` privileged intent** under *Privileged Gateway Intents*. Without this, the bot receives empty message bodies.
5. Click **Reset Token** → copy the bot token.

### Step 2: Connect in Bond Settings

1. Open Bond Settings → **Channels** tab
2. Find the **Discord** card and click **Set Up**
3. Paste your bot token → click **Connect**
4. Bond validates the token and shows: "✅ Connected as BondBot"
5. Click the **Invite Link** to add the bot to your Discord server

### Step 3: Start Chatting

1. Send a DM to the bot in Discord
2. The first message auto-registers you as the owner (allow-list)
3. You can also @mention the bot in any server channel

### Troubleshooting

| Problem | Fix |
|---|---|
| Bot connects but messages are empty | Enable `MESSAGE_CONTENT` intent in Developer Portal → Bot → Privileged Gateway Intents |
| "Invalid token" error | Reset the token in Developer Portal and paste the new one |
| Bot doesn't respond to @mentions | Make sure the bot has been invited to the server with the invite link |
| "You're not on the allow list" | The first DM auto-adds you. If you @mentioned first, try a DM instead |

---

## Slack

### Prerequisites

- A Slack workspace where you have admin/install permissions
- Access to [api.slack.com/apps](https://api.slack.com/apps)

### Step 1: Create a Slack App (from Manifest)

The fastest way — paste this manifest to auto-configure everything:

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From a manifest**
2. Select your workspace
3. Switch to **YAML** and paste:

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

4. Click **Create**
5. Go to **Install App** → **Install to Workspace** → **Allow**
6. Copy the **Bot User OAuth Token** (`xoxb-...`)
7. Go to **Basic Information** → **App-Level Tokens** → **Generate Token** (scope: `connections:write`) → copy the token (`xapp-...`)

### Step 2: Connect in Bond Settings

1. Open Bond Settings → **Channels** tab
2. Find the **Slack** card and click **Set Up**
3. Paste the **Bot Token** (`xoxb-...`) and **App Token** (`xapp-...`) → click **Connect**
4. Bond validates and shows: "✅ Connected to workspace: YourWorkspace"

### Step 3: Start Chatting

1. Open a DM with the bot in Slack
2. The first message auto-registers you as the owner (allow-list)
3. You can also @mention the bot in any channel it's been invited to

### Troubleshooting

| Problem | Fix |
|---|---|
| "not_authed" or "invalid_auth" | Double-check both tokens. Bot Token starts with `xoxb-`, App Token starts with `xapp-` |
| Bot doesn't respond to DMs | Ensure `im:history` and `im:read` scopes are present. Reinstall the app after adding scopes |
| Bot doesn't respond to @mentions | Ensure `app_mentions:read` scope is present and the `app_mention` event is subscribed |
| "You're not on the allow list" | The first DM auto-adds you. If you @mentioned first, try a DM instead |
| Socket Mode connection fails | Ensure the App-Level Token has the `connections:write` scope |

---

## Coming Soon

- **Group chat support** — Mention-gated responses in Telegram/WhatsApp groups
- **Multi-user allow-lists** — UI for adding additional allowed users
