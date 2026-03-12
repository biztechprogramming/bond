# GitHub Webhooks Setup Guide

Bond's gateway can receive GitHub webhook events — push, pull requests, issues, check runs, and more — and route them to agents via the EventBus system. This guide walks through everything you need to get webhooks flowing end-to-end.

---

## Overview

- GitHub delivers webhook payloads via HTTPS POST to your gateway's `/webhooks/github` endpoint
- The gateway verifies each request using an HMAC signature and your `GITHUB_WEBHOOK_SECRET`
- Verified events are published to the in-memory EventBus (see `gateway/src/events/routes.ts`) where agents can subscribe to them
- The `WebhookRegistrar` (see `gateway/src/webhooks/registrar.ts`) auto-registers hooks on startup via the `gh` CLI — no manual GitHub configuration required

---

## Prerequisites

- Bond gateway running (`make dev` or `make gateway`)
- A public domain/URL pointing to your server (e.g., `bond.yourdomain.com`)
- An SSL certificate — GitHub requires HTTPS for webhook delivery
- `gh` CLI installed and authenticated (for auto-registration) — or you can register webhooks manually

---

## Step 1: Generate a Webhook Secret

The webhook secret authenticates payloads from GitHub. Generate one with:

```bash
make webhook-secret
```

Or manually:

```bash
openssl rand -hex 32
```

Copy the output — you'll need it in the next step and when configuring GitHub.

---

## Step 2: Configure Your Environment

Create or edit `gateway/.env` and add the following:

```env
GITHUB_WEBHOOK_SECRET=<your-secret-from-step-1>
GATEWAY_EXTERNAL_URL=https://bond.yourdomain.com
```

The interactive setup command does this for you and generates a fresh secret automatically:

```bash
make webhook-setup
```

It will prompt you for your external URL, write both variables to `gateway/.env`, and print next steps.

> **Note:** `gateway/.env` is gitignored — your secret stays local.

---

## Step 3: SSL & Reverse Proxy Setup

GitHub requires HTTPS. The recommended setup is nginx as a reverse proxy in front of the gateway, with a Let's Encrypt certificate.

### Install certbot

```bash
sudo apt install certbot python3-certbot-nginx
sudo mkdir -p /var/www/html/.well-known/acme-challenge
```

### nginx configuration

Create `/etc/nginx/sites-available/bond-webhooks`:

```nginx
server {
    listen 80;
    server_name bond.yourdomain.com;

    # Required for certbot domain verification
    location /.well-known/ {
        root /var/www/html;
    }

    # Redirect all other HTTP traffic to HTTPS
    location / {
        return 301 https://$host$request_uri;
    }
}

server {
    listen 443 ssl;
    server_name bond.yourdomain.com;

    # SSL certificates — certbot fills these in after you run it below
    # ssl_certificate     /etc/letsencrypt/live/bond.yourdomain.com/fullchain.pem;
    # ssl_certificate_key /etc/letsencrypt/live/bond.yourdomain.com/privkey.pem;

    # Only expose the webhook endpoint — block everything else
    location /webhooks/github {
        proxy_pass         http://localhost:18789;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }

    location / {
        return 403;
    }
}
```

Enable the site and obtain your certificate:

```bash
sudo ln -s /etc/nginx/sites-available/bond-webhooks /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# Obtain certificate (certbot auto-updates the nginx config with SSL paths)
sudo certbot certonly --webroot -w /var/www/html -d bond.yourdomain.com
sudo systemctl reload nginx
```

> **DNS first:** Make sure your domain's A record points to your server's public IP before running certbot, or it will fail with a DNS error.

---

## Step 4: Configure Repositories

Tell Bond which repos to register webhooks for.

### Option A — Explicit list (recommended)

Add a `webhooks` section to `bond.json` under the `gateway` key:

```json
{
  "gateway": {
    "port": 18789,
    "webhooks": {
      "repos": ["owner/repo1", "owner/repo2"],
      "autoDiscover": false
    }
  }
}
```

