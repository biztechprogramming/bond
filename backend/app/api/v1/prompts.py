"""Prompt management API — fragments, templates, versioning, AI generation."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from ulid import ULID

from backend.app.api.deps import get_db

logger = logging.getLogger("bond.api.prompts")
router = APIRouter(prefix="/prompts", tags=["prompts"])


# ── Models ────────────────────────────────────────────────────────────────


class FragmentCreate(BaseModel):
    name: str
    display_name: str
    category: str
    content: str
    description: str = ""
    summary: str = ""
    tier: str = "standard"
    task_triggers: list[str] = []


class FragmentUpdate(BaseModel):
    display_name: str | None = None
    category: str | None = None
    content: str | None = None
    description: str | None = None
    is_active: bool | None = None
    summary: str | None = None
    tier: str | None = None
    task_triggers: list[str] | None = None
    change_reason: str = ""


class TemplateUpdate(BaseModel):
    display_name: str | None = None
    category: str | None = None
    content: str | None = None
    variables: list[str] | None = None
    description: str | None = None
    is_active: bool | None = None
    change_reason: str = ""


class AttachFragment(BaseModel):
    fragment_id: str
    rank: int = 0
    enabled: bool = True


class UpdateAttachment(BaseModel):
    rank: int | None = None
    enabled: bool | None = None


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


@router.get("/fragments")
async def list_fragments(db: AsyncSession = Depends(get_db)):
    """List all prompt fragments with agent usage counts."""
    result = await db.execute(text(
        "SELECT pf.*, "
        "(SELECT COUNT(*) FROM agent_prompt_fragments apf WHERE apf.fragment_id = pf.id) as agent_count, "
        "(SELECT MAX(version) FROM prompt_fragment_versions pfv WHERE pfv.fragment_id = pf.id) as version "
        "FROM prompt_fragments pf ORDER BY pf.category, pf.display_name"
    ))
    return [dict(r) for r in result.mappings().all()]


@router.post("/fragments")
async def create_fragment(body: FragmentCreate, db: AsyncSession = Depends(get_db)):
    """Create a new prompt fragment."""
    valid_categories = {"behavior", "tools", "safety", "context"}
    if body.category not in valid_categories:
        raise HTTPException(status_code=400, detail=f"Invalid category. Must be one of: {valid_categories}")

    frag_id = str(ULID())
    ver_id = str(ULID())

    token_estimate = len(body.content) // 4
    await db.execute(text(
        "INSERT INTO prompt_fragments (id, name, display_name, category, content, description, "
        "summary, tier, task_triggers, token_estimate, is_system) "
        "VALUES (:id, :name, :display_name, :category, :content, :description, "
        ":summary, :tier, :task_triggers, :token_estimate, 0)"
    ), {
        "id": frag_id, "name": body.name, "display_name": body.display_name,
        "category": body.category, "content": body.content, "description": body.description,
        "summary": body.summary, "tier": body.tier,
        "task_triggers": json.dumps(body.task_triggers), "token_estimate": token_estimate,
    })

    await db.execute(text(
        "INSERT INTO prompt_fragment_versions (id, fragment_id, version, content, change_reason, changed_by) "
        "VALUES (:id, :frag_id, 1, :content, 'Initial creation', 'user')"
    ), {"id": ver_id, "frag_id": frag_id, "content": body.content})

    await db.commit()
    return {"id": frag_id, "name": body.name}


@router.get("/fragments/{fragment_id}")
async def get_fragment(fragment_id: str, db: AsyncSession = Depends(get_db)):
    """Get a fragment with its current content."""
    result = await db.execute(text(
        "SELECT pf.*, "
        "(SELECT COUNT(*) FROM agent_prompt_fragments apf WHERE apf.fragment_id = pf.id) as agent_count, "
        "(SELECT MAX(version) FROM prompt_fragment_versions pfv WHERE pfv.fragment_id = pf.id) as version "
        "FROM prompt_fragments pf WHERE pf.id = :id"
    ), {"id": fragment_id})
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Fragment not found")
    return dict(row)


@router.put("/fragments/{fragment_id}")
async def update_fragment(fragment_id: str, body: FragmentUpdate, db: AsyncSession = Depends(get_db)):
    """Update a fragment. If content changes, creates a new version."""
    # Check exists
    result = await db.execute(text("SELECT id, content FROM prompt_fragments WHERE id = :id"), {"id": fragment_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Fragment not found")

    old_content = row[1]
    updates = {}
    if body.display_name is not None:
        updates["display_name"] = body.display_name
    if body.category is not None:
        valid_categories = {"behavior", "tools", "safety", "context"}
        if body.category not in valid_categories:
            raise HTTPException(status_code=400, detail=f"Invalid category. Must be one of: {valid_categories}")
        updates["category"] = body.category
    if body.content is not None:
        updates["content"] = body.content
        updates["token_estimate"] = len(body.content) // 4
    if body.description is not None:
        updates["description"] = body.description
    if body.is_active is not None:
        updates["is_active"] = 1 if body.is_active else 0
    if body.summary is not None:
        updates["summary"] = body.summary
    if body.tier is not None:
        valid_tiers = {"core", "standard", "specialized"}
        if body.tier not in valid_tiers:
            raise HTTPException(status_code=400, detail=f"Invalid tier. Must be one of: {valid_tiers}")
        updates["tier"] = body.tier
    if body.task_triggers is not None:
        updates["task_triggers"] = json.dumps(body.task_triggers)

    if updates:
        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["id"] = fragment_id
        await db.execute(text(f"UPDATE prompt_fragments SET {set_clause} WHERE id = :id"), updates)

    # Version tracking for content changes
    if body.content is not None and body.content != old_content:
        ver_result = await db.execute(text(
            "SELECT MAX(version) FROM prompt_fragment_versions WHERE fragment_id = :id"
        ), {"id": fragment_id})
        max_ver = ver_result.fetchone()[0] or 0

        ver_id = str(ULID())
        await db.execute(text(
            "INSERT INTO prompt_fragment_versions (id, fragment_id, version, content, change_reason, changed_by) "
            "VALUES (:id, :frag_id, :version, :content, :reason, 'user')"
        ), {
            "id": ver_id, "frag_id": fragment_id, "version": max_ver + 1,
            "content": body.content, "reason": body.change_reason or "Updated",
        })

    await db.commit()
    return await get_fragment(fragment_id, db)


@router.delete("/fragments/{fragment_id}")
async def delete_fragment(fragment_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a fragment. Fails if is_system."""
    result = await db.execute(text(
        "SELECT is_system FROM prompt_fragments WHERE id = :id"
    ), {"id": fragment_id})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Fragment not found")
    if row[0]:
        raise HTTPException(status_code=400, detail="Cannot delete system fragments. Disable it instead.")

    await db.execute(text("DELETE FROM prompt_fragments WHERE id = :id"), {"id": fragment_id})
    await db.commit()
    return {"deleted": True}


