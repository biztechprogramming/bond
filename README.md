# Bond — Local-First AI Assistant

Bond is a personal AI assistant that runs entirely on your machine. It connects to LLM providers of your choice (Anthropic, OpenAI, Ollama, etc.), manages conversations through a WebSocket gateway, and gives you a clean web UI — all without sending your data to third-party services.

## Architecture

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Frontend   │────▶│   Gateway    │────▶│   Backend    │────▶│ SpacetimeDB  │
│  (Next.js)   │     │  (WebSocket) │     │  (FastAPI)   │     │  (Database)  │
│  :18788      │     │  :18789      │     │  :18790      │     │  :18787      │
└──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
```

| Service | Tech | Port | Purpose |
|---------|------|------|---------|
| **Frontend** | Next.js 15 / React 19 | 18788 | Web UI for chat, settings, deployments |
| **Gateway** | TypeScript / Express / WS | 18789 | WebSocket server, channels, session management |
| **Backend** | Python / FastAPI | 18790 | LLM orchestration, tools, memory, vault |
| **SpacetimeDB** | SpacetimeDB v2 | 18787 | Real-time database for state & sync |

---

## Quick Start (5 minutes)

### Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| **Python** | 3.12+ | [python.org](https://www.python.org/downloads/) |
| **Node.js** | 22+ | [nodejs.org](https://nodejs.org/) |
| **Docker** | Any recent | [docker.com](https://docs.docker.com/get-docker/) |
| **uv** | Latest | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **pnpm** | Latest | `npm install -g pnpm` |

### Step 1 — Clone & Install

```bash
git clone <your-repo-url> bond
cd bond

# Install all dependencies (Python, Gateway, Frontend, SpacetimeDB)
make install
```

This will:
- Check for the SpacetimeDB CLI (offers to install it if missing)
- Optionally start a local SpacetimeDB instance via Docker
- Install Python dependencies via `uv sync`
- Install Gateway dependencies via `pnpm install`
- Install Frontend dependencies via `pnpm install`

### Step 2 — Run the Setup Wizard

```bash
make setup
```

The wizard walks you through:
1. **Pick your LLM provider** — Anthropic, OpenAI, Google, DeepSeek, Groq, Mistral, Ollama, OpenRouter, xAI, Azure, Bedrock, LM Studio, or any OpenAI-compatible API
2. **Choose a model** — sensible defaults are suggested (e.g. `claude-sonnet-4-20250514` for Anthropic)
3. **Enter your API key** — stored in an encrypted local vault (skipped for local providers like Ollama)

Configuration is saved to `bond.json` and `~/.bond/`.

### Step 3 — Start SpacetimeDB

```bash
# Start SpacetimeDB via Docker (recommended)
make spacetimedb-up
```

Verify it's running:
```bash
make spacetimedb-health
```

### Step 4 — Run Migrations

```bash
make migrate
```

This runs SQLite schema migrations and publishes the SpacetimeDB module. If you don't have `golang-migrate` installed:

```bash
make install-migrate   # Requires Go
```

### Step 5 — Launch Bond

```bash
make dev
```

This starts all three services concurrently:
- **Backend** → http://localhost:18790
- **Gateway** → ws://localhost:18789
- **Frontend** → http://localhost:18788

Open **http://localhost:18788** in your browser and start chatting.

---

## One-Liner (if you're feeling bold)

```bash
make install && make setup && make spacetimedb-up && make migrate && make dev
```

---

## Docker (Alternative)

Run everything in a single container:

```bash
docker compose up --build
```

This exposes the same three ports (18788, 18789, 18790) and manages data in a Docker volume.

For development with hot-reload:

```bash
docker compose -f docker-compose.dev.yml up
```

---

## Configuration

### `bond.json` (project root)

Primary configuration file — LLM provider, model, ports, SpacetimeDB connection:

```json
{
  "llm": {
    "provider": "anthropic",
    "model": "claude-sonnet-4-20250514"
  },
  "backend": { "host": "0.0.0.0", "port": 18790 },
  "gateway": { "host": "0.0.0.0", "port": 18789 },
  "frontend": { "port": 18788 },
  "spacetimedb": {
    "url": "http://localhost:18787",
    "module": "bond-core-v2"
  }
}
```

### `.env` (project root, gitignored)

Optional overrides and secrets. See `.env.example` for all options:

```bash
cp .env.example .env
```

Key variables:
- `SPACETIMEDB_TOKEN` — Auth token for SpacetimeDB (auto-configured by setup)
- `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` — API keys (prefer the vault via `make setup`)
- `BOND_VAULT_KEY` — Encryption key for the local vault (auto-generated if unset)

### `~/.bond/` (home directory)

Bond's local data directory:
```
~/.bond/
├── data/          # SQLite databases (knowledge.db)
├── logs/          # Application logs
├── cache/         # LLM response cache, repo maps
├── workspace/     # Agent working directory
├── spacetimedb/   # SpacetimeDB data (Docker volume)
└── config.json    # Package manager preference
```

---

## Supported LLM Providers

| Provider | API Key Env Var | Local? |
|----------|----------------|--------|
| Anthropic | `ANTHROPIC_API_KEY` | No |
| OpenAI | `OPENAI_API_KEY` | No |
| Google (Gemini) | `GOOGLE_API_KEY` | No |
| DeepSeek | `DEEPSEEK_API_KEY` | No |
| Groq | `GROQ_API_KEY` | No |
| Mistral AI | `MISTRAL_API_KEY` | No |
| xAI (Grok) | `XAI_API_KEY` | No |
| OpenRouter | `OPENROUTER_API_KEY` | No |
| AWS Bedrock | `BEDROCK_API_KEY` | No |
| Azure OpenAI | `AZURE_API_KEY` | No |
| **Ollama** | _(none needed)_ | **Yes** |
| **LM Studio** | _(none needed)_ | **Yes** |
| Other (OpenAI-compatible) | `OTHER_API_KEY` | Varies |

Switch providers anytime by re-running `make setup` or editing `bond.json`.

---

## Common Commands

| Command | What it does |
|---------|-------------|
| `make dev` | Start all services (backend + gateway + frontend) |
| `make setup` | Run the first-time setup wizard |
| `make install` | Install all dependencies |
| `make migrate` | Run database migrations |
| `make test` | Run all tests (Python + Gateway + Frontend) |
| `make lint` | Lint all code |
| `make clean` | Remove caches and generated files |
| `make images` | Build Docker images for agent containers |

### SpacetimeDB

| Command | What it does |
|---------|-------------|
| `make spacetimedb-up` | Start SpacetimeDB via Docker |
| `make spacetimedb-down` | Stop SpacetimeDB |
| `make spacetimedb-health` | Check if SpacetimeDB is running |
| `make spacetimedb-logs` | Tail SpacetimeDB logs |
| `make spacetimedb-reset` | ⚠️ Delete all data and start fresh |

### Observability (Optional)

| Command | What it does |
|---------|-------------|
| `make langfuse-up` | Start Langfuse (LLM tracing) at :18786 |
| `make langfuse-down` | Stop Langfuse |
| `make langfuse-health` | Check Langfuse status |

### GitHub Webhooks (Optional)

| Command | What it does |
|---------|-------------|
| `make webhook-setup` | Interactive webhook configuration |
| `make webhook-test` | Test webhook endpoint |
| `make webhook-status` | Show webhook configuration |

---

## Troubleshooting

### "Port already in use"

Bond kills stale processes on startup, but if you still get conflicts:

```bash
lsof -ti :18788 :18789 :18790 | xargs kill -9
```

### SpacetimeDB won't start

```bash
# Check if Docker is running
docker ps

