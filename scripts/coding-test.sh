#!/usr/bin/env bash
# coding-test.sh — Bond agent coding evaluation harness
#
# Runs a coding task through BOND'S OWN AGENT (agent_turn), captures every
# step, tool call, diff, model metrics, and runs functional tests.
#
# Usage:
#   ./scripts/coding-test.sh [--task-file path] [--label run-name]
#
# Requires: Bond backend running (make backend) or starts it temporarily.
#
# Output:
#   tests/coding-runs/<label>-<timestamp>.log
#
set -euo pipefail

# ─── Defaults ───────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="test"
TASK_FILE=""
AGENT_ID=""
LOG_DIR="$REPO_ROOT/tests/coding-runs"
BOND_PORT="${BOND_PORT:-18790}"
BOND_URL="http://127.0.0.1:${BOND_PORT}"

# ─── Parse args ─────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --task-file) TASK_FILE="$2"; shift 2 ;;
    --agent)     AGENT_ID="$2"; shift 2 ;;
    --label)     LABEL="$2"; shift 2 ;;
    *)           echo "Unknown arg: $1"; exit 1 ;;
  esac
done

# Load task
if [[ -n "$TASK_FILE" && -f "$TASK_FILE" ]]; then
  TASK="$(cat "$TASK_FILE")"
else
  TASK_FILE="$REPO_ROOT/scripts/coding-tasks/default.md"
  if [[ -f "$TASK_FILE" ]]; then
    TASK="$(cat "$TASK_FILE")"
  else
    echo "ERROR: No task file found. Create scripts/coding-tasks/default.md or pass --task-file"
    exit 1
  fi
fi

# ─── Setup ──────────────────────────────────────────────────────────────
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
RUN_ID="${LABEL}-${TIMESTAMP}"
mkdir -p "$LOG_DIR"
LOGFILE="$LOG_DIR/${RUN_ID}.log"

START_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
START_BRANCH="$(git -C "$REPO_ROOT" branch --show-current)"

# ─── Logging helpers ────────────────────────────────────────────────────
log() {
  local ts
  ts="$(date '+%Y-%m-%d %H:%M:%S')"
  echo "[$ts] $*" | tee -a "$LOGFILE"
}

section() {
  {
    echo ""
    echo "════════════════════════════════════════════════════════════════"
    echo "  $1"
    echo "════════════════════════════════════════════════════════════════"
    echo ""
  } >> "$LOGFILE"
}

# ─── Check Bond is running ──────────────────────────────────────────────
STARTED_BOND=false
if ! curl -sf "$BOND_URL/api/v1/health" > /dev/null 2>&1; then
  log "Bond backend not running. Starting it..."
  cd "$REPO_ROOT"
  uv run uvicorn backend.app.main:app --host 127.0.0.1 --port "$BOND_PORT" &
  BOND_PID=$!
  STARTED_BOND=true
  # Wait for health
  for i in $(seq 1 30); do
    if curl -sf "$BOND_URL/api/v1/health" > /dev/null 2>&1; then
      log "Bond backend started (pid $BOND_PID)"
      break
    fi
    sleep 1
  done
  if ! curl -sf "$BOND_URL/api/v1/health" > /dev/null 2>&1; then
    log "ERROR: Bond backend failed to start"
    kill $BOND_PID 2>/dev/null || true
    exit 1
  fi
else
  log "Bond backend already running at $BOND_URL"
fi

cleanup() {
  if [[ "$STARTED_BOND" == "true" ]]; then
    log "Stopping Bond backend (pid $BOND_PID)..."
    kill $BOND_PID 2>/dev/null || true
    wait $BOND_PID 2>/dev/null || true
  fi
}
trap cleanup EXIT

# ─── Header ─────────────────────────────────────────────────────────────
section "CODING TEST RUN: $RUN_ID"
log "Run ID:       $RUN_ID"
log "Agent:        Bond (agent_turn) ${AGENT_ID:-default}"
log "Bond URL:     $BOND_URL"
log "Repo:         $REPO_ROOT"
log "Branch:       $START_BRANCH"
log "Start SHA:    $START_SHA"
log "Task file:    $TASK_FILE"
log "Task:"
echo "$TASK" >> "$LOGFILE"

