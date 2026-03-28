# AppDeploy.ai Deployment

Best for: Quick demos, prototypes, simple static/frontend apps.

## Prerequisites

Register for an API key:

```bash
export APPDEPLOY_API_KEY=$(curl -s https://api.appdeploy.ai/register | jq -r '.api_key')
```

## Deploy

Build your app first, then deploy via JSON-RPC:

```bash
# Build (example for React/Vite)
npm run build

# Deploy
curl -X POST https://api.appdeploy.ai/rpc \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $APPDEPLOY_API_KEY" \
  -d '{"jsonrpc":"2.0","method":"deploy","params":{"template":"react-vite","source":"./dist"},"id":1}'
```

The response includes the live URL.

## Supported Templates

| Template | Use for |
|----------|---------|
| `html-static` | Plain HTML/CSS/JS sites |
| `react-vite` | React apps built with Vite |
| `nextjs-static` | Next.js with `output: 'export'` (static export only) |

## Limitations

- **Frontend only** — no server-side processing, databases, or background jobs
- Supports frontend + simple backend proxies, but not complex server-side apps
- File size limits may apply
- This is a third-party service — review their terms before deploying sensitive content

## Environment Variables

Pass environment variables at build time (before deploying), not at deploy time. AppDeploy.ai serves static files only.

## Custom Domain

Not supported — apps are served from an AppDeploy.ai subdomain.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| 401 Unauthorized | Check that `$APPDEPLOY_API_KEY` is set and valid |
| Deploy fails | Ensure `source` points to a directory with built static files (e.g., `./dist`) |
| Blank page | Check that the build output is correct — run `ls dist/` to verify files exist |
| Wrong template | Match the template to your framework — use `html-static` for plain HTML |
