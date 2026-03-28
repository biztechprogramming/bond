# SSH/rsync Deployment

Best for: Deploying to your own server (VPS, dedicated, on-prem).

## Prerequisites

- SSH access to the target server (`ssh user@server`)
- A web server installed on the target (nginx, caddy, or apache)
- rsync installed locally and on the server

## Deploy Static Sites

Build locally, then rsync the output:

```bash
npm run build
rsync -avz --delete ./dist/ user@server:/var/www/myapp/
```

## Deploy Node.js / Python Apps

Sync code (excluding deps and git), then install and restart on the server:

```bash
# Node.js
rsync -avz --exclude node_modules --exclude .git ./ user@server:/opt/myapp/
ssh user@server "cd /opt/myapp && npm install --production && pm2 restart myapp"

# Python
rsync -avz --exclude __pycache__ --exclude .venv --exclude .git ./ user@server:/opt/myapp/
ssh user@server "cd /opt/myapp && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && sudo systemctl restart myapp"
```

## Deploy with Docker

```bash
# Build and push to a registry
docker build -t registry.example.com/myapp:latest .
docker push registry.example.com/myapp:latest

# SSH to pull and restart
ssh user@server "docker pull registry.example.com/myapp:latest && docker compose up -d"
```

## Systemd Service Setup

Create `/etc/systemd/system/myapp.service` on the server:

```ini
[Unit]
Description=My App
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/myapp
ExecStart=/usr/bin/node dist/server.js
Restart=on-failure
Environment=NODE_ENV=production
Environment=PORT=3000

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable myapp
sudo systemctl start myapp
```

## Nginx Reverse Proxy

For Node.js/Python apps listening on a port, add to `/etc/nginx/sites-available/myapp`:

```nginx
server {
    listen 80;
    server_name example.com;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Enable it:

```bash
sudo ln -s /etc/nginx/sites-available/myapp /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## SSL with Let's Encrypt

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d example.com
```

Certbot auto-configures nginx for HTTPS and sets up auto-renewal.

## Environment Variables

Set in the systemd service file (`Environment=KEY=value`) or use an env file:

```ini
# In the [Service] section
EnvironmentFile=/opt/myapp/.env
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Permission denied (rsync) | Check SSH key auth and target directory permissions |
| App won't start | Check `journalctl -u myapp` for systemd logs |
| 502 Bad Gateway | App isn't running or wrong port in nginx config — check `ss -tlnp` |
| SSL cert failed | Ensure DNS A record points to server IP before running certbot |
| rsync slow | Add `-z` for compression, or use `--partial` for resumable transfers |
