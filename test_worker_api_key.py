#!/usr/bin/env python3
"""Test worker API key resolution with SpacetimeDB."""

import asyncio
import os
import sys
from pathlib import Path

# Add the backend directory to the path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

# Mock the global state
class MockState:
    def __init__(self):
        from backend.app.agent.persistence_client import PersistenceClient
        self.persistence = PersistenceClient(agent_id="test-agent", mode="api")
        self.persistence.gateway_url = "http://localhost:18789"

async def test_api_key_resolution():
    """Test the _resolve_api_key logic."""
    # Create mock state
    _state = MockState()
    await _state.persistence.init()
    
    # Mock injected keys and provider aliases
    injected_keys = {}
    provider_aliases = {}
    
    def _resolve_provider(model_id: str) -> str:
        """Mock provider resolution."""
        if "gemini" in model_id.lower() or "google" in model_id.lower():
            return "google"
        elif "claude" in model_id.lower():
            return "anthropic"
        elif "gpt" in model_id.lower():
            return "openai"
        return "anthropic"  # default
    
    async def _resolve_api_key(model_id: str) -> str | None:
        """Mock the worker's API key resolution."""
        prov = _resolve_provider(model_id)
        
        # 1. Keys from provider_api_keys (injected at container launch)
        key = injected_keys.get(prov)
        if key:
            print(f"  Got {prov} key from injected_keys")
            return key
        
        # 2. SpacetimeDB via Gateway
        try:
            if _state.persistence and _state.persistence.mode == "api":
                # Try provider_api_keys table first
                encrypted_key = await _state.persistence.get_provider_api_key(prov)
                if encrypted_key:
                    from backend.app.core.crypto import decrypt_value
                    decrypted = decrypt_value(encrypted_key)
                    if decrypted and decrypted != encrypted_key:
                        print(f"  Got {prov} key from SpacetimeDB provider_api_keys")
                        return decrypted
                
                # Try settings table for LLM API keys
                llm_setting_key = f"llm.api_key.{prov}"
                encrypted_llm_key = await _state.persistence.get_setting(llm_setting_key)
                if encrypted_llm_key:
                    from backend.app.core.crypto import decrypt_value
                    decrypted = decrypt_value(encrypted_llm_key)
                    if decrypted and decrypted != encrypted_llm_key:
                        print(f"  Got {prov} key from SpacetimeDB settings (llm.api_key)")
                        return decrypted
        except Exception as e:
            print(f"  Error getting key from SpacetimeDB for {prov}: {e}")
        
        return None
    
    # Test with different model IDs
    test_models = [
        "gemini-2.0-flash",
        "claude-3-5-sonnet",
        "gpt-4o",
    ]
    
    for model in test_models:
        print(f"\nTesting model: {model}")
        key = await _resolve_api_key(model)
        if key:
            print(f"  ✓ Got API key: {key[:20]}...")
        else:
            print(f"  ✗ No API key found")
    
    await _state.persistence.close()

if __name__ == "__main__":
    print("Testing worker API key resolution from SpacetimeDB...")
    print("=" * 60)
    asyncio.run(test_api_key_resolution())