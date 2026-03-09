"""Prompt management API — templates, AI generation, and manifest-based fragment listing.

Fragment CRUD and agent attachment endpoints have been removed (Doc 027 Phase 1).
Prompts live on the filesystem at ~/bond/prompts/, versioned in git.
Metadata lives in prompts/manifest.yaml.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from backend.app.api.deps import get_db

logger = logging.getLogger("bond.api.prompts")
router = APIRouter(prefix="/prompts", tags=["prompts"])


# ── Models ────────────────────────────────────────────────────────────────


class TemplateUpdate(BaseModel):
    display_name: str | None = None
    category: str | None = None
    content: str | None = None
    variables: list[str] | None = None
    description: str | None = None
    is_active: bool | None = None
    change_reason: str = ""


class GeneratePromptRequest(BaseModel):
    agent_name: str = ""
    agent_role: str = ""
    tools: str = ""
    responsibilities: str = ""


class ImprovePromptRequest(BaseModel):
    current_prompt: str
    agent_role: str = ""
    issues: str = ""


class GenerateFragmentRequest(BaseModel):
    purpose: str
    category: str = "behavior"
    target_agents: str = ""


class PreviewAssemblyRequest(BaseModel):
    system_prompt: str
    fragment_ids: list[str] = []


# ── Fragment CRUD ─────────────────────────────────────────────────────────
# REMOVED (Doc 027 Phase 1): Fragment CRUD endpoints have been removed.
# Prompt fragments are now files on disk at ~/bond/prompts/, versioned in git.
# Edit a prompt by editing the markdown file and committing.
# Metadata (tier, phase, utterances) lives in prompts/manifest.yaml.


@router.get("/fragments")
async def list_fragments():
    """List prompt fragments from the filesystem manifest.

    Returns fragment metadata from manifest.yaml. Content is on disk, not in a database.
    """
    from backend.app.agent.manifest import load_manifest
    prompts_dir = Path(__file__).parent.parent.parent.parent.parent / "prompts"
    manifest = load_manifest(prompts_dir)
    return [
        {
            "path": f.path,
            "tier": f.tier,
            "phase": f.phase,
            "utterances": f.utterances,
            "token_estimate": f.token_estimate,
        }
        for f in sorted(manifest.values(), key=lambda x: (x.tier, x.path))
    ]


# ── Template CRUD ─────────────────────────────────────────────────────────


@router.get("/templates")
async def list_templates(db: AsyncSession = Depends(get_db)):
    """List all prompt templates."""
    result = await db.execute(text(
        "SELECT pt.*, "
        "(SELECT MAX(version) FROM prompt_template_versions ptv WHERE ptv.template_id = pt.id) as version "
        "FROM prompt_templates pt ORDER BY pt.category, pt.display_name"
    ))
    rows = []
    for r in result.mappings().all():
        d = dict(r)
        d["variables"] = json.loads(d["variables"]) if isinstance(d["variables"], str) else d["variables"]
        rows.append(d)
    return rows


@router.get("/templates/{template_id}")
async def get_template(template_id: str, db: AsyncSession = Depends(get_db)):
    """Get a template with current content."""
    result = await db.execute(text(
        "SELECT pt.*, "
        "(SELECT MAX(version) FROM prompt_template_versions ptv WHERE ptv.template_id = pt.id) as version "
        "FROM prompt_templates pt WHERE pt.id = :id"
    ), {"id": template_id})
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")
    d = dict(row)
    d["variables"] = json.loads(d["variables"]) if isinstance(d["variables"], str) else d["variables"]
    return d


@router.put("/templates/{template_id}")
async def update_template(template_id: str, body: TemplateUpdate, db: AsyncSession = Depends(get_db)):
    """Update a template. If content changes, creates a new version."""
    result = await db.execute(text("SELECT id, content FROM prompt_templates WHERE id = :id"), {"id": template_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Template not found")

    old_content = row[1]
    updates = {}
    if body.display_name is not None:
        updates["display_name"] = body.display_name
    if body.category is not None:
        updates["category"] = body.category
    if body.content is not None:
        updates["content"] = body.content
    if body.variables is not None:
        updates["variables"] = json.dumps(body.variables)
    if body.description is not None:
        updates["description"] = body.description
    if body.is_active is not None:
        updates["is_active"] = 1 if body.is_active else 0

    if updates:
        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["id"] = template_id
        await db.execute(text(f"UPDATE prompt_templates SET {set_clause} WHERE id = :id"), updates)

    if body.content is not None and body.content != old_content:
        ver_result = await db.execute(text(
            "SELECT MAX(version) FROM prompt_template_versions WHERE template_id = :id"
        ), {"id": template_id})
        max_ver = ver_result.fetchone()[0] or 0

        ver_id = str(ULID())
        await db.execute(text(
            "INSERT INTO prompt_template_versions (id, template_id, version, content, change_reason, changed_by) "
            "VALUES (:id, :tmpl_id, :version, :content, :reason, 'user')"
        ), {
            "id": ver_id, "tmpl_id": template_id, "version": max_ver + 1,
            "content": body.content, "reason": body.change_reason or "Updated",
        })

    await db.commit()
    return await get_template(template_id, db)


@router.get("/templates/{template_id}/versions")
async def list_template_versions(template_id: str, db: AsyncSession = Depends(get_db)):
    """List version history for a template."""
    result = await db.execute(text(
        "SELECT * FROM prompt_template_versions WHERE template_id = :id ORDER BY version DESC"
    ), {"id": template_id})
    return [dict(r) for r in result.mappings().all()]


@router.post("/templates/{template_id}/rollback/{version}")
async def rollback_template(template_id: str, version: int, db: AsyncSession = Depends(get_db)):
    """Rollback a template to a specific version."""
    result = await db.execute(text(
        "SELECT content FROM prompt_template_versions WHERE template_id = :id AND version = :ver"
    ), {"id": template_id, "ver": version})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Version not found")

    old_content = row[0]
    await db.execute(text(
        "UPDATE prompt_templates SET content = :content WHERE id = :id"
    ), {"content": old_content, "id": template_id})

    ver_result = await db.execute(text(
        "SELECT MAX(version) FROM prompt_template_versions WHERE template_id = :id"
    ), {"id": template_id})
    max_ver = ver_result.fetchone()[0] or 0

    ver_id = str(ULID())
    await db.execute(text(
        "INSERT INTO prompt_template_versions (id, template_id, version, content, change_reason, changed_by) "
        "VALUES (:id, :tmpl_id, :version, :content, :reason, 'user')"
    ), {
        "id": ver_id, "tmpl_id": template_id, "version": max_ver + 1,
        "content": old_content, "reason": f"Rollback to version {version}",
    })

    await db.commit()
    return await get_template(template_id, db)


# ── Agent Fragment Attachment ─────────────────────────────────────────────
# REMOVED (Doc 027 Phase 1): Fragment attachment endpoints have been removed.
# Fragments are no longer attached to agents via checkboxes.
# Tier 1 fragments are always-on (system prompt), Tier 2 are lifecycle-triggered,
# Tier 3 are selected by semantic router. All from disk via manifest.yaml.


# ── Prompt Assembly & Preview ─────────────────────────────────────────────


@router.post("/agents/{agent_id}/preview")
async def preview_assembled_prompt(agent_id: str, db: AsyncSession = Depends(get_db)):
    """Preview the full assembled prompt for an agent (system prompt + Tier 1 fragments)."""
    from backend.app.agent.manifest import load_manifest, get_tier1_content

    # Get agent system prompt
    agent_result = await db.execute(text(
        "SELECT system_prompt FROM agents WHERE id = :id"
    ), {"id": agent_id})
    agent_row = agent_result.fetchone()
    if not agent_row:
        raise HTTPException(status_code=404, detail="Agent not found")

    prompts_dir = Path(__file__).parent.parent.parent.parent.parent / "prompts"
    manifest = load_manifest(prompts_dir)
    tier1_content = get_tier1_content(manifest)

    parts = [agent_row[0]]
    if tier1_content:
        parts.append(tier1_content)

    assembled = "\n\n".join(parts)
    tier1_count = sum(1 for f in manifest.values() if f.tier == 1)
    return {"assembled_prompt": assembled, "fragment_count": tier1_count, "total_chars": len(assembled)}


# ── AI Generation ─────────────────────────────────────────────────────────


@router.post("/generate/system-prompt")
async def generate_system_prompt(body: GeneratePromptRequest, db: AsyncSession = Depends(get_db)):
    """Use AI to generate a system prompt based on agent role and tools."""
    template = await _get_template_content(db, "prompt-generation")
    if not template:
        raise HTTPException(status_code=500, detail="Prompt generation template not found")

    prompt = template.format(
        agent_name=body.agent_name or "Agent",
        agent_role=body.agent_role or "General purpose assistant",
        tools=body.tools or "respond, search_memory, memory_save, file_read, file_write, code_execute",
        responsibilities=body.responsibilities or "Help the user with coding tasks",
    )

    result = await _call_llm(prompt)
    return {"generated_prompt": result}


@router.post("/generate/improve-prompt")
async def improve_prompt(body: ImprovePromptRequest, db: AsyncSession = Depends(get_db)):
    """Use AI to improve an existing system prompt."""
    template = await _get_template_content(db, "prompt-improvement")
    if not template:
        raise HTTPException(status_code=500, detail="Prompt improvement template not found")

    prompt = template.format(
        current_prompt=body.current_prompt,
        agent_role=body.agent_role or "General purpose assistant",
        issues=body.issues or "None specified",
    )

    result = await _call_llm(prompt)
    return {"improved_prompt": result}


@router.post("/generate/fragment")
async def generate_fragment(body: GenerateFragmentRequest, db: AsyncSession = Depends(get_db)):
    """Use AI to generate a prompt fragment."""
    template = await _get_template_content(db, "fragment-generation")
    if not template:
        raise HTTPException(status_code=500, detail="Fragment generation template not found")

    prompt = template.format(
        purpose=body.purpose,
        category=body.category,
        target_agents=body.target_agents or "All agents",
    )

    result = await _call_llm(prompt)
    return {"generated_fragment": result}


# ── Helpers ───────────────────────────────────────────────────────────────


async def _get_template_content(db: AsyncSession, name: str) -> str | None:
    """Load a prompt template by name."""
    result = await db.execute(text(
        "SELECT content FROM prompt_templates WHERE name = :name AND is_active = 1"
    ), {"name": name})
    row = result.fetchone()
    return row[0] if row else None


async def _call_llm(prompt: str) -> str:
    """Call the LLM for prompt generation. Uses the vault for API keys."""
    import litellm
    from backend.app.core.vault import Vault

    vault = Vault()
    api_key = vault.get_api_key("anthropic")

    extra = {}
    if api_key:
        extra["api_key"] = api_key

    response = await litellm.acompletion(
        model="anthropic/claude-sonnet-4-20250514",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
        max_tokens=4096,
        **extra,
    )

    return response.choices[0].message.content or ""
