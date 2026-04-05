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


def _prompt_anthropic_auth(provider_name: str) -> tuple[str, str]:
    """Prompt for Anthropic auth method: OAuth or API key.

    Returns (api_key_or_token, key_type).
    """
    print("\n  Authentication method:\n")
    print("    1. OAuth (Claude Max/Pro subscription) — No per-token costs")
    print("    2. API Key (pay-per-use from console.anthropic.com)")
    print()

    auth_choice = input("  Select [1]: ").strip() or "1"

    if auth_choice == "1":
        creds_path = Path.home() / ".claude" / ".credentials.json"
        if creds_path.exists():
            try:
                creds = json.loads(creds_path.read_text())
                oauth_data = creds.get("claudeAiOauth", {})
                access_token = oauth_data.get("accessToken", "")
                if access_token:
                    print(f"  Found Claude OAuth credentials at {creds_path}")
                    return access_token, "oauth_token"
                else:
                    print("  Warning: Credentials file exists but missing accessToken.")
            except (json.JSONDecodeError, KeyError):
                print("  Warning: Could not parse credentials file.")
        else:
            print(f"\n  OAuth credentials not found at {creds_path}")
            print()
            print("  To use OAuth, you need Claude CLI credentials:")
            print()
            print("    1. Install Claude CLI:  npm install -g @anthropic-ai/claude-code")
            print("    2. Run:                 claude")
            print("    3. Log in via browser when prompted")
            print("    4. Re-run:              make setup")
            print()

        fallback = input("  Press Enter to use an API key instead (or Ctrl+C to abort): ").strip()

    # Fall through to API key prompt
    api_key = input(f"  {provider_name} API key: ").strip()
    if not api_key:
        print("  Warning: No API key provided. Set it later in settings.")
    return api_key, "api_key"


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
    key_type = "api_key"
    if selected_provider not in local_providers:
        # Anthropic: offer OAuth option
        if selected_provider == "anthropic":
            api_key, key_type = _prompt_anthropic_auth(provider_name)
        else:
            api_key = input(f"  {provider_name} API key: ").strip()
            if not api_key:
                print("  Warning: No API key provided. Set it later in settings.")

    # Save to vault
    if api_key:
        vault = Vault()
        vault_key = f"{selected_provider.upper()}_API_KEY"
        vault.set(vault_key, api_key)
        vault.set_key_type(vault_key, key_type)
        if key_type == "oauth_token":
            print("  OAuth token saved to encrypted vault.")
        else:
            print("  API key saved to encrypted vault.")

    # --- Image Generation (optional) ---
    print("\n  \U0001f3a8 Image Generation (optional)")
    print("  " + "\u2501" * 30 + "\n")
    print("  Bond can generate images (icons, logos, mockups, diagrams).\n")

    image_providers_yaml = _load_providers().get("image", {})
    image_provider_ids = list(image_providers_yaml.keys())

    for i, pid in enumerate(image_provider_ids, 1):
        name = image_providers_yaml[pid].get("name", pid)
        note = ""
        if pid == "openai" and selected_provider == "openai":
            note = " \u2014 uses your existing OpenAI key \u2713"
        elif pid == "comfyui":
            note = " \u2014 local, free, requires GPU"
        print(f"    {i}. {name}{note}")
    skip_idx = len(image_provider_ids) + 1
    print(f"    {skip_idx}. Skip for now")
    print()

    img_choice = input(f"  Choose [1-{skip_idx}]: ").strip() or str(skip_idx)
    try:
        img_idx = int(img_choice) - 1
    except ValueError:
        img_idx = len(image_provider_ids)  # skip

    image_config: dict = {}
    if 0 <= img_idx < len(image_provider_ids):
        img_provider = image_provider_ids[img_idx]
        img_provider_name = image_providers_yaml[img_provider].get("name", img_provider)
        img_default_model = image_providers_yaml[img_provider].get("default_model", "")

        if img_provider == "openai" and selected_provider == "openai":
            print(f"  \u2705 Image generation configured: {img_provider_name} / {img_default_model}")
            print("     Using existing OpenAI API key from vault.")
        elif img_provider == "comfyui":
            print(f"  \u2705 Image generation configured: {img_provider_name}")
            print("     Make sure ComfyUI is running on http://localhost:8188")
        else:
            img_api_key = input(f"  {img_provider_name} API key: ").strip()
            if img_api_key:
                vault_key = f"{img_provider.upper()}_API_KEY"
                vault.set(vault_key, img_api_key)
                print(f"  \u2705 {img_provider_name} API key saved to vault.")
            else:
                print("  Warning: No API key provided. Set it later in settings.")

        image_config = {
            "provider": img_provider,
            "model": img_default_model,
        }
    else:
        print("  Skipped image generation setup.")

    # Write bond.json
    config = {
        "llm": {
            "provider": selected_provider,
            "model": model,
        },
    }
    if image_config:
        config["image"] = image_config

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