@router.get("/fragments/{fragment_id}/versions")
async def list_fragment_versions(fragment_id: str, db: AsyncSession = Depends(get_db)):
    """List version history for a fragment."""
    result = await db.execute(text(
        "SELECT * FROM prompt_fragment_versions WHERE fragment_id = :id ORDER BY version DESC"
    ), {"id": fragment_id})
    return [dict(r) for r in result.mappings().all()]


@router.post("/fragments/{fragment_id}/rollback/{version}")
async def rollback_fragment(fragment_id: str, version: int, db: AsyncSession = Depends(get_db)):
    """Rollback a fragment to a specific version."""
    result = await db.execute(text(
        "SELECT content FROM prompt_fragment_versions WHERE fragment_id = :id AND version = :ver"
    ), {"id": fragment_id, "ver": version})
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Version not found")

    old_content = row[0]

    # Update current content
    await db.execute(text(
        "UPDATE prompt_fragments SET content = :content WHERE id = :id"
    ), {"content": old_content, "id": fragment_id})

    # Create a new version entry for the rollback
    ver_result = await db.execute(text(
        "SELECT MAX(version) FROM prompt_fragment_versions WHERE fragment_id = :id"
    ), {"id": fragment_id})
    max_ver = ver_result.fetchone()[0] or 0

    ver_id = str(ULID())
    await db.execute(text(
        "INSERT INTO prompt_fragment_versions (id, fragment_id, version, content, change_reason, changed_by) "
        "VALUES (:id, :frag_id, :version, :content, :reason, 'user')"
    ), {
        "id": ver_id, "frag_id": fragment_id, "version": max_ver + 1,
        "content": old_content, "reason": f"Rollback to version {version}",
    })

    await db.commit()
    return await get_fragment(fragment_id, db)


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


