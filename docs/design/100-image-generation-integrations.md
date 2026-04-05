# Design Doc 100: Image Generation Integrations

**Status:** Proposal  
**Author:** Bond AI  
**Date:** 2026-04-05  
**Depends on:** Design Doc 014 (Integration Evaluation), Design Doc 028 (Unify LLM Key Resolution)

---

## 1. Problem Statement

Bond has **zero image generation capability**. When a user asks Bond to create an icon, logo, diagram, mockup, or any visual asset, Bond must refuse or suggest the user go elsewhere. This is a significant gap — modern AI assistants are expected to handle multimodal tasks.

### 1.1 Use Cases That Are Currently Impossible

| Use Case | Frequency | Value |
|----------|-----------|-------|
| Generate app icons, logos, favicons | High | Users ask Bond to create branding assets for projects it's building |
| Create UI mockups / wireframes | Medium | Visual planning before coding |
| Generate placeholder images for development | Medium | Stock-photo replacement during frontend work |
| Create diagrams and architecture visuals | Medium | Supplement design docs with visual aids |
| Edit / refine existing images (img2img) | Low | Iterate on generated or uploaded assets |
| Generate social media / marketing assets | Low | Content creation workflows |

### 1.2 Current Architecture Gap

Bond's `providers.yaml` has two capability sections:

```yaml
chat:       # 13 providers (anthropic, openai, google, etc.)
embedding:  # 4 providers (huggingface, openai, ollama, google)
```

There is no `image` section. The tool registry (`native_registry.py`) has ~30 tools spanning file I/O, code execution, web search, memory, deployment, and coding agents — but nothing for image generation.

---

## 2. Goals

1. **Add a `generate_image` tool** to Bond's tool registry that any agent can invoke
2. **Support multiple image providers** following the same multi-provider pattern as chat (providers.yaml + API key resolution)
3. **Reuse existing infrastructure** — API key vault, provider settings UI, LiteLLM where possible
4. **Start with 3 providers** covering cloud, multi-model, and local use cases
5. **Store generated images** in the agent workspace and return them inline in conversations
6. **Support the setup wizard** — `make setup` / `uv run bond setup` should offer image provider configuration

---

## 3. Non-Goals

- **Image understanding / vision** — Already handled by multimodal chat models (GPT-4o, Claude, Gemini). This doc is about *generation* only.
- **Video generation** — Out of scope for v1. Could follow the same pattern later.
- **Fine-tuning / training** — No custom model training. We use hosted APIs.
- **Real-time image editing UI** — Bond generates images via tool calls, not a canvas editor.

---

## 4. Provider Selection

### 4.1 Evaluation Matrix

| Provider | API Quality | Model Variety | Cost | Local Option | LiteLLM Support | Effort | Verdict |
|----------|------------|---------------|------|-------------|-----------------|--------|---------|
| **OpenAI (DALL-E / gpt-image-1)** | ⭐⭐⭐⭐⭐ | 2 models | $$$ | No | ✅ Native | Low | **Phase 1** |
| **Replicate** | ⭐⭐⭐⭐ | 100+ models | $$ | No | ❌ Custom | Low-Med | **Phase 1** |
| **ComfyUI (Local)** | ⭐⭐⭐⭐ | Unlimited (local) | Free | ✅ Yes | ❌ Custom | Medium | **Phase 2** |
| Stability AI | ⭐⭐⭐⭐ | 3-4 models | $$ | No | ❌ Custom | Low-Med | Phase 3 |
| Fal.ai | ⭐⭐⭐⭐ | 20+ models | $ | No | ❌ Custom | Low-Med | Phase 3 |
| Midjourney | ⭐⭐⭐⭐⭐ | 1 model | $$$ | No | ❌ No API | N/A | Not viable (no official API) |

### 4.2 Recommended Phasing

**Phase 1 — Immediate (this PR)**
- **OpenAI** — Reuse existing API key. LiteLLM has native `image_generation` support. Lowest effort, highest coverage since most Bond users already have an OpenAI key.
- **Replicate** — The "OpenRouter of images." One API key unlocks Flux Pro, Stable Diffusion 3.5, SDXL, Playground v3, and hundreds more. Best model variety per integration effort.

**Phase 2 — Local-First**
- **ComfyUI** — The "Ollama of images." Runs on user's GPU, no API costs, no data leaving the machine. Aligns with Bond's local-first philosophy (we already support Ollama and LM Studio for chat).

**Phase 3 — Expand**
- **Stability AI** — Direct API for fine-grained SD3.5 control (negative prompts, style presets, img2img).
- **Fal.ai** — Serverless GPU, very fast, good for latency-sensitive workflows.

---

## 5. Technical Design

### 5.1 Provider Configuration

Extend `providers.yaml` with a new `image` section:

```yaml
# providers.yaml (additions)
image:
  openai:
    name: OpenAI (DALL-E / gpt-image-1)
    litellm_provider: openai
    models:
      - dall-e-3
      - gpt-image-1
    default_model: gpt-image-1
    supports:
      - text_to_image
      - image_edit
    max_resolution: 1536x1536
    output_formats: [png, webp]

  replicate:
    name: Replicate
    api_base_url: https://api.replicate.com/v1
    models:
      - black-forest-labs/flux-1.1-pro
      - stability-ai/stable-diffusion-3.5-large
      - stability-ai/sdxl
      - playgroundai/playground-v3
    default_model: black-forest-labs/flux-1.1-pro
    supports:
      - text_to_image
    output_formats: [png, webp, jpg]

  comfyui:
    name: ComfyUI (Local)
    api_base_url: http://localhost:8188
    auth_type: none
    models: []  # discovered dynamically from local instance
    default_model: auto
    supports:
      - text_to_image
      - image_edit
      - img2img
    output_formats: [png]
```

### 5.2 SpacetimeDB Schema Update

The existing `providers` table already has generic fields that can accommodate image providers:

```typescript
// providers_table.ts — existing schema (no changes needed)
{
  id: string,           // e.g. "image.openai", "image.replicate"
  displayName: string,
  litellmPrefix: string,
  apiBaseUrl: string?,
  modelsEndpoint: string?,
  modelsFetchMethod: string,
  authType: string,
  isEnabled: boolean,
  config: string,       // JSON blob for provider-specific settings
  createdAt: u64,
  updatedAt: u64,
}
```

We add a new `image_settings` table for user preferences:

```sql
CREATE TABLE image_settings (
    id          TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
    provider    TEXT NOT NULL DEFAULT 'openai',
    model       TEXT NOT NULL DEFAULT 'gpt-image-1',
    resolution  TEXT NOT NULL DEFAULT '1024x1024',
    quality     TEXT NOT NULL DEFAULT 'standard',  -- standard | hd
    style       TEXT NOT NULL DEFAULT 'natural',   -- natural | vivid (OpenAI-specific)
    output_dir  TEXT NOT NULL DEFAULT '.bond/images',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

### 5.3 API Key Resolution

Reuse the existing `ApiKeyResolver` and `Vault` infrastructure. Image providers follow the same resolution chain:

```
1. Environment variable:  OPENAI_API_KEY, REPLICATE_API_KEY
2. Settings DB:           llm.api_key.openai (shared with chat)
3. Vault file:            ~/.bond/vault/OPENAI_API_KEY
```

**Key insight:** OpenAI's image API uses the **same API key** as their chat API. Users who already configured OpenAI for chat get image generation for free — zero additional setup.

Replicate requires a new key: `REPLICATE_API_KEY`.

### 5.4 Tool Definition

Add a `generate_image` tool to `definitions.py`:

```python
{
    "type": "function",
    "function": {
        "name": "generate_image",
        "description": (
            "Generate an image from a text prompt. Returns the file path of the "
            "generated image saved to the workspace. Use for icons, logos, mockups, "
            "diagrams, or any visual asset the user requests."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Detailed description of the image to generate. Be specific about style, colors, composition, and content."
                },
                "size": {
                    "type": "string",
                    "enum": ["256x256", "512x512", "1024x1024", "1024x1536", "1536x1024"],
                    "description": "Image dimensions. Default: 1024x1024"
                },
                "style": {
                    "type": "string",
                    "enum": ["natural", "vivid", "anime", "photographic", "digital-art", "pixel-art", "icon"],
                    "description": "Visual style hint. Default: natural"
                },
                "provider": {
                    "type": "string",
                    "enum": ["openai", "replicate", "comfyui"],
                    "description": "Image provider to use. Default: user's configured provider."
                },
                "model": {
                    "type": "string",
                    "description": "Specific model to use. Default: provider's default model."
                },
                "filename": {
                    "type": "string",
                    "description": "Output filename (without extension). Default: auto-generated from prompt."
                },
                "count": {
                    "type": "integer",
                    "description": "Number of images to generate (1-4). Default: 1"
                }
            },
            "required": ["prompt"]
        }
    }
}
```

### 5.5 Tool Handler Implementation

New file: `backend/app/agent/tools/image_gen.py`

```python
"""Image generation tool — multi-provider image creation."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
from pathlib import Path

import httpx
import litellm

from backend.app.agent.api_key_resolver import ApiKeyResolver
from backend.app.config import get_settings

logger = logging.getLogger("bond.agent.tools.image_gen")

# Provider-specific adapters
PROVIDER_ADAPTERS = {
    "openai": "_generate_openai",
    "replicate": "_generate_replicate",
    "comfyui": "_generate_comfyui",
}


async def handle_generate_image(arguments: dict, context: dict) -> dict:
    """Generate an image from a text prompt."""
    prompt = arguments.get("prompt", "")
    if not prompt:
        return {"error": "prompt is required"}

    size = arguments.get("size", "1024x1024")
    style = arguments.get("style", "natural")
    provider = arguments.get("provider")  # None = use default
    model = arguments.get("model")
    filename = arguments.get("filename")
    count = min(arguments.get("count", 1), 4)

    # Resolve provider from settings if not specified
    settings = get_settings()
    provider = provider or getattr(settings, "image_provider", "openai")
    
    # Resolve API key using existing infrastructure
    resolver = ApiKeyResolver(provider=provider, model=model or "")
    api_key = await resolver.resolve_api_key(model or "")

    # Determine output path
    workspace = context.get("workspace_dir", "/workspace")
    output_dir = Path(workspace) / ".bond" / "images"
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        # Auto-generate filename from prompt hash
        slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower())[:40]
        filename = slug.rstrip("-")

    # Dispatch to provider adapter
    adapter_name = PROVIDER_ADAPTERS.get(provider)
    if not adapter_name:
        return {"error": f"Unsupported image provider: {provider}"}

    adapter = globals()[adapter_name]
    results = await adapter(
        prompt=prompt,
        size=size,
        style=style,
        model=model,
        api_key=api_key,
        count=count,
    )

    # Save images to workspace
    saved_paths = []
    for i, image_data in enumerate(results):
        suffix = f"_{i+1}" if count > 1 else ""
        output_path = output_dir / f"{filename}{suffix}.png"
        
        if isinstance(image_data, bytes):
            output_path.write_bytes(image_data)
        elif isinstance(image_data, str) and image_data.startswith("http"):
            # Download from URL
            async with httpx.AsyncClient() as client:
                resp = await client.get(image_data)
                output_path.write_bytes(resp.content)
        elif isinstance(image_data, str):
            # Base64 encoded
            output_path.write_bytes(base64.b64decode(image_data))

        saved_paths.append(str(output_path))
        logger.info("Image saved: %s", output_path)

    return {
        "success": True,
        "paths": saved_paths,
        "provider": provider,
        "model": model,
        "prompt": prompt,
        "message": f"Generated {len(saved_paths)} image(s): {', '.join(saved_paths)}",
    }


async def _generate_openai(
    prompt: str,
    size: str,
    style: str,
    model: str | None,
    api_key: str | None,
    count: int,
) -> list[bytes | str]:
    """Generate via OpenAI DALL-E / gpt-image-1."""
    model = model or "gpt-image-1"

    # LiteLLM has native image_generation support
    response = await litellm.aimage_generation(
        model=f"openai/{model}",
        prompt=prompt,
        n=count,
        size=size,
        quality="standard",
        response_format="b64_json",
        api_key=api_key,
    )

    return [item.b64_json for item in response.data]


async def _generate_replicate(
    prompt: str,
    size: str,
    style: str,
    model: str | None,
    api_key: str | None,
    count: int,
) -> list[str]:
    """Generate via Replicate API."""
    model = model or "black-forest-labs/flux-1.1-pro"

    # Parse size to width/height
    w, h = (int(x) for x in size.split("x"))

    async with httpx.AsyncClient() as client:
        # Create prediction
        resp = await client.post(
            "https://api.replicate.com/v1/predictions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "input": {
                    "prompt": prompt,
                    "width": w,
                    "height": h,
                    "num_outputs": count,
                },
            },
            timeout=120,
        )
        resp.raise_for_status()
        prediction = resp.json()

        # Poll for completion
        poll_url = prediction["urls"]["get"]
        for _ in range(120):  # 2 min timeout
            import asyncio
            await asyncio.sleep(1)
            poll_resp = await client.get(
                poll_url,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            result = poll_resp.json()
            if result["status"] == "succeeded":
                return result["output"]  # List of URLs
            if result["status"] == "failed":
                raise RuntimeError(f"Replicate generation failed: {result.get('error')}")

    raise TimeoutError("Replicate prediction timed out after 120s")


async def _generate_comfyui(
    prompt: str,
    size: str,
    style: str,
    model: str | None,
    api_key: str | None,
    count: int,
) -> list[bytes]:
    """Generate via local ComfyUI instance."""
    import json
    import uuid

    base_url = os.environ.get("COMFYUI_API_BASE", "http://localhost:8188")
    w, h = (int(x) for x in size.split("x"))

    # Build a simple txt2img workflow
    # ComfyUI uses a node-based workflow JSON format
    client_id = str(uuid.uuid4())
    workflow = _build_comfyui_workflow(prompt, w, h, model or "sd_xl_base_1.0")

    async with httpx.AsyncClient() as client:
        # Queue prompt
        resp = await client.post(
            f"{base_url}/prompt",
            json={"prompt": workflow, "client_id": client_id},
            timeout=10,
        )
        resp.raise_for_status()
        prompt_id = resp.json()["prompt_id"]

        # Poll history for completion
        for _ in range(180):  # 3 min timeout for local gen
            import asyncio
            await asyncio.sleep(1)
            hist_resp = await client.get(f"{base_url}/history/{prompt_id}")
            history = hist_resp.json()
            if prompt_id in history:
                outputs = history[prompt_id]["outputs"]
                images = []
                for node_output in outputs.values():
                    for img in node_output.get("images", []):
                        img_resp = await client.get(
                            f"{base_url}/view",
                            params={
                                "filename": img["filename"],
                                "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output"),
                            },
                        )
                        images.append(img_resp.content)
                return images[:count]

    raise TimeoutError("ComfyUI generation timed out after 180s")


def _build_comfyui_workflow(prompt: str, width: int, height: int, model: str) -> dict:
    """Build a minimal ComfyUI txt2img workflow."""
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": f"{model}.safetensors"},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": prompt, "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "", "clip": ["1", 1]},  # negative prompt
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": 1},
        },
        "5": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["1", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
                "seed": -1,
                "steps": 25,
                "cfg": 7.0,
                "sampler_name": "euler_ancestral",
                "scheduler": "normal",
                "denoise": 1.0,
            },
        },
        "6": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["5", 0], "vae": ["1", 2]},
        },
        "7": {
            "class_type": "SaveImage",
            "inputs": {"images": ["6", 0], "filename_prefix": "bond"},
        },
    }
```

### 5.6 Tool Registration

Register in both `native_registry.py` (container-side) and `__init__.py` (host-side):

```python
# native_registry.py additions
from .image_gen import handle_generate_image

registry.register("generate_image", handle_generate_image)
```

### 5.7 Setup Wizard Integration

The setup wizard (`uv run bond setup`) currently walks users through:
1. Pick a chat provider
2. Choose a model
3. Enter API key

Add a new section after chat setup:

```
🎨 Image Generation (optional)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Bond can generate images (icons, logos, mockups, diagrams).

Available providers:
  1. OpenAI (DALL-E 3 / gpt-image-1) — uses your existing OpenAI key ✓
  2. Replicate (Flux, Stable Diffusion, 100+ models)
  3. ComfyUI (local, free, requires GPU)
  4. Skip for now

Choose [1-4]: 1

✅ Image generation configured: OpenAI / gpt-image-1
   Using existing OpenAI API key from vault.
```

### 5.8 Frontend Display

When `generate_image` returns, the conversation UI needs to render images inline. The gateway should:

1. Detect tool results containing image paths
2. Serve images via a `/api/workspace-files/:path` endpoint (or existing file-serving route)
3. Frontend renders `<img>` tags with the workspace-relative path

```typescript
// Message rendering — detect image tool results
if (toolResult.name === "generate_image" && toolResult.paths) {
  return (
    <div className="flex flex-wrap gap-2">
      {toolResult.paths.map((path: string) => (
        <img 
          key={path}
          src={`/api/workspace-files/${encodeURIComponent(path)}`}
          alt={toolResult.prompt}
          className="rounded-lg max-w-md shadow-md"
        />
      ))}
    </div>
  );
}
```

---

## 6. Provider Deep-Dives

### 6.1 OpenAI (DALL-E / gpt-image-1)

**Why first:** Zero additional setup for existing OpenAI users. LiteLLM has native `aimage_generation()` support.

| Feature | DALL-E 3 | gpt-image-1 |
|---------|----------|-------------|
| Max resolution | 1024x1792 | 1536x1536 |
| Styles | vivid, natural | More flexible |
| Editing | ❌ | ✅ (inpainting) |
| Cost (1024x1024) | $0.040 | $0.040 |
| Speed | ~5s | ~8s |
| Quality | Good | Excellent (GPT-4o based) |

**API call via LiteLLM:**
```python
response = await litellm.aimage_generation(
    model="openai/gpt-image-1",
    prompt="A minimalist app icon for a personal AI assistant named Bond...",
    n=1,
    size="1024x1024",
    response_format="b64_json",
    api_key=resolved_key,
)
```

### 6.2 Replicate

**Why second:** One API key → hundreds of models. The "OpenRouter of images."

| Model | Best For | Speed | Cost |
|-------|----------|-------|------|
| Flux 1.1 Pro | General purpose, high quality | ~5s | $0.04/image |
| Flux Schnell | Fast drafts | ~1s | $0.003/image |
| SD 3.5 Large | Fine control, negative prompts | ~8s | $0.035/image |
| SDXL | Battle-tested, wide ecosystem | ~5s | $0.01/image |
| Playground v3 | Artistic/creative | ~6s | $0.02/image |

**API pattern:**
```python
# POST https://api.replicate.com/v1/predictions
# Poll GET {prediction_url} until status == "succeeded"
# Response contains list of output URLs
```

### 6.3 ComfyUI (Local — Phase 2)

**Why local matters:** Aligns with Bond's existing local-first options (Ollama, LM Studio). No API costs, no data leaving the machine, unlimited generation.

**Requirements:**
- ComfyUI running locally on port 8188
- A GPU with ≥6GB VRAM (for SDXL) or ≥4GB (for SD 1.5)
- Downloaded model checkpoints

**Discovery:** Bond can auto-detect ComfyUI by checking `http://localhost:8188/system_stats`. If available, offer it in the setup wizard.

---

## 7. Prompt Engineering for Image Tools

The agent needs guidance on *when* and *how* to use `generate_image` effectively. Add to the system prompt:

```markdown
## Image Generation
When the user asks you to create, generate, or design any visual asset:
1. Use the `generate_image` tool with a detailed prompt
2. Be specific: describe style, colors, composition, mood, and content
3. For icons/logos: specify "minimal, clean, vector-style, solid background"
4. For UI mockups: describe layout, components, and color scheme
5. Always save to the workspace and show the user the result
6. If the first result isn't right, iterate — refine the prompt based on feedback
```

---

## 8. Cost Controls

Image generation costs money. Add guardrails:

| Control | Implementation |
|---------|---------------|
| **Per-image cost tracking** | Log provider + model + size + cost to the existing cost tracking system (Design Doc 081) |
| **Daily budget** | `image_settings.daily_budget_usd` — default $5.00, configurable |
| **Confirmation for expensive ops** | If generating >2 images or using HD quality, ask user first |
| **Cost display** | Show estimated cost before generation: "This will cost ~$0.04" |

---

## 9. Migration Plan

### Phase 1: Core Integration (1-2 days)

| Step | Task | Files Changed |
|------|------|---------------|
| 1 | Add `image` section to `providers.yaml` | `backend/app/agent/providers.yaml` |
| 2 | Create `image_gen.py` tool handler | `backend/app/agent/tools/image_gen.py` (new) |
| 3 | Add tool definition to `definitions.py` | `backend/app/agent/tools/definitions.py` |
| 4 | Register tool in both registries | `backend/app/agent/tools/native_registry.py`, `__init__.py` |
| 5 | Add `generate_image` to agent tool list | `backend/app/agent/tools/definitions.py` (TOOL_SUMMARIES) |
| 6 | Add image prompt fragment | `prompts/tools/image-generation.md` (new) |
| 7 | Seed Replicate provider to DB | `migrations/XXXXXX_image_providers.up.sql` (new) |

### Phase 2: Setup Wizard + UI (1 day)

| Step | Task | Files Changed |
|------|------|---------------|
| 8 | Add image provider to setup wizard | `backend/app/cli/setup.py` (or equivalent) |
| 9 | Add image rendering in chat UI | `frontend/src/components/chat/MessageContent.tsx` |
| 10 | Add workspace file serving route | `gateway/src/routes/workspace.ts` (or existing) |

### Phase 3: ComfyUI Local (1-2 days)

| Step | Task | Files Changed |
|------|------|---------------|
| 11 | Add ComfyUI adapter | `backend/app/agent/tools/image_gen.py` |
| 12 | Add ComfyUI auto-detection | `backend/app/agent/tools/image_gen.py` |
| 13 | Add ComfyUI to setup wizard | `backend/app/cli/setup.py` |

---

## 10. Testing Strategy

### Unit Tests

```python
# tests/test_image_gen.py

async def test_generate_image_openai_returns_path():
    """Mock OpenAI API, verify image saved to workspace."""

async def test_generate_image_replicate_polling():
    """Mock Replicate API, verify polling + download."""

async def test_generate_image_missing_api_key():
    """Verify helpful error when no API key configured."""

async def test_generate_image_invalid_provider():
    """Verify error for unsupported provider."""

async def test_filename_generation_from_prompt():
    """Verify slug generation from prompt text."""

async def test_cost_budget_enforcement():
    """Verify generation blocked when daily budget exceeded."""
```

### Integration Tests

```python
async def test_agent_generates_icon_end_to_end():
    """Send 'create an icon for my app' → verify generate_image tool called → image file exists."""

async def test_agent_iterates_on_image():
    """Send 'make it more blue' → verify second generate_image call with refined prompt."""
```

---

## 11. Security Considerations

| Concern | Mitigation |
|---------|-----------|
| **Prompt injection via image prompts** | Image prompts are user-controlled by design — no injection risk beyond normal tool use |
| **API key exposure** | Reuse existing vault/encryption infrastructure. No new key storage mechanism |
| **Malicious image content** | OpenAI and Replicate have built-in content filters. ComfyUI is local (user's responsibility) |
| **Cost runaway** | Daily budget limit + per-generation cost logging |
| **Large file storage** | Images saved to workspace (ephemeral in containers). Not persisted to DB. |

---

## 12. Open Questions

- [ ] Should generated images be persisted beyond the container lifecycle? (e.g., save to SpacetimeDB as blobs, or to a shared volume)
- [ ] Should we support image-to-image (img2img) in v1, or defer to Phase 3?
- [ ] Should the `generate_image` tool be available to all agents, or only certain tiers/roles?
- [ ] Do we need a dedicated "image agent" skill (like coding_agent), or is a single tool sufficient?
- [ ] Should ComfyUI workflow templates be user-customizable, or hardcoded?

---

## 13. References

- **`backend/app/agent/providers.yaml`** — Current provider definitions (chat + embedding only)
- **`backend/app/agent/llm.py`** — LiteLLM wrapper with provider config and API key resolution
- **`backend/app/agent/api_key_resolver.py`** — Multi-source API key resolution (env → DB → vault)
- **`backend/app/agent/tools/native_registry.py`** — Container-side tool registry (30 tools registered)
- **`backend/app/agent/tools/__init__.py`** — Host-side tool registry with handler imports
- **`backend/app/agent/tools/definitions.py`** — Tool JSON schemas for LLM function calling
- **`frontend/src/lib/spacetimedb/providers_table.ts`** — SpacetimeDB provider schema
- **`docs/design/014-integration-evaluation.md`** — Previous integration evaluation framework
- **`docs/design/053-solidtime-mcp-integration.md`** — Pattern for adding new service integrations
- **`docs/design/081-cost-tracking-and-budget-controls.md`** — Cost tracking infrastructure
- [LiteLLM Image Generation docs](https://docs.litellm.ai/docs/image_generation)
- [Replicate HTTP API](https://replicate.com/docs/reference/http)
- [ComfyUI API docs](https://github.com/comfyanonymous/ComfyUI/wiki/API)
- [OpenAI Images API](https://platform.openai.com/docs/api-reference/images)
