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
- **No public exposure** — Both Telegram (long-polling) and WhatsApp (Baileys) connect outbound. No ports need to be opened.
- **Wildcard (`*`) is dev-only** — The allow-list supports `*` to allow all senders, but this is intended for local development/testing only.

---

## Coming Soon

- **Discord** — Bot integration via discord.js
- **Slack** — Workspace app via Bolt.js
- **Group chat support** — Mention-gated responses in Telegram/WhatsApp groups
- **Multi-user allow-lists** — UI for adding additional allowed users
