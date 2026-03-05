#!/usr/bin/env python3
"""Test API key resolution from SpacetimeDB."""

import asyncio
import os
import sys
from pathlib import Path

# Add the backend directory to the path
sys.path.insert(0, str(Path(__file__).parent / "backend"))

from backend.app.agent.persistence_client import PersistenceClient
from backend.app.core.crypto import encrypt_value, decrypt_value

async def test_get_provider_api_key():
    """Test getting a provider API key from SpacetimeDB."""
    # Create a persistence client
    client = PersistenceClient(agent_id="test-agent", mode="api")
    
    # Mock the Gateway URL to a test server
    # In real usage, this would be set via BOND_GATEWAY_URL env var
    client.gateway_url = "http://localhost:18792"
    
    try:
        await client.init()
        print(f"Client initialized with mode: {client.mode}")
        
        # Try to get a Google API key
        encrypted_key = await client.get_provider_api_key("google")
        if encrypted_key:
            print(f"Got encrypted Google API key from SpacetimeDB: {encrypted_key[:50]}...")
            # Try to decrypt it
            decrypted = decrypt_value(encrypted_key)
            if decrypted != encrypted_key:
                print(f"Successfully decrypted Google API key: {decrypted[:10]}...")
            else:
                print("Failed to decrypt API key (might be plaintext or wrong key)")
        else:
            print("No Google API key found in SpacetimeDB")
            
        # Try to get a setting
        setting = await client.get_setting("llm.api_key.google")
        if setting:
            print(f"Got setting llm.api_key.google from SpacetimeDB: {setting[:50]}...")
        else:
            print("No llm.api_key.google setting found in SpacetimeDB")
            
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await client.close()

async def test_encryption():
    """Test encryption/decryption."""
    test_key = "test-api-key-123"
    encrypted = encrypt_value(test_key)
    print(f"Original: {test_key}")
    print(f"Encrypted: {encrypted}")
    print(f"Decrypted: {decrypt_value(encrypted)}")
    print(f"Is encrypted? {encrypted.startswith('enc:')}")

if __name__ == "__main__":
    print("Testing API key resolution from SpacetimeDB...")
    print("=" * 60)
    
    # Test encryption first
    asyncio.run(test_encryption())
    
    print("\n" + "=" * 60)
    print("Testing PersistenceClient...")
    
    # Test getting API keys
    asyncio.run(test_get_provider_api_key())