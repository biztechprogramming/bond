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

# Clean generated files
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	rm -rf .ruff_cache

# Run coding agent evaluation test
coding-test:
	@./scripts/coding-test.sh --label coding-test

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
