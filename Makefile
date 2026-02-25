.PHONY: dev backend gateway frontend setup install test lint clean

# Start all services for development
dev:
	@echo "Starting Bond development servers..."
	@$(MAKE) -j3 backend gateway frontend

# Backend (FastAPI)
backend:
	cd /home/andrew/bond && uv run uvicorn backend.app.main:app --host 127.0.0.1 --port 18790 --reload

# Gateway (TypeScript WebSocket server)
gateway:
	cd /home/andrew/bond/gateway && pnpm dev

# Frontend (Next.js)
frontend:
	cd /home/andrew/bond/frontend && pnpm dev

# First-run setup wizard
setup:
	uv run bond setup

# Install all dependencies
install:
	uv sync
	cd /home/andrew/bond/gateway && pnpm install
	cd /home/andrew/bond/frontend && pnpm install

# Run tests
test:
	uv run pytest

# Lint
lint:
	uv run ruff check backend/
	cd /home/andrew/bond/gateway && pnpm lint
	cd /home/andrew/bond/frontend && pnpm lint

# Run migrations (Docker)
migrate:
	docker compose run --rm migrate

# Run migrations (local, requires migrate CLI with SQLite support)
migrate-local:
	@./scripts/migrate.sh

# Roll back last migration (Docker)
migrate-down:
	docker compose run --rm migrate -path=/migrations -database="sqlite3:///home/bond/.bond/data/knowledge.db" down 1

# Roll back last migration (local)
migrate-down-local:
	migrate -path migrations -database "sqlite3://$$HOME/.bond/data/knowledge.db" down 1

# Show current migration version
migrate-version:
	migrate -path migrations -database "sqlite3://$$HOME/.bond/data/knowledge.db" version

# Install golang-migrate with SQLite support (requires Go)
install-migrate:
	go install -tags 'sqlite3' github.com/golang-migrate/migrate/v4/cmd/migrate@latest

# Clean generated files
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf .ruff_cache