# Try the simple compose (no network config)
make spacetimedb-simple-up

# Or start manually
docker run -d --name bond-spacetimedb -p 18787:3000 \
  -v ~/.bond/spacetimedb:/var/lib/spacetimedb \
  clockworklabs/spacetime:v2.0.2 start
```

### Migration errors

```bash
# Install golang-migrate with SQLite support
make install-migrate

# Check current migration version
make migrate-version

# Roll back one migration
make migrate-down
```

### "No API key" errors

Re-run the setup wizard:
```bash
make setup
```

Or set the key directly in `.env`:
```bash
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env
```

### Frontend won't build

```bash
cd frontend && pnpm install && pnpm build
```

---

## Project Structure

```
bond/
├── backend/              # Python FastAPI backend
│   ├── app/
│   │   ├── agent/        # LLM orchestration, tools, memory
│   │   ├── api/          # REST API routes
│   │   ├── core/         # Vault, database, config
│   │   ├── sandbox/      # Code execution sandboxing
│   │   └── main.py       # FastAPI app entrypoint
│   └── tests/
├── gateway/              # TypeScript WebSocket gateway
│   └── src/
├── frontend/             # Next.js web UI
│   └── src/
├── spacetimedb/          # SpacetimeDB module (TypeScript)
│   └── spacetimedb/
├── migrations/           # SQLite schema migrations
├── prompts/              # System prompt fragments
├── scripts/              # Setup & maintenance scripts
├── docs/                 # Architecture docs & guides
├── docker/               # Dockerfiles for agent containers
├── bond.json             # Primary configuration
├── Makefile              # All commands live here
└── pyproject.toml        # Python project config
```

---

## Development

### Running Individual Services

```bash
make backend    # Just the FastAPI server
make gateway    # Just the WebSocket gateway
make frontend   # Just the Next.js UI
```

### Running Tests

```bash
make test       # All tests
uv run pytest   # Python tests only
cd gateway && pnpm test    # Gateway tests only
cd frontend && pnpm test   # Frontend tests only
```

### Adding a New Migration

```bash
# Create migration files
~/go/bin/migrate create -ext sql -dir migrations -seq <name>

# Edit the .up.sql and .down.sql files, then:
make migrate
```

---

## License

Private — see repository for details.