# ─── Step 1: Pre-run snapshot ───────────────────────────────────────────
section "STEP 1: PRE-RUN STATE"
log "Git status before run:"
git -C "$REPO_ROOT" status --short >> "$LOGFILE" 2>&1 || echo "(clean)" >> "$LOGFILE"

log "Files that the task references (pre-run content):"
REFERENCED_FILES=$(echo "$TASK" | grep -oE '[a-zA-Z0-9_/]+\.(py|ts|js|md|yaml|yml|json|toml)' | sort -u)
for f in $REFERENCED_FILES; do
  if [[ -f "$REPO_ROOT/$f" ]]; then
    echo "" >> "$LOGFILE"
    echo "── $f ($(wc -l < "$REPO_ROOT/$f") lines) ──" >> "$LOGFILE"
    cat "$REPO_ROOT/$f" >> "$LOGFILE"
    echo "" >> "$LOGFILE"
  fi
done

# ─── Step 2: Run Bond agent ─────────────────────────────────────────────
section "STEP 2: BOND AGENT EXECUTION"
log "Sending task to Bond agent..."

AGENT_START="$(date +%s)"
AGENT_RESPONSE_FILE="$LOG_DIR/${RUN_ID}.response.json"

# Build JSON payload
if [[ -n "$AGENT_ID" ]]; then
  PAYLOAD=$(jq -n --arg msg "$TASK" --arg aid "$AGENT_ID" '{message: $msg, agent_id: $aid}')
else
  PAYLOAD=$(jq -n --arg msg "$TASK" '{message: $msg}')
fi

# Call Bond's agent turn API
set +e
HTTP_CODE=$(curl -sf -o "$AGENT_RESPONSE_FILE" -w '%{http_code}' \
  -X POST "$BOND_URL/api/v1/agent/turn" \
  -H "Content-Type: application/json" \
  -d "$PAYLOAD" \
  --max-time 300)
AGENT_EXIT=$?
set -e

AGENT_END="$(date +%s)"
AGENT_DURATION=$((AGENT_END - AGENT_START))

log "HTTP status:   $HTTP_CODE"
log "curl exit:     $AGENT_EXIT"
log "Duration:      ${AGENT_DURATION}s"

# ─── Step 2b: Parse response ────────────────────────────────────────────
section "STEP 2b: AGENT RESPONSE"

if [[ -f "$AGENT_RESPONSE_FILE" ]]; then
  AGENT_RESPONSE=$(jq -r '.response // .detail // "no response field"' "$AGENT_RESPONSE_FILE" 2>/dev/null || cat "$AGENT_RESPONSE_FILE")
  CONVERSATION_ID=$(jq -r '.conversation_id // "unknown"' "$AGENT_RESPONSE_FILE" 2>/dev/null || echo "unknown")

  log "Conversation:  $CONVERSATION_ID"
  log "Agent response:"
  echo "────────────────────────────────────────────────" >> "$LOGFILE"
  echo "$AGENT_RESPONSE" >> "$LOGFILE"
  echo "────────────────────────────────────────────────" >> "$LOGFILE"
else
  log "ERROR: No response file"
  AGENT_RESPONSE=""
  CONVERSATION_ID="unknown"
fi

# ─── Step 2c: Model metrics from Bond logs ──────────────────────────────
section "STEP 2c: MODEL METRICS"

# Query Bond's internal metrics if available (conversation messages give us tool call count)
if [[ "$CONVERSATION_ID" != "unknown" ]]; then
  set +e
  MESSAGES_JSON=$(curl -sf "$BOND_URL/api/v1/conversations/$CONVERSATION_ID/messages" 2>/dev/null)
  set -e

  if [[ -n "$MESSAGES_JSON" ]]; then
    echo "$MESSAGES_JSON" > "$LOG_DIR/${RUN_ID}.messages.json"

    # Count tool calls and assistant messages (each assistant message = 1 model call)
    TOTAL_MESSAGES=$(echo "$MESSAGES_JSON" | jq 'length' 2>/dev/null || echo "unknown")
    ASSISTANT_MESSAGES=$(echo "$MESSAGES_JSON" | jq '[.[] | select(.role == "assistant")] | length' 2>/dev/null || echo "unknown")
    TOOL_CALLS=$(echo "$MESSAGES_JSON" | jq '[.[] | select(.role == "tool")] | length' 2>/dev/null || echo "unknown")
    USER_MESSAGES=$(echo "$MESSAGES_JSON" | jq '[.[] | select(.role == "user")] | length' 2>/dev/null || echo "unknown")

    log "Total messages:    $TOTAL_MESSAGES"
    log "Model calls:       $ASSISTANT_MESSAGES"
    log "Tool calls:        $TOOL_CALLS"
    log "User messages:     $USER_MESSAGES"

    # Log each tool call for step-by-step review
    log ""
    log "Tool call sequence:"
    echo "$MESSAGES_JSON" | jq -r '.[] | select(.role == "tool") | "  → \(.tool_call_id // "?"): \(.content[:120] // "?")"' 2>/dev/null >> "$LOGFILE" || true

    NUM_TURNS="$ASSISTANT_MESSAGES"
  else
    log "Could not fetch conversation messages"
    NUM_TURNS="unknown"
  fi
