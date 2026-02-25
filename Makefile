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

# Run database migrations
migrate:
	uv run python -m migrations.runner up

# Migration status
migrate-status:
	uv run python -m migrations.runner status

# Roll back last migration
migrate-down:
	uv run python -m migrations.runner down

# Clean generated files
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf .ruff_cache
