#!/usr/bin/env python3
"""
Seed initial providers into SpacetimeDB.
"""
import asyncio
import json
import time
from backend.app.core.spacetimedb import StdbClient

PROVIDERS = [
    {
        "id": "anthropic",
        "displayName": "Anthropic",
        "litellmPrefix": "anthropic",
        "apiBaseUrl": "https://api.anthropic.com",
        "modelsEndpoint": "/v1/models?limit=100",
        "modelsFetchMethod": "anthropic_api",
        "authType": "x-api-key",
        "isEnabled": True,
        "config": '{"anthropic_version": "2023-06-01"}',
    },
    {
        "id": "google",
        "displayName": "Google",
        "litellmPrefix": "gemini",
        "apiBaseUrl": "https://generativelanguage.googleapis.com",
        "modelsEndpoint": "/v1beta/models",
        "modelsFetchMethod": "google_api",
        "authType": "query_param",
        "isEnabled": True,
        "config": "{}",
    },
    {
        "id": "openai",
        "displayName": "OpenAI",
        "litellmPrefix": "openai",
        "apiBaseUrl": "https://api.openai.com",
        "modelsEndpoint": "/v1/models",
        "modelsFetchMethod": "openai_compat",
        "authType": "bearer",
        "isEnabled": True,
        "config": "{}",
    },
    {
        "id": "deepseek",
        "displayName": "DeepSeek",
        "litellmPrefix": "deepseek",
        "apiBaseUrl": "https://api.deepseek.com",
        "modelsEndpoint": "/models",
        "modelsFetchMethod": "openai_compat",
        "authType": "bearer",
        "isEnabled": True,
        "config": "{}",
    },
    {
        "id": "groq",
        "displayName": "Groq",
        "litellmPrefix": "groq",
        "apiBaseUrl": "https://api.groq.com/openai",
        "modelsEndpoint": "/v1/models",
        "modelsFetchMethod": "openai_compat",
        "authType": "bearer",
        "isEnabled": True,
        "config": "{}",
    },
    {
        "id": "mistral",
        "displayName": "Mistral",
        "litellmPrefix": "mistral",
        "apiBaseUrl": "https://api.mistral.ai",
        "modelsEndpoint": "/v1/models",
        "modelsFetchMethod": "openai_compat",
        "authType": "bearer",
        "isEnabled": True,
        "config": "{}",
    },
    {
        "id": "xai",
        "displayName": "xAI",
        "litellmPrefix": "xai",
        "apiBaseUrl": "https://api.x.ai",
        "modelsEndpoint": "/v1/models",
        "modelsFetchMethod": "openai_compat",
        "authType": "bearer",
        "isEnabled": True,
        "config": "{}",
    },
    {
        "id": "openrouter",
        "displayName": "OpenRouter",
        "litellmPrefix": "openrouter",
        "apiBaseUrl": "https://openrouter.ai/api",
        "modelsEndpoint": "/v1/models",
        "modelsFetchMethod": "openai_compat",
        "authType": "bearer",
        "isEnabled": True,
        "config": "{}",
    },
]

def encode_option(value):
    """Encode optional string as SpacetimeDB Option."""
    if value is None:
        return {"none": []}
    else:
        return {"some": value}

async def seed():
    client = StdbClient()
    try:
        now = int(time.time() * 1000)  # milliseconds
        for provider in PROVIDERS:
            # Convert to args list in the order defined in the reducer
            # id, displayName, litellmPrefix, apiBaseUrl, modelsEndpoint, modelsFetchMethod, authType, isEnabled, config, createdAt, updatedAt
            args = [
                provider["id"],
                provider["displayName"],
                provider["litellmPrefix"],
                encode_option(provider["apiBaseUrl"]),
                encode_option(provider["modelsEndpoint"]),
                provider["modelsFetchMethod"],
                provider["authType"],
                provider["isEnabled"],
                provider["config"],
                now,
                now,
            ]
            print(f"Seeding provider {provider['id']}...")
            success = await client.call_reducer("add_provider", args)
            if success:
                print(f"  OK")
            else:
                print(f"  FAILED")
        print("Seeding complete")
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(seed())