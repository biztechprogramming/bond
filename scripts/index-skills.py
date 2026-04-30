#!/usr/bin/env python3
"""Skill indexer CLI — walks skill directories and produces a skills.json catalog.

Usage:
    python scripts/index-skills.py [--output skills.json] [--embed] [--voyage-key KEY]

Core indexing logic lives in backend/app/agent/skills_indexer.py.
This script is a thin CLI wrapper.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Ensure backend is importable
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from backend.app.agent.skills_indexer import index_all


async def _embed_pipeline(catalog_path: Path, voyage_key: str | None) -> None:
    """Load catalog into SQLite and generate embeddings."""
    voyage_key = voyage_key or os.environ.get("VOYAGE_API_KEY", "")

    from backend.app.agent.tools.skills_db import index_skills_from_json
    from backend.app.agent.skills_embedder import embed_all_skills, _build_engine

    count = await index_skills_from_json(catalog_path)
    print(f"Loaded {count} skills into SQLite", file=sys.stderr)

    if not voyage_key:
        print("No Voyage API key — skipping embedding generation", file=sys.stderr)
        print("Set VOYAGE_API_KEY or use --voyage-key to enable embeddings", file=sys.stderr)
        return

    engine = _build_engine(voyage_key, "voyage-4-nano", 1024)
    embedded = await embed_all_skills(engine)
    print(f"Embedded {embedded} skills", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Index skills into a JSON catalog")
    parser.add_argument("--output", "-o", default="skills.json", help="Output file path")
    parser.add_argument("--vendor", default="vendor/skills", help="Vendor skills directory")
    parser.add_argument("--local", nargs="*", default=["skills/", "~/.openclaw/skills/"],
                        help="Local skill directories")
    parser.add_argument("--embed", action="store_true",
                        help="Also load into SQLite and generate embeddings")
    parser.add_argument("--voyage-key", help="Voyage API key (for --embed)")
    args = parser.parse_args()

    catalog = index_all(Path(args.vendor), [Path(p) for p in args.local])

    output_path = Path(args.output)
    output_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    print(f"Indexed {len(catalog)} skills → {output_path}", file=sys.stderr)

    if args.embed:
        asyncio.run(_embed_pipeline(output_path, args.voyage_key))


if __name__ == "__main__":
    main()
