# Railway Deployment

Best for: Backend APIs, full-stack apps, databases, and anything with a Dockerfile.

## Prerequisites

```bash
npm i -g @railway/cli
railway login
```

## Deploy

### New project

```bash
railway init
railway up
```

Railway uses Nixpacks to auto-detect your framework and build accordingly — zero config for most apps.

### Existing project

```bash
railway link
railway up
```

### Git-connected (recommended)

```bash
railway link
```

Then connect your GitHub repo in the Railway dashboard. Every push to `main` triggers a deploy.

## Environment Variables

```bash
# Set a variable
railway variables set KEY=value

# Set multiple
railway variables set KEY1=value1 KEY2=value2

# Open dashboard to manage variables
railway open
```

## Custom Domain

```bash
railway domain
```

This generates a `*.up.railway.app` subdomain. To add a custom domain, use the Railway dashboard under Settings → Domains.

Railway provides free SSL automatically.

## Database Provisioning

```bash
# Add a database service to your project
railway add
```

Select from Postgres, MySQL, Redis, or MongoDB. Railway automatically injects connection environment variables (e.g., `DATABASE_URL`).

## Procfile Support

For custom start commands, add a `Procfile`:

```
web: node dist/server.js
```

## Framework Support

Railway supports any language/framework that Nixpacks can build:
- **Node.js**: Express, Fastify, Hono, Koa, Next.js, Nuxt, SvelteKit
- **Python**: FastAPI, Flask, Django, Streamlit
- **Go**: Gin, Echo, Fiber
- **Rust**: Actix-web, Axum, Rocket
- **Docker**: Any Dockerfile

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Build fails | Check `railway logs` — usually missing env vars or unsupported runtime version |
| Port not detected | Set `PORT` env var or ensure your app listens on `process.env.PORT` / `$PORT` |
| Deploy stuck | Check `railway status` and `railway logs --tail` |
| Database connection refused | Ensure you're using the `DATABASE_URL` variable injected by Railway, not a hardcoded URL |
| Out of memory | Upgrade to a paid plan or optimize your app's memory usage |
