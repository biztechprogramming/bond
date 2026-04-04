#!/bin/bash
set -euo pipefail

# generate-test-db.sh — Build a clean bond-test.db by running all migrations
# and inserting realistic seed data, then scrubbing secrets.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Check dependencies
if ! command -v sqlite3 &>/dev/null; then
    echo "ERROR: sqlite3 is required but not found." >&2
    exit 1
fi

TMPDIR="$(mktemp -d)"
DB="$TMPDIR/bond-test.db"
MIGRATIONS_DIR="$PROJECT_ROOT/migrations"
FIXTURE_DIR="$PROJECT_ROOT/data/test-fixtures"
SCRUB_SQL="$FIXTURE_DIR/scrub-secrets.sql"

trap 'rm -rf "$TMPDIR"' EXIT

echo "==> Creating database in $DB"

# Run all .up.sql migrations in order
echo "==> Running migrations..."
for migration in "$MIGRATIONS_DIR"/*.up.sql; do
    name="$(basename "$migration")"
    echo "    $name"
    # Some migrations may duplicate columns already added by earlier ones
    # (e.g., 000024 re-adds agent_id from 000023). Tolerate those errors.
    if ! sqlite3 "$DB" < "$migration" 2>/tmp/mig_err.txt; then
        if grep -q "duplicate column name" /tmp/mig_err.txt; then
            echo "      (skipped: duplicate column — already exists)"
        else
            cat /tmp/mig_err.txt >&2
            exit 1
        fi
    fi
done

echo "==> Inserting seed data..."
sqlite3 "$DB" <<'SEED'
-- Sample settings (embedding defaults come from migration 000026)
INSERT OR IGNORE INTO settings (key, value) VALUES
    ('llm.default_model', 'anthropic/claude-sonnet-4-20250514'),
    ('ui.theme', 'dark');

-- Sample conversations
INSERT INTO conversations (id, agent_id, channel, title, is_active, message_count)
VALUES
    ('conv_test_001', '01JBOND0000000000000DEFAULT', 'webchat', 'Hello World Chat', 1, 3),
    ('conv_test_002', '01JBOND0000000000000DEFAULT', 'webchat', 'Code Review Session', 0, 12);

-- Sample messages
INSERT INTO conversation_messages (id, conversation_id, role, content) VALUES
    ('msg_001', 'conv_test_001', 'user', 'Hello, Bond!'),
    ('msg_002', 'conv_test_001', 'assistant', 'Hello! How can I help you today?'),
    ('msg_003', 'conv_test_001', 'user', 'Just testing.');

-- Sample content chunks (knowledge)
INSERT INTO content_chunks (id, source_type, source_id, text, summary) VALUES
    ('chunk_test_001', 'file', 'README.md', 'Bond is a local AI assistant.', 'About Bond'),
    ('chunk_test_002', 'file', 'ARCHITECTURE.md', 'The backend uses FastAPI with SQLite.', 'Architecture overview');

-- Sample memories
INSERT INTO memories (id, type, content, summary, source_type, importance) VALUES
    ('mem_test_001', 'fact', 'User prefers dark mode.', 'Dark mode preference', 'user_explicit', 0.7),
    ('mem_test_002', 'instruction', 'Always use type hints in Python.', 'Coding style', 'user_explicit', 0.8);

-- Sample session summary
INSERT INTO session_summaries (id, session_key, summary, key_decisions, message_count) VALUES
    ('ss_test_001', 'session_001', 'Discussed project setup and configuration.', '["Chose SQLite for storage"]', 5);

-- Sample work plan with items
INSERT INTO work_plans (id, agent_id, conversation_id, title, status) VALUES
    ('wp_test_001', '01JBOND0000000000000DEFAULT', 'conv_test_001', 'Set up development environment', 'completed');

INSERT INTO work_items (id, plan_id, title, status, ordinal) VALUES
    ('wi_test_001', 'wp_test_001', 'Install dependencies', 'complete', 1),
    ('wi_test_002', 'wp_test_001', 'Configure database', 'complete', 2),
    ('wi_test_003', 'wp_test_001', 'Run initial tests', 'complete', 3);

-- Sample provider API key (will be scrubbed)
INSERT OR IGNORE INTO provider_api_keys (provider_id, encrypted_value) VALUES
    ('anthropic', 'FAKE_ENCRYPTED_KEY_FOR_TESTING');

-- Sample MCP server
INSERT INTO mcp_servers (id, name, command, args, env) VALUES
    ('mcp_test_001', 'test-server', '/usr/bin/test-mcp', '["--port", "3000"]', '{"API_KEY": "secret123"}');
SEED

echo "==> Applying scrub-secrets.sql..."
sqlite3 "$DB" < "$SCRUB_SQL"

echo "==> Copying to $FIXTURE_DIR/bond-test.db"
mkdir -p "$FIXTURE_DIR"
cp "$DB" "$FIXTURE_DIR/bond-test.db"

echo "==> Done. Tables in bond-test.db:"
sqlite3 "$FIXTURE_DIR/bond-test.db" ".tables"
