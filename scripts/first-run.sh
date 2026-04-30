#!/usr/bin/env bash
# First-run credential display for Bond.
# Shows critical security credentials on first startup and requires acknowledgment.

set -euo pipefail

BOND_DATA="${BOND_HOME:-$HOME/.bond}/data"
SENTINEL="$BOND_DATA/.first_run_complete"

# Skip if already completed
if [ -f "$SENTINEL" ]; then
  exit 0
fi

mkdir -p "$BOND_DATA"

# Resolve credentials
GATEWAY_KEY=""
if [ -n "${BOND_API_KEY:-}" ]; then
  GATEWAY_KEY="$BOND_API_KEY"
elif [ -f "$BOND_DATA/.gateway_key" ]; then
  GATEWAY_KEY="$(cat "$BOND_DATA/.gateway_key")"
fi

STDB_TOKEN=""
if [ -n "${SPACETIMEDB_TOKEN:-}" ]; then
  STDB_TOKEN="$SPACETIMEDB_TOKEN"
elif [ -f "$HOME/.config/spacetime/cli.toml" ]; then
  STDB_TOKEN="$(grep -oP 'spacetimedb_token\s*=\s*"\K[^"]+' "$HOME/.config/spacetime/cli.toml" 2>/dev/null || true)"
fi

VAULT_KEY=""
if [ -f "$BOND_DATA/.vault_key" ]; then
  VAULT_KEY="$(cat "$BOND_DATA/.vault_key")"
fi

echo ""
echo "=================================================================="
echo "  BOND - FIRST RUN SECURITY CREDENTIALS"
echo "=================================================================="
echo ""
echo "  Save these credentials in a password manager NOW."
echo "  They control access to all Bond services and data."
echo ""
echo "------------------------------------------------------------------"
echo ""
echo "  1. BOND API KEY"
echo "     ${GATEWAY_KEY:-<not yet generated - will appear after services start>}"
echo ""
echo "     Controls access to ALL Bond HTTP and WebSocket endpoints."
echo "     Anyone with this key can use your Bond instance."
echo "     Set via: BOND_API_KEY env var or ~/.bond/data/.gateway_key"
echo ""
echo "------------------------------------------------------------------"
echo ""
echo "  2. SPACETIMEDB TOKEN"
echo "     ${STDB_TOKEN:-<not configured>}"
echo ""
echo "     Grants admin access to the SpacetimeDB database."
echo "     Can read/modify all conversations, agents, and memories."
echo "     Found in: ~/.config/spacetime/cli.toml"
echo ""
echo "------------------------------------------------------------------"
echo ""
echo "  3. VAULT ENCRYPTION KEY"
echo "     ${VAULT_KEY:-<not yet generated>}"
echo ""
echo "     Encrypts all stored secrets and API keys (LLM keys, etc)."
echo "     If lost, all stored credentials become unrecoverable."
echo "     Stored at: ~/.bond/data/.vault_key"
echo ""
echo "=================================================================="
echo ""
echo "  WARNING: Losing these credentials may result in loss of access"
echo "  to your Bond instance, database, or stored secrets."
echo ""
echo "=================================================================="
echo ""

# If running interactively, require acknowledgment
if [ -t 0 ]; then
  echo -n "  Type 'I understand' to continue: "
  read -r response
  if [ "$response" != "I understand" ]; then
    echo ""
    echo "  You must type 'I understand' to proceed."
    echo -n "  Type 'I understand' to continue: "
    read -r response
    if [ "$response" != "I understand" ]; then
      echo "  Exiting. Re-run to try again."
      exit 1
    fi
  fi
else
  echo "  (Non-interactive mode — continuing automatically)"
fi

echo ""
echo "  Credentials acknowledged. Starting Bond..."
echo ""

# Create sentinel
touch "$SENTINEL"
