---
name: appdeploy
description: >
  Deploy web applications to hosting platforms. Use when the user asks to deploy an app,
  publish a site, ship to production, push to hosting, or make an app live. Triggers on
  phrases like "deploy this app", "deploy to vercel", "ship this", "make this live",
  "publish this site", "push to production", "host this app", "deploy my project".
  Supports static sites, frontend apps (React, Vue, Next.js, Svelte), backend APIs
  (Node, Python, Go, Rust), full-stack apps, and containerized services.
---

# AppDeploy

Deploy apps to the best-fit hosting platform with minimal configuration.

## Workflow

### 1. Detect the App

Run the detection script to analyze the project:

```bash
python3 skills/appdeploy/scripts/detect_app.py <project_path>
```

This outputs JSON with:
- `app_type` — static, frontend-spa, frontend-ssr, backend-api, fullstack, containerized
- `framework` — react, nextjs, vue, svelte, express, fastapi, flask, django, go, rust, etc.
- `has_dockerfile` — whether a Dockerfile exists
- `package_manager` — npm, yarn, pnpm, pip, poetry, cargo, go
- `build_command` — detected build command (if any)
- `output_dir` — detected build output directory (if any)
- `recommended_platforms` — ranked list of platforms, best fit first

### 2. Choose a Platform

Use the detection output to recommend a platform. If the user hasn't specified one, suggest the top recommendation and explain why. Let the user override.

**Platform selection priority** (simplest first):

| App Type | Best Fit | Also Works |
|----------|----------|------------|
| Static site (HTML/CSS/JS) | Vercel, AppDeploy.ai | Render, Netlify |
| React/Vue/Svelte SPA | Vercel | Render, Railway |
| Next.js / Nuxt (SSR) | Vercel | Railway, Fly.io |
| Node/Python/Go API | Railway | Fly.io, Render |
| Containerized (Dockerfile) | Railway, Fly.io | Render |
| Full-stack (frontend + API) | Railway | Vercel + Railway |
| Simple frontend demo | AppDeploy.ai | Vercel |

### 3. Deploy

Read the platform-specific guide and follow its steps:

- **Vercel**: See [references/vercel.md](references/vercel.md) — best for frontend and Next.js
- **Railway**: See [references/railway.md](references/railway.md) — best for backend and full-stack
- **Fly.io**: See [references/flyio.md](references/flyio.md) — best for containers and edge deployment
- **Render**: See [references/render.md](references/render.md) — best for simple web services
- **AppDeploy.ai**: See [references/appdeploy-ai.md](references/appdeploy-ai.md) — fastest for simple frontend demos
- **SSH/rsync**: See [references/ssh-deploy.md](references/ssh-deploy.md) — for deploying to user's own server

### 4. Verify

After deployment:
1. Hit the deployed URL and confirm it responds
2. Check for HTTPS
3. Report the live URL to the user
4. If the platform provides a dashboard URL, share that too

## Discovery Mode

When invoked with a discovery prompt (message starts with `[DEPLOYMENT DISCOVERY]`), analyze the repository and return a **structured JSON object** as your final response. The JSON must match this schema:

```json
{
  "framework": {
    "framework": "string — e.g. Next.js, Express, FastAPI",
    "version": "string | null",
    "runtime": "string — e.g. node, python, go",
    "confidence": 0.95,
    "evidence": ["list of files/signals that led to this conclusion"]
  },
  "build_strategy": {
    "strategy": "string — docker, docker-compose, npm, pip, cargo, etc.",
    "confidence": 0.9,
    "evidence": ["list of files/signals"],
    "dockerfile_path": "string | null",
    "compose_path": "string | null"
  },
  "services": [
    {
      "name": "string",
      "type": "database | cache | queue | search | storage | other",
      "source": "string — how detected",
      "confidence": 0.9
    }
  ],
  "env_vars": [
    {
      "name": "string",
      "required": true,
      "source": "string — file where found",
      "has_default": false
    }
  ],
  "ports": [
    {
      "port": 3000,
      "source": "string",
      "confidence": 0.9
    }
  ],
  "health_endpoint": {
    "path": "/health",
    "method": "GET",
    "source": "string",
    "confidence": 0.85
  },
  "app_port": 3000,
  "deployment_notes": "string — important deployment considerations"
}
```

The discovery prompt will include pre-gathered probe results. Use them to confirm or augment your analysis. Wrap the JSON in a markdown code block tagged `discovery-result`:

````
```discovery-result
{ ... }
```
````

## Important Rules

- **Always detect first.** Don't guess the app type — run the detection script.
- **Prefer zero-config platforms.** If Vercel or Railway can auto-detect the framework, use that over manual configuration.
- **Ask before spending money.** If a platform requires a paid plan, tell the user before proceeding.
- **Don't store secrets in code.** Use each platform's environment variable system for API keys, database URLs, etc.
- **Check for existing config.** If the project already has `vercel.json`, `fly.toml`, `railway.json`, `render.yaml`, or `Procfile`, use the platform that matches.
- **Git-based deploys are preferred.** If the project is a git repo with a remote, prefer connecting the repo to the platform over manual CLI deploys.