This is the most predictable option — only the repos you list get a webhook.

### Option B — Auto-discover

Set `autoDiscover: true` (this is the default when no `webhooks` section is present):

```json
{
  "gateway": {
    "webhooks": {
      "autoDiscover": true
    }
  }
}
```

On startup the `WebhookRegistrar` runs `gh repo list --limit 100` and registers webhooks for every repo it finds under your authenticated account. See `gateway/src/webhooks/registrar.ts` for the full logic.

> **Tip:** Auto-discover is convenient but registers up to 100 repos. If you have many repos and only care about a few, use Option A instead.

---

## Step 5: Start the Gateway

```bash
make dev
# or just the gateway:
make gateway
```

On startup, the `WebhookRegistrar` runs automatically and registers webhooks for all configured/discovered repos via the `gh` CLI. Watch the logs for `[registrar]` messages:

```
[registrar] Using 2 configured repo(s) from bond.json
[registrar] Webhook already registered for owner/repo1 (id=12345678)
[registrar] Created webhook for owner/repo2 → https://bond.yourdomain.com/webhooks/github
```

Registration is idempotent — repos that already have a matching webhook are skipped.

---

## Step 6: Manual GitHub Setup (Alternative)

If you prefer not to use auto-registration, or if the `gh` CLI isn't available, you can add the webhook directly in GitHub:

1. Go to your repository → **Settings** → **Webhooks** → **Add webhook**
2. Fill in the fields:
   - **Payload URL:** `https://bond.yourdomain.com/webhooks/github`
   - **Content type:** `application/json`
   - **Secret:** the value of `GITHUB_WEBHOOK_SECRET` from your `.env`
   - **Which events:** "Send me everything", or pick specific events (push, pull_request, issues, etc.)
3. Click **Add webhook**

GitHub will send a ping event immediately. Check your gateway logs for a `[webhook]` entry to confirm delivery.

---

## Step 7: Verify It Works

Push a commit to one of the configured repos, then check your gateway logs:

```
[webhook] Received push event for owner/repo1
[webhook] Published to EventBus: github.push
```

Or query the event history directly:

```bash
curl http://localhost:18789/api/v1/events/history
```

You can also run a quick connectivity check:

```bash
make webhook-test
```

This sends an unsigned POST to the webhook endpoint and checks the response code. A `400` or `401` means the endpoint is reachable (the request is rejected because it has no valid signature, which is expected). A `000` means the gateway isn't running.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| 502 Bad Gateway | Gateway isn't running. Start with `make gateway` |
| 401 Invalid signature | Secret mismatch — make sure `GITHUB_WEBHOOK_SECRET` in `.env` exactly matches what's in GitHub webhook settings |
| DNS SERVFAIL from certbot | Your domain's A record doesn't point to this server yet. Update DNS and wait for propagation |
| `gh` CLI not found | Install from https://cli.github.com/, then run `gh auth login`. Or use manual setup (Step 6) |
| No `[webhook]` log entries | Check that nginx is proxying to port 18789, not another port |
| Webhook shows "Recent Deliveries → failed" in GitHub | Check gateway logs for the error. Common cause: secret mismatch or gateway returned a non-2xx status |
| `make webhook-setup` has no effect | Make sure you're running it from the `bond/` directory (project root) |

---

## Quick Status Check

At any time, check your current webhook configuration:

```bash
make webhook-status
```

This shows your configured external URL, whether the secret is set, which repos are listed in `bond.json`, and whether the gateway is currently running.

---

## Event Subscription API

Once events are flowing, agents can subscribe to them via the EventBus API:

```bash
# Subscribe to all GitHub push events
curl -X POST http://localhost:18789/api/v1/events/subscribe \
  -H "Content-Type: application/json" \
  -d '{
    "filter": { "type": "github.push" },
    "agentId": "my-agent"
  }'
```

See design doc [`040-gateway-event-subscriptions.md`](../design/040-gateway-event-subscriptions.md) for the full subscription API, filter syntax, and delivery model.
