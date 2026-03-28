# Render Deployment

Best for: Simple web services, static sites, free-tier hosting, git-connected deploys.

## Prerequisites

A Render account at https://render.com. Render is primarily dashboard-driven — connect your GitHub or GitLab repo in the web UI.

## Deploy

### Via Dashboard (primary method)

1. Go to https://dashboard.render.com/new
2. Select **Web Service** (for APIs) or **Static Site** (for SPAs/static)
3. Connect your GitHub/GitLab repo
4. Render auto-detects framework and configures build settings
5. Click **Deploy**

Every push to `main` triggers an auto-deploy.

### Via Blueprint (IaC)

Create a `render.yaml` in your repo root:

```yaml
services:
  - type: web
    name: my-api
    runtime: node
    buildCommand: npm install && npm run build
    startCommand: node dist/server.js
    envVars:
      - key: NODE_ENV
        value: production
      - key: DATABASE_URL
        fromDatabase:
          name: mydb
          property: connectionString

databases:
  - name: mydb
    plan: free
```

Then go to https://dashboard.render.com/blueprints and connect the repo.

## Environment Variables

Set via the dashboard under your service's **Environment** tab, or define them in `render.yaml` as shown above.

## Custom Domain

Configure in the dashboard under your service's **Settings** → **Custom Domains**. Add a CNAME record pointing to your Render URL.

Render provides free SSL automatically.

## Framework Support

- **Node.js**: Express, Fastify, Next.js, Nuxt, SvelteKit
- **Python**: FastAPI, Flask, Django
- **Go**: Any Go binary
- **Rust**: Any Rust binary
- **Docker**: Dockerfile-based deploys supported
- **Static sites**: HTML, React, Vue, Svelte, Astro — built and served from CDN

## Free Tier

- Static sites: free
- Web services: free (spins down after 15 min inactivity, cold starts ~30s)
- PostgreSQL: free for 90 days

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Build fails | Check build logs in dashboard — usually missing env vars or wrong runtime version |
| Cold start slow | Free tier spins down after inactivity. Upgrade to paid for always-on |
| 404 on refresh (SPA) | Set **Rewrite Rules** to `/* → /index.html` in static site settings |
| Port not detected | Ensure your app listens on `process.env.PORT` or `$PORT` (Render sets this) |
| Deploy not triggering | Check that auto-deploy is enabled and the correct branch is configured |
