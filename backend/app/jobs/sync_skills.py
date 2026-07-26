"""Periodic job: sync skill submodules and re-index the catalog."""

from __future__ import annotations

import asyncio
import json
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
        # --init is required: the vendored skill submodules are not registered
        # in a fresh clone's .git/config, and `git submodule update` (without
        # --init) silently SKIPS uninitialized submodules and still returns 0.
        # Without this the checkout stays empty, index_all() finds no vendored
        # skills, and the catalog gets wiped down to local-only. See the guard
        # in Step 2 for the second line of defense.
        proc = await asyncio.create_subprocess_exec(
            "git", "submodule", "update", "--init", "--remote", "vendor/skills/",
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

        catalog_path = _PROJECT_ROOT / "skills.json"

        # Guard against silently wiping the federated catalog. If this re-index
        # produced zero vendored skills (e.g. the submodule checkout above
        # failed on a network hiccup or force-push) but the existing catalog on
        # disk has some, refuse to overwrite — a local-only catalog would strip
        # every agent down to the handful of first-party skills until the next
        # successful sync. Keep the last-good catalog instead and alert.
        vendored = [s for s in catalog if s.get("source_type") != "local"]
        if not vendored and catalog_path.exists():
            try:
                prev = json.loads(catalog_path.read_text(encoding="utf-8"))
            except Exception:
                prev = []
            prev_vendored = [s for s in prev if s.get("source_type") != "local"]
            if prev_vendored:
                logger.error(
                    "Skill re-index produced 0 vendored skills but the existing "
                    "catalog has %d — refusing to overwrite (submodule checkout "
                    "likely failed). Keeping the last-good catalog.",
                    len(prev_vendored),
                )
                return

        # Write catalog
        catalog_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
        logger.info("Indexed %d skills to %s", len(catalog), catalog_path)

        # Load into SQLite. Target the *shared* agent-data database — the one
        # bind-mounted into agent containers at /data/skills.db (adapters.py) —
        # not this indexer process's own data dir. Those are different paths
        # when bond runs containerized (BOND_HOST_HOME set): the indexer's
        # _PROJECT_ROOT/data is /app/data (unmounted, ephemeral), while agents
        # read $BOND_HOST_HOME/.bond/agent-data/skills.db. Writing to the wrong
        # one leaves every agent with an empty skill_index.
        from backend.app.agent.tools.skills_db import index_skills_from_json
        from backend.app.sandbox.adapters import _agent_bind_data_root

        shared_db = _agent_bind_data_root() / "skills.db"
        count = await index_skills_from_json(catalog_path, db_path=shared_db)
        logger.info("Loaded %d skills into shared SQLite at %s", count, shared_db)

    except Exception:
        logger.exception("Failed to re-index skills")
