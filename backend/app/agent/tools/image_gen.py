"""Image generation tool — multi-provider image creation.

Design Doc 100: Image Generation Integrations
Supports OpenAI (DALL-E / gpt-image-1), Replicate, and ComfyUI (local).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
from pathlib import Path

import httpx

from backend.app.agent.api_key_resolver import ApiKeyResolver

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

    # Resolve provider from bond.json config if not specified
    if not provider:
        try:
            from backend.app.config import load_bond_json
            bond_cfg = load_bond_json()
            image_cfg = bond_cfg.get("image", {})
            provider = image_cfg.get("provider", "openai")
            if not model:
                model = image_cfg.get("model")
        except Exception:
            provider = "openai"

    # Resolve API key using existing infrastructure
    api_key: str | None = None
    if provider != "comfyui":
        try:
            # Build resolver from worker config if available in context,
            # otherwise fall back to injected_keys from bond.json.
            injected_keys: dict[str, str] = {}
            provider_aliases: dict[str, str] = {}
            litellm_prefixes: dict[str, str] = {}
            if context:
                cfg = context.get("config", {})
                injected_keys = cfg.get("api_keys", {})
                provider_aliases = cfg.get("provider_aliases", {})
                litellm_prefixes = cfg.get("litellm_prefixes", {})
            resolver = ApiKeyResolver(
                injected_keys=injected_keys,
                provider_aliases=provider_aliases,
                litellm_prefixes=litellm_prefixes,
            )
            # Construct a model-like string so the resolver can identify the provider
            resolve_model = model or f"{provider}/image-gen"
            api_key = await resolver.resolve_api_key(resolve_model)
        except Exception as e:
            logger.warning("API key resolution failed for image provider %s: %s", provider, e)

    if provider != "comfyui" and not api_key:
        env_key = f"{provider.upper()}_API_KEY"
        api_key = os.environ.get(env_key)
        if not api_key:
            return {
                "error": f"No API key found for image provider '{provider}'. "
                f"Set {env_key} or run 'make setup' to configure.",
            }

    # Determine output path — /data/images is the writable directory in the
    # agent container.  /workspace is a host bind-mount and .bond is a
    # separate named volume on the *main* Bond container, not the agent.
    output_dir = Path("/data/images")
    output_dir.mkdir(parents=True, exist_ok=True)

    if not filename:
        slug = re.sub(r"[^a-z0-9]+", "-", prompt.lower())[:40]
        filename = slug.rstrip("-") or "image"
    else:
        # Strip any existing image extension to avoid double extensions
        # (e.g., "photo.png" -> "photo" before we append ".png")
        filename = re.sub(r"\.(png|jpe?g|webp|gif|bmp)$", "", filename, flags=re.IGNORECASE)

    # Dispatch to provider adapter
    adapter_name = PROVIDER_ADAPTERS.get(provider)
    if not adapter_name:
        return {"error": f"Unsupported image provider: {provider}. Supported: {', '.join(PROVIDER_ADAPTERS)}"}

    adapter = globals()[adapter_name]
    try:
        results = await adapter(
            prompt=prompt,
            size=size,
            style=style,
            model=model,
            api_key=api_key,
            count=count,
        )
    except Exception as e:
        logger.exception("Image generation failed with provider %s", provider)
        return {"error": f"Image generation failed: {e}"}

    # Save images to workspace
    saved_paths = []
    for i, image_data in enumerate(results):
        suffix = f"_{i + 1}" if count > 1 else ""
        output_path = output_dir / f"{filename}{suffix}.png"

        if isinstance(image_data, bytes):
            output_path.write_bytes(image_data)
        elif isinstance(image_data, str) and image_data.startswith("http"):
            async with httpx.AsyncClient() as client:
                resp = await client.get(image_data, timeout=60)
                resp.raise_for_status()
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
    import litellm

    model = model or "gpt-image-1"

    # gpt-image-1 does not support response_format through litellm's
    # parameter validation.  Use drop_params so litellm silently strips
    # any unsupported keys instead of raising UnsupportedParamsError.
    prev_drop = getattr(litellm, "drop_params", False)
    litellm.drop_params = True
    try:
        response = await litellm.aimage_generation(
            model=f"openai/{model}",
            prompt=prompt,
            n=count,
            size=size,
            quality="auto",
            api_key=api_key,
        )
    finally:
        litellm.drop_params = prev_drop

    # Extract image data — prefer b64_json, fall back to url
    images: list[bytes | str] = []
    for item in response.data:
        if hasattr(item, "b64_json") and item.b64_json:
            images.append(item.b64_json)
        elif hasattr(item, "url") and item.url:
            images.append(item.url)
        else:
            logger.warning("Image response item has no b64_json or url: %s", item)
    return images


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

    w, h = (int(x) for x in size.split("x"))

    async with httpx.AsyncClient() as client:
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

        poll_url = prediction["urls"]["get"]
        for _ in range(120):  # 2 min timeout
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
    import uuid

    base_url = os.environ.get("COMFYUI_API_BASE", "http://localhost:8188")
    w, h = (int(x) for x in size.split("x"))

    client_id = str(uuid.uuid4())
    workflow = _build_comfyui_workflow(prompt, w, h, model or "sd_xl_base_1.0")

    async with httpx.AsyncClient() as client:
        # Check if ComfyUI is reachable
        try:
            health = await client.get(f"{base_url}/system_stats", timeout=5)
            health.raise_for_status()
        except (httpx.ConnectError, httpx.TimeoutException):
            raise ConnectionError(
                f"ComfyUI not reachable at {base_url}. "
                "Make sure ComfyUI is running locally."
            )

        resp = await client.post(
            f"{base_url}/prompt",
            json={"prompt": workflow, "client_id": client_id},
            timeout=10,
        )
        resp.raise_for_status()
        prompt_id = resp.json()["prompt_id"]

        for _ in range(180):  # 3 min timeout for local gen
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