else
  log "No conversation ID — cannot fetch metrics"
  NUM_TURNS="unknown"
fi

# ─── Step 3: Post-run diff ─────────────────────────────────────────────
section "STEP 3: FILES CHANGED"

CHANGED_FILES=$(git -C "$REPO_ROOT" diff --name-only "$START_SHA" 2>/dev/null || true)
UNTRACKED_FILES=$(git -C "$REPO_ROOT" ls-files --others --exclude-standard 2>/dev/null || true)

if [[ -z "$CHANGED_FILES" && -z "$UNTRACKED_FILES" ]]; then
  log "  (no changes detected)"
else
  for f in $CHANGED_FILES; do
    INSERTIONS=$(git -C "$REPO_ROOT" diff "$START_SHA" -- "$f" | grep -c '^+[^+]' || echo 0)
    DELETIONS=$(git -C "$REPO_ROOT" diff "$START_SHA" -- "$f" | grep -c '^-[^-]' || echo 0)
    log "  [modified] $f  (+$INSERTIONS -$DELETIONS)"
  done
  for f in $UNTRACKED_FILES; do
    [[ "$f" == tests/coding-runs/* ]] && continue
    LINES=$(wc -l < "$REPO_ROOT/$f" 2>/dev/null || echo "?")
    log "  [new file] $f  ($LINES lines)"
  done
fi

# ─── Step 4: Full diff ─────────────────────────────────────────────────
section "STEP 4: FULL DIFF"
log "git diff $START_SHA:"
echo "" >> "$LOGFILE"
git -C "$REPO_ROOT" diff "$START_SHA" >> "$LOGFILE" 2>/dev/null || true

for f in $UNTRACKED_FILES; do
  [[ "$f" == tests/coding-runs/* ]] && continue
  if [[ -f "$REPO_ROOT/$f" ]]; then
    echo "" >> "$LOGFILE"
    echo "diff --git a/$f b/$f" >> "$LOGFILE"
    echo "new file" >> "$LOGFILE"
    echo "--- /dev/null" >> "$LOGFILE"
    echo "+++ b/$f" >> "$LOGFILE"
    sed 's/^/+/' "$REPO_ROOT/$f" >> "$LOGFILE"
  fi
done

# ─── Step 5: Post-run file contents ────────────────────────────────────
section "STEP 5: POST-RUN FILE CONTENTS"
for f in $CHANGED_FILES $UNTRACKED_FILES; do
  [[ "$f" == tests/coding-runs/* ]] && continue
  if [[ -f "$REPO_ROOT/$f" ]]; then
    echo "" >> "$LOGFILE"
    echo "── $f (post-run, $(wc -l < "$REPO_ROOT/$f") lines) ──" >> "$LOGFILE"
    cat "$REPO_ROOT/$f" >> "$LOGFILE"
    echo "" >> "$LOGFILE"
  fi
done

# ─── Step 6: Project test suite ────────────────────────────────────────
section "STEP 6: PROJECT TESTS (make test)"
log "Running project test suite to check for regressions..."
echo "" >> "$LOGFILE"

set +e
TEST_OUTPUT=$(cd "$REPO_ROOT" && uv run pytest -v --tb=short 2>&1)
TEST_EXIT=$?
set -e

echo "$TEST_OUTPUT" >> "$LOGFILE"
echo "" >> "$LOGFILE"

TESTS_PASSED=$(echo "$TEST_OUTPUT" | grep -c " PASSED" || echo 0)
TESTS_FAILED=$(echo "$TEST_OUTPUT" | grep -c " FAILED" || echo 0)
TESTS_ERROR=$(echo "$TEST_OUTPUT" | grep -c " ERROR" || echo 0)

log "Project tests: $TESTS_PASSED passed, $TESTS_FAILED failed, $TESTS_ERROR errors"
log "Test exit code: $TEST_EXIT"

# ─── Step 7: Static validation ─────────────────────────────────────────
section "STEP 7: STATIC VALIDATION"

PASS_COUNT=0
FAIL_COUNT=0

check() {
  local name="$1"
  local result="$2"
  if [[ "$result" == "pass" ]]; then
    log "  ✅ $name"
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    log "  ❌ $name — $result"
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

for f in $CHANGED_FILES $UNTRACKED_FILES; do
  [[ "$f" == tests/coding-runs/* ]] && continue
  [[ "$f" != *.py ]] && continue
  if [[ -f "$REPO_ROOT/$f" ]]; then
    if python3 -c "import ast; ast.parse(open('$REPO_ROOT/$f').read())" 2>/dev/null; then
      check "Syntax valid: $f" "pass"
    else
      check "Syntax valid: $f" "syntax error"
    fi
  fi
done

for f in $CHANGED_FILES; do
  if [[ -f "$REPO_ROOT/$f" ]] && grep -q '<<<<<<<' "$REPO_ROOT/$f" 2>/dev/null; then
    check "No merge conflicts: $f" "merge conflict markers found"
  else
    check "No merge conflicts: $f" "pass"
  fi
done

CHANGED_PY=$(echo "$CHANGED_FILES" | grep '\.py$' || true)
if [[ -n "$CHANGED_PY" ]]; then
  set +e
  RUFF_OUTPUT=$(.venv/bin/python -m ruff check $CHANGED_PY 2>&1)
  RUFF_EXIT=$?
  set -e
  if [[ $RUFF_EXIT -eq 0 ]]; then
    check "Ruff lint" "pass"
  else
    check "Ruff lint" "$(echo "$RUFF_OUTPUT" | tail -1)"
    echo "$RUFF_OUTPUT" >> "$LOGFILE"
  fi
fi

# ─── Summary ────────────────────────────────────────────────────────────
section "SUMMARY"
log "┌─────────────────────────────────────────────┐"
log "│ Run ID:         $RUN_ID"
log "│ Agent:          Bond (agent_turn)"
log "│ Duration:       ${AGENT_DURATION}s"
log "│ Model calls:    ${NUM_TURNS:-unknown}"
log "│ Tool calls:     ${TOOL_CALLS:-unknown}"
log "│ HTTP status:    $HTTP_CODE"
log "│ Files changed:  $(echo "$CHANGED_FILES" | grep -c . || echo 0)"
log "│ Static checks:  $PASS_COUNT passed, $FAIL_COUNT failed"
if [[ $TEST_EXIT -ge 0 ]]; then
  log "│ Func tests:     $TESTS_PASSED passed, $TESTS_FAILED failed"
fi
log "│"
if [[ $FAIL_COUNT -eq 0 && $TESTS_FAILED -eq 0 && $TESTS_ERROR -eq 0 && "$HTTP_CODE" == "200" ]]; then
  log "│ Result: ✅ PASS"
else
  log "│ Result: ❌ FAIL"
fi
log "└─────────────────────────────────────────────┘"

# ─── Revert ─────────────────────────────────────────────────────────────
section "REVERT"
log "Revert is DISABLED — uncomment below to enable"

# # Uncomment these lines to revert changes after the test:
# log "Reverting to $START_SHA..."
# git -C "$REPO_ROOT" checkout "$START_SHA" -- . 2>/dev/null
# git -C "$REPO_ROOT" clean -fd -- backend/ 2>/dev/null
# log "Reverted."

# Save list of changed files for make coding-test-revert
REVERT_FILE="$LOG_DIR/.last-changed-files"
echo "$CHANGED_FILES" > "$REVERT_FILE"

log ""
log "Log:      $LOGFILE"
log "Response: $AGENT_RESPONSE_FILE"
[[ -f "$LOG_DIR/${RUN_ID}.messages.json" ]] && log "Messages: $LOG_DIR/${RUN_ID}.messages.json"
echo ""
echo "📄 Log:      $LOGFILE"
echo "📊 Response: $AGENT_RESPONSE_FILE"
