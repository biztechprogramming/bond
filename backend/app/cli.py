"""Bond setup wizard — CLI for first-run configuration.

Usage: python -m backend.app.cli setup
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml


def _load_providers() -> dict:
    providers_path = Path(__file__).parent / "agent" / "providers.yaml"
    with open(providers_path) as f:
        return yaml.safe_load(f)


def setup() -> None:
    """Interactive setup wizard: pick provider, enter API key, save to vault."""
    from backend.app.config import BOND_HOME, BOND_JSON_PATH
    from backend.app.core.vault import Vault

    print("\n  Bond Setup Wizard")
    print("  =================\n")

    # Ensure bond home exists
    BOND_HOME.mkdir(parents=True, exist_ok=True)
    (BOND_HOME / "data").mkdir(parents=True, exist_ok=True)
    (BOND_HOME / "logs").mkdir(parents=True, exist_ok=True)
    (BOND_HOME / "cache").mkdir(parents=True, exist_ok=True)
    (BOND_HOME / "workspace").mkdir(parents=True, exist_ok=True)

    # Load available providers
    providers = _load_providers()
    chat_providers = providers.get("chat", {})

    print("  Available LLM providers:\n")
    provider_ids = list(chat_providers.keys())
    for i, pid in enumerate(provider_ids, 1):
        name = chat_providers[pid].get("name", pid)
        print(f"    {i}. {name}")

    print()
    while True:
        try:
            choice = input("  Select provider [1]: ").strip()
            if not choice:
                choice = "1"
            idx = int(choice) - 1
            if 0 <= idx < len(provider_ids):
                break
        except (ValueError, IndexError):
            pass
        print("  Invalid choice. Try again.")

    selected_provider = provider_ids[idx]
    provider_name = chat_providers[selected_provider].get("name", selected_provider)
    print(f"\n  Selected: {provider_name}")

    # Default models per provider
    default_models = {
        "anthropic": "claude-sonnet-4-20250514",
        "openai": "gpt-4o",
        "google": "gemini-2.0-flash",
        "deepseek": "deepseek-chat",
        "groq": "llama-3.3-70b-versatile",
        "mistral": "mistral-large-latest",
        "ollama": "llama3.2",
        "openrouter": "anthropic/claude-sonnet-4-20250514",
        "xai": "grok-3-mini",
    }

    default_model = default_models.get(selected_provider, "")
    model = input(f"  Model name [{default_model}]: ").strip() or default_model

    # API key (skip for local providers)
    local_providers = {"ollama", "lm_studio"}
    api_key = ""
    if selected_provider not in local_providers:
        api_key = input(f"  {provider_name} API key: ").strip()
        if not api_key:
            print("  Warning: No API key provided. Set it later in settings.")

    # Save to vault
    if api_key:
        vault = Vault()
        vault.set(f"{selected_provider.upper()}_API_KEY", api_key)
        print("  API key saved to encrypted vault.")

    # Write bond.json
    config = {
        "llm": {
            "provider": selected_provider,
            "model": model,
        },
    }

    if BOND_JSON_PATH.exists():
        with open(BOND_JSON_PATH) as f:
            existing = json.load(f)
        existing.update(config)
        config = existing

    with open(BOND_JSON_PATH, "w") as f:
        json.dump(config, f, indent=2)

    print(f"\n  Configuration saved to {BOND_JSON_PATH}")
    print("\n  Bond is ready! Start with: make dev\n")


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: bond <command>")
        print("Commands: setup")
        sys.exit(1)

    command = sys.argv[1]
    if command == "setup":
        setup()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
