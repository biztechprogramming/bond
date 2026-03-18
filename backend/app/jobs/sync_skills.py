"""Periodic job: sync skill submodules and re-index the catalog."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

logger = logging.getLogger(__name__)

# Project root — two levels up from backend/app/jobs/
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent


async def sync_skills(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Pull latest skill submodules, re-index, and optionally re-embed."""

    # Step 1: git submodule update
    vendor_dir = _PROJECT_ROOT / "vendor" / "skills"
    if vendor_dir.is_dir():
        logger.info("Updating skill submodules in %s", vendor_dir)
        proc = await asyncio.create_subprocess_exec(
            "git", "submodule", "update", "--remote", "vendor/skills/",
            cwd=str(_PROJECT_ROOT),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            logger.error(
                "git submodule update failed (rc=%d): %s",
                proc.returncode,
                stderr.decode(errors="replace"),
            )
        else:
            logger.info("Submodule update succeeded")
    else:
        logger.warning("Vendor skills directory not found: %s", vendor_dir)

    # Step 2: Re-index from filesystem into skills.json + SQLite
    try:
        from backend.app.agent.skills_indexer import index_all

        catalog = index_all(
            vendor_dir,
            [Path("skills/"), Path("~/.openclaw/skills/")],
        )

        # Write catalog
        catalog_path = _PROJECT_ROOT / "skills.json"
        import json
        catalog_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
        logger.info("Indexed %d skills to %s", len(catalog), catalog_path)

        # Load into SQLite
        from backend.app.agent.tools.skills_db import index_skills_from_json
        count = await index_skills_from_json(catalog_path)
        logger.info("Loaded %d skills into SQLite", count)

    except Exception:
        logger.exception("Failed to re-index skills")
