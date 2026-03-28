# Vercel Deployment

Best for: Static sites, React SPAs, Next.js, Nuxt, SvelteKit, Astro.

## Prerequisites

```bash
npm i -g vercel
vercel login
```

## Deploy

### Zero-config (recommended)

From the project root:

```bash
vercel --yes
```

Vercel auto-detects the framework and configures build settings. This creates a preview deployment.

### Production deploy

```bash
vercel --prod
```

### Git-connected (recommended for ongoing projects)

```bash
vercel link          # Connect local project to Vercel
vercel git connect   # Link to GitHub/GitLab/Bitbucket repo
```

After linking, every push to `main` triggers a production deploy automatically.

## Environment Variables

```bash
# Add a variable
vercel env add VARIABLE_NAME production

# Add from .env file
vercel env pull .env.local   # Download existing
vercel env add < .env        # Upload from file
```

Or set inline during deploy:

```bash
vercel --env KEY=value --prod
```

## Custom Domain

```bash
vercel domains add example.com
```

Vercel provides free SSL automatically.

## Configuration (vercel.json)

Only needed for non-standard setups:

```json
{
  "buildCommand": "npm run build",
  "outputDirectory": "dist",
  "framework": null,
  "rewrites": [
    { "source": "/(.*)", "destination": "/index.html" }
  ]
}
```

Common SPA rewrite above routes all paths to index.html for client-side routing.

## Framework-Specific Notes

### Next.js
- Zero config — Vercel is the creator of Next.js
- API routes, ISR, middleware all work out of the box
- `next.config.js` `output: 'standalone'` is NOT needed for Vercel

### React (Vite/CRA)
- Auto-detected, zero config
- Add SPA rewrite in `vercel.json` if using client-side routing

### Vue / Nuxt
- Nuxt 3 with SSR: auto-detected
- Vue SPA (Vite): auto-detected, add SPA rewrite for routing

### Astro
- Zero config — auto-detects `@astrojs/vercel` adapter

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Build fails | Check `vercel logs` — usually missing env vars or wrong Node version |
| 404 on refresh (SPA) | Add SPA rewrite to `vercel.json` |
| Wrong Node version | Add `"engines": { "node": "20.x" }` to `package.json` |
| API routes not working | Ensure they're in `api/` directory (or `app/api/` for Next.js App Router) |
