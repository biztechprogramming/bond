.PHONY: dev backend gateway frontend setup install test lint clean images \
	langfuse-up langfuse-down langfuse-logs langfuse-ps langfuse-restart langfuse-stop langfuse-start langfuse-health langfuse-reset

# Start all services for development
dev:
	@echo "Starting Bond development servers..."
	@# Kill stale processes on our ports
	@lsof -ti :18789 | xargs kill -9 2>/dev/null || true
	@lsof -ti :18790 | xargs kill -9 2>/dev/null || true
	@lsof -ti :18788 | xargs kill -9 2>/dev/null || true
	@sleep 1
	@$(MAKE) -j3 backend gateway frontend

# Backend (FastAPI)
backend:
	cd /home/andrew/bond && uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 18790 --reload

# Gateway (TypeScript WebSocket server)
gateway:
	cd /home/andrew/bond/gateway && \
	  SPACETIMEDB_TOKEN=$(grep 'spacetimedb_token' ~/.config/spacetime/cli.toml 2>/dev/null | cut -d'"' -f2) \
	  BOND_SPACETIMEDB_URL=http://localhost:18787 \
	  BOND_SPACETIMEDB_MODULE=bond-core-v2 \
	  pnpm dev

# Frontend (Next.js)
frontend:
	cd /home/andrew/bond/frontend && pnpm dev

# First-run setup wizard
setup:
	uv run bond setup

# Install all dependencies
install:
	@chmod +x ./scripts/setup-spacetimedb.sh
	@./scripts/setup-spacetimedb.sh
	uv sync
	cd /home/andrew/bond/gateway && $$(grep -oP '"package_manager":\s*"\K[^"]+' ~/.bond/config.json || echo "pnpm") install
	cd /home/andrew/bond/frontend && $$(grep -oP '"package_manager":\s*"\K[^"]+' ~/.bond/config.json || echo "pnpm") install

# Run tests
test:
	uv run pytest

# Lint
lint:
	uv run ruff check backend/
	cd /home/andrew/bond/gateway && pnpm lint
	cd /home/andrew/bond/frontend && pnpm lint

# Run migrations (Docker)
# Run migrations (tries local first, falls back to Docker)
migrate:
	@./scripts/migrate.sh

# Run migrations via Docker
migrate-docker:
	docker compose -f docker-compose.dev.yml run --rm migrate

# Roll back last migration (Docker)
migrate-down:
	~/go/bin/migrate -path migrations -database "sqlite3://$$HOME/.bond/data/knowledge.db" down 1

migrate-down-docker:
	docker compose -f docker-compose.dev.yml run --rm migrate -path=/migrations -database="sqlite3:///home/bond/.bond/data/knowledge.db" down 1

# Show current migration version
migrate-version:
	migrate -path migrations -database "sqlite3://$$HOME/.bond/data/knowledge.db" version

# Install golang-migrate with SQLite support (requires Go)
install-migrate:
	go install -tags 'sqlite3' github.com/golang-migrate/migrate/v4/cmd/migrate@latest

# Build all agent container images
images:
	@echo "Building agent container images..."
	docker build -f Dockerfile.agent -t bond-agent-worker:latest .
	docker build -f docker/Dockerfile.python -t bond-agent-python:latest .
	docker build -f docker/Dockerfile.dotnet -t bond-agent-dotnet:latest .
	docker build -f docker/Dockerfile.node -t bond-agent-node:latest .
	@echo "All agent images built."

# Clean generated files
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf .ruff_cache

# Run coding agent evaluation test (default agent)
coding-test:
	@./scripts/coding-test.sh --label coding-test

# Run coding test with a specific agent
coding-test-agent:
	@test -n "$(AGENT)" || (echo "Usage: make coding-test-agent AGENT=<agent-id>" && exit 1)
	@./scripts/coding-test.sh --label coding-test-$(AGENT) --agent $(AGENT)

# Revert changes from a previous coding test run
coding-test-revert:
	@if [ -f tests/coding-runs/.last-changed-files ]; then \
		echo "Reverting files from last coding test..."; \
		xargs git checkout HEAD -- < tests/coding-runs/.last-changed-files 2>/dev/null || true; \
		rm tests/coding-runs/.last-changed-files; \
		echo "Done."; \
	else \
		echo "No previous coding test to revert."; \
	fi

# Langfuse (LLM observability) Docker Compose commands
langfuse-up:
	docker compose -f docker-compose.langfuse.yml up -d
	@echo "Langfuse UI: http://localhost:18786"

langfuse-down:
	docker compose -f docker-compose.langfuse.yml down

langfuse-logs:
	docker compose -f docker-compose.langfuse.yml logs -f

langfuse-ps:
	docker compose -f docker-compose.langfuse.yml ps

langfuse-restart:
	docker compose -f docker-compose.langfuse.yml restart

langfuse-stop:
	docker compose -f docker-compose.langfuse.yml stop

langfuse-start:
	docker compose -f docker-compose.langfuse.yml start

langfuse-health:
	@curl -s http://localhost:18786/api/public/health 2>/dev/null && echo " Langfuse is healthy" || echo "Langfuse is not responding"

langfuse-reset: langfuse-down
	@echo "WARNING: This will delete all Langfuse data (traces, scores, etc.)!"
	@read -p "Are you sure? (y/N) " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		docker volume rm bond_langfuse-db-data 2>/dev/null || true; \
		echo "Data removed. Run 'make langfuse-up' to start fresh."; \
	else \
		echo "Reset cancelled."; \
	fi

# SpacetimeDB Docker Compose commands
spacetimedb-up:
	docker-compose -f docker-compose.spacetimedb.yml up -d

spacetimedb-down:
	docker-compose -f docker-compose.spacetimedb.yml down

spacetimedb-logs:
	docker-compose -f docker-compose.spacetimedb.yml logs -f

spacetimedb-ps:
	docker-compose -f docker-compose.spacetimedb.yml ps

spacetimedb-restart:
	docker-compose -f docker-compose.spacetimedb.yml restart

spacetimedb-stop:
	docker-compose -f docker-compose.spacetimedb.yml stop

spacetimedb-start:
	docker-compose -f docker-compose.spacetimedb.yml start

# Simple version (without network)
spacetimedb-simple-up:
	docker-compose -f docker-compose.spacetimedb-simple.yml up -d

spacetimedb-simple-down:
	docker-compose -f docker-compose.spacetimedb-simple.yml down

# Check SpacetimeDB health
spacetimedb-health:
	@curl -s http://localhost:18787/v1/health && echo "SpacetimeDB is healthy" || echo "SpacetimeDB is not responding"

# Reset SpacetimeDB completely (WARNING: deletes all data)
spacetimedb-reset: spacetimedb-down
	@echo "WARNING: This will delete all SpacetimeDB data!"
	@read -p "Are you sure? (y/N) " -n 1 -r; \
	echo; \
	if [[ $$REPLY =~ ^[Yy]$$ ]]; then \
		echo "Removing SpacetimeDB data..."; \
		rm -rf ~/.bond/spacetimedb/data; \
		mkdir -p ~/.bond/spacetimedb/data; \
		echo "Data removed. Run 'make spacetimedb-up' to start fresh."; \
	else \
		echo "Reset cancelled."; \
	fi
