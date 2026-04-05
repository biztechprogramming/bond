#!/usr/bin/env python3
"""Display Bond security credentials. Run anytime to review saved credentials."""

import os
import sys
from pathlib import Path


def main():
    bond_home = Path(os.environ.get("BOND_HOME", Path.home() / ".bond"))
    data_dir = bond_home / "data"

    # Resolve API key
    api_key = os.environ.get("BOND_API_KEY", "")
    if not api_key:
        key_path = data_dir / ".gateway_key"
        if key_path.exists():
            api_key = key_path.read_text().strip()

    # Resolve SpacetimeDB token
    stdb_token = os.environ.get("SPACETIMEDB_TOKEN", "")
    if not stdb_token:
        toml_path = Path.home() / ".config" / "spacetime" / "cli.toml"
        if toml_path.exists():
            import re
            m = re.search(r'spacetimedb_token\s*=\s*"([^"]+)"', toml_path.read_text())
            if m:
                stdb_token = m.group(1)

    # Resolve vault key
    vault_key = ""
    vault_path = data_dir / ".vault_key"
    if vault_path.exists():
        vault_key = vault_path.read_text().strip()

    print()
    print("=" * 66)
    print("  BOND - SECURITY CREDENTIALS")
    print("=" * 66)
    print()
    print("  1. BOND API KEY")
    print(f"     {api_key or '<not found>'}")
    print()
    print("     Controls access to ALL Bond HTTP and WebSocket endpoints.")
    print("     Source: BOND_API_KEY env var or ~/.bond/data/.gateway_key")
    print()
    print("-" * 66)
    print()
    print("  2. SPACETIMEDB TOKEN")
    print(f"     {stdb_token or '<not configured>'}")
    print()
    print("     Grants admin access to the SpacetimeDB database.")
    print("     Source: SPACETIMEDB_TOKEN env var or ~/.config/spacetime/cli.toml")
    print()
    print("-" * 66)
    print()
    print("  3. VAULT ENCRYPTION KEY")
    print(f"     {vault_key or '<not found>'}")
    print()
    print("     Encrypts all stored secrets and API keys.")
    print("     If lost, stored credentials become unrecoverable.")
    print("     Source: ~/.bond/data/.vault_key")
    print()
    print("=" * 66)
    print()


if __name__ == "__main__":
    main()