@router.get("/agents/{agent_id}/fragments")
async def list_agent_fragments(agent_id: str, db: AsyncSession = Depends(get_db)):
    """List fragments attached to an agent, ordered by rank."""
    result = await db.execute(text(
        "SELECT apf.id as attachment_id, apf.rank, apf.enabled, "
        "pf.id, pf.name, pf.display_name, pf.category, pf.content, pf.is_active, "
        "pf.summary, pf.tier, pf.task_triggers, pf.token_estimate "
        "FROM agent_prompt_fragments apf "
        "JOIN prompt_fragments pf ON pf.id = apf.fragment_id "
        "WHERE apf.agent_id = :agent_id "
        "ORDER BY apf.rank"
    ), {"agent_id": agent_id})
    return [dict(r) for r in result.mappings().all()]


@router.post("/agents/{agent_id}/fragments")
async def attach_fragment(agent_id: str, body: AttachFragment, db: AsyncSession = Depends(get_db)):
    """Attach a fragment to an agent."""
    # Verify both exist
    agent_check = await db.execute(text("SELECT id FROM agents WHERE id = :id"), {"id": agent_id})
    if not agent_check.fetchone():
        raise HTTPException(status_code=404, detail="Agent not found")

    frag_check = await db.execute(text("SELECT id FROM prompt_fragments WHERE id = :id"), {"id": body.fragment_id})
    if not frag_check.fetchone():
        raise HTTPException(status_code=404, detail="Fragment not found")

    # Check not already attached
    existing = await db.execute(text(
        "SELECT id FROM agent_prompt_fragments WHERE agent_id = :aid AND fragment_id = :fid"
    ), {"aid": agent_id, "fid": body.fragment_id})
    if existing.fetchone():
        raise HTTPException(status_code=400, detail="Fragment already attached to this agent")

    apf_id = str(ULID())
    await db.execute(text(
        "INSERT INTO agent_prompt_fragments (id, agent_id, fragment_id, rank, enabled) "
        "VALUES (:id, :aid, :fid, :rank, :enabled)"
    ), {
        "id": apf_id, "aid": agent_id, "fid": body.fragment_id,
        "rank": body.rank, "enabled": 1 if body.enabled else 0,
    })
    await db.commit()
    return {"id": apf_id}


@router.put("/agents/{agent_id}/fragments/{fragment_id}")
async def update_attachment(agent_id: str, fragment_id: str, body: UpdateAttachment, db: AsyncSession = Depends(get_db)):
    """Update rank or enabled status of an attached fragment."""
    updates = {}
    if body.rank is not None:
        updates["rank"] = body.rank
    if body.enabled is not None:
        updates["enabled"] = 1 if body.enabled else 0

    if updates:
        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["aid"] = agent_id
        updates["fid"] = fragment_id
        result = await db.execute(text(
            f"UPDATE agent_prompt_fragments SET {set_clause} WHERE agent_id = :aid AND fragment_id = :fid"
        ), updates)
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Attachment not found")

    await db.commit()
    return {"updated": True}


@router.delete("/agents/{agent_id}/fragments/{fragment_id}")
async def detach_fragment(agent_id: str, fragment_id: str, db: AsyncSession = Depends(get_db)):
    """Detach a fragment from an agent."""
    result = await db.execute(text(
        "DELETE FROM agent_prompt_fragments WHERE agent_id = :aid AND fragment_id = :fid"
    ), {"aid": agent_id, "fid": fragment_id})
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Attachment not found")
    await db.commit()
    return {"detached": True}


# ── Prompt Assembly & Preview ─────────────────────────────────────────────


@router.post("/agents/{agent_id}/preview")
async def preview_assembled_prompt(agent_id: str, db: AsyncSession = Depends(get_db)):
    """Preview the full assembled prompt for an agent."""
    # Get agent system prompt
    agent_result = await db.execute(text(
        "SELECT system_prompt FROM agents WHERE id = :id"
    ), {"id": agent_id})
    agent_row = agent_result.fetchone()
    if not agent_row:
        raise HTTPException(status_code=404, detail="Agent not found")

    parts = [agent_row[0]]

    # Get active, enabled fragments ordered by rank
    frag_result = await db.execute(text(
        "SELECT pf.content FROM agent_prompt_fragments apf "
        "JOIN prompt_fragments pf ON pf.id = apf.fragment_id "
        "WHERE apf.agent_id = :aid AND apf.enabled = 1 AND pf.is_active = 1 "
        "ORDER BY apf.rank"
    ), {"aid": agent_id})

    for row in frag_result.fetchall():
        parts.append(row[0])

    assembled = "\n\n".join(parts)
    return {"assembled_prompt": assembled, "fragment_count": len(parts) - 1, "total_chars": len(assembled)}


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
