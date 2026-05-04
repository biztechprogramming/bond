#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$SKILL_DIR/../.." && pwd)"
SCREENSHOTS_DIR="/tmp/screenshots"
PID_DIR="/tmp/visual-ui-test"

mkdir -p "$SCREENSHOTS_DIR" "$PID_DIR"

# --- Helper: check if a port is already listening ---
port_is_up() {
    curl -sf "http://localhost:$1" > /dev/null 2>&1
}

# --- SpacetimeDB configuration ---
# BOND_TEST_STDB_HOST: how this container reaches the host's test SpacetimeDB.
# Defaults to host.docker.internal (standard Docker), override for other setups.
STDB_HOST="${BOND_TEST_STDB_HOST:-host.docker.internal}"
STDB_PORT="${BOND_TEST_STDB_PORT:-18797}"
STDB_URL="http://${STDB_HOST}:${STDB_PORT}"

# --- Step 1: Health-check SpacetimeDB ---
echo "Checking SpacetimeDB at ${STDB_URL}..."
for i in $(seq 1 15); do
    if curl -sf "${STDB_URL}/v1/health" > /dev/null 2>&1; then
        echo "SpacetimeDB is reachable."
        break
    fi
    if [ "$i" -eq 15 ]; then
        echo "ERROR: Cannot reach SpacetimeDB at ${STDB_URL}" >&2
        echo "Ensure the host has run: skills/visual-ui-test/scripts/setup-test-spacetimedb.sh" >&2
        echo "And that BOND_TEST_STDB_HOST is set correctly." >&2
        exit 1
    fi
    sleep 2
done

# --- Common environment ---
export PYTHONPATH="$PROJECT_ROOT"
export BOND_HOME="/tmp/visual-ui-test/bond-home"
export BOND_SPACETIMEDB_URL="$STDB_URL"
export BOND_SPACETIMEDB_MODULE="bond-core-v2"
mkdir -p "$BOND_HOME/data"

# --- Step 2: Start backend ---
if [ -f "$PID_DIR/backend.pid" ] && kill -0 "$(cat "$PID_DIR/backend.pid")" 2>/dev/null; then
    echo "Backend already running (PID $(cat "$PID_DIR/backend.pid"))"
else
    echo "Starting backend..."
    cd "$PROJECT_ROOT"
    BOND_SPACETIMEDB_URL="$STDB_URL" \
    uv run uvicorn backend.app.main:app --host 0.0.0.0 --port 18790 \
        > "$PID_DIR/backend.log" 2>&1 &
    echo $! > "$PID_DIR/backend.pid"
fi

# --- Step 3: Start gateway ---
if [ -f "$PID_DIR/gateway.pid" ] && kill -0 "$(cat "$PID_DIR/gateway.pid")" 2>/dev/null; then
    echo "Gateway already running (PID $(cat "$PID_DIR/gateway.pid"))"
else
    echo "Starting gateway..."
    cd "$PROJECT_ROOT/gateway"
    [ -d node_modules ] || pnpm install --frozen-lockfile
    BOND_BACKEND_URL="http://localhost:18790" \
    BOND_SPACETIMEDB_URL="$STDB_URL" \
    BOND_SPACETIMEDB_MODULE="bond-core-v2" \
    NODE_OPTIONS='--experimental-global-webcrypto' \
    pnpm dev > "$PID_DIR/gateway.log" 2>&1 &
    echo $! > "$PID_DIR/gateway.pid"
fi

# --- Step 4: Start frontend ---
if [ -f "$PID_DIR/frontend.pid" ] && kill -0 "$(cat "$PID_DIR/frontend.pid")" 2>/dev/null; then
    echo "Frontend already running (PID $(cat "$PID_DIR/frontend.pid"))"
else
    echo "Starting frontend..."
    cd "$PROJECT_ROOT/frontend"
    [ -d node_modules ] || pnpm install --frozen-lockfile
    NEXT_PUBLIC_STDB_HOST="$STDB_HOST" \
    NEXT_PUBLIC_STDB_PORT="$STDB_PORT" \
    BOND_SPACETIMEDB_URL="$STDB_URL" \
    pnpm dev > "$PID_DIR/frontend.log" 2>&1 &
    echo $! > "$PID_DIR/frontend.pid"
fi

# --- Step 5: Wait for health ---
echo "Waiting for services to start..."
SERVICES=("18790:Backend" "18789:Gateway" "18788:Frontend")
for entry in "${SERVICES[@]}"; do
    port="${entry%%:*}"
    name="${entry##*:}"
    ready=false
    for i in $(seq 1 30); do
        if port_is_up "$port"; then
            echo "$name is ready on port $port"
            ready=true
            break
        fi
        sleep 2
    done
    if [ "$ready" = false ]; then
        echo "ERROR: $name failed to start on port $port after 60s. Check $PID_DIR/${name,,}.log" >&2
        exit 1
    fi
done

echo ""
echo "Dev environment is running."
echo "  Frontend:    http://localhost:18788"
echo "  Gateway:     http://localhost:18789"
echo "  Backend:     http://localhost:18790"
echo "  SpacetimeDB: ${STDB_URL} (host-managed)"

# --- Step 6: Verify Playwright connectivity ---
echo ""
echo "Verifying Playwright connectivity..."
python3 -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('http://localhost:18788', wait_until='domcontentloaded', timeout=15000)
    print(f'Playwright verified: {page.title()}')
    browser.close()
" && echo "Playwright can connect to frontend" || echo "WARNING: Playwright connectivity check failed (services may still be starting)"
