"""Skill catalog indexer — walks skill directories and produces a catalog.

Core indexing logic extracted from scripts/index-skills.py so it can be
imported by both the CLI script and the sync_skills cron job.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Frontmatter parser
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_YAML_KV_RE = re.compile(r'^(\w[\w-]*):\s*(.+)$', re.MULTILINE)


def parse_frontmatter(text: str) -> dict[str, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    block = m.group(1)
    result: dict[str, str] = {}
    for kv in _YAML_KV_RE.finditer(block):
        key = kv.group(1)
        val = kv.group(2).strip().strip('"').strip("'")
        result[key] = val
    return result


def first_sentence(text: str) -> str:
    text = text.strip()
    lines = text.split("\n")
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for end in (".", "!", "?"):
            idx = line.find(end)
            if idx > 0:
                return line[: idx + 1]
        return line[:200]
    return text[:200]


def extract_body_after_frontmatter(text: str) -> str:
    m = _FRONTMATTER_RE.match(text)
    if m:
        return text[m.end():]
    return text


def extract_l1_overview(text: str, max_chars: int = 2000) -> str:
    body = extract_body_after_frontmatter(text).strip()
    if len(body) <= max_chars:
        return body
    truncated = body[:max_chars]
    last_para = truncated.rfind("\n\n")
    if last_para > max_chars // 2:
        return truncated[:last_para].strip()
    return truncated.strip()


# ---------------------------------------------------------------------------
# Skill discovery
# ---------------------------------------------------------------------------

def discover_agentskills(base_dir: Path, source: str, source_type: str) -> list[dict]:
    skills = []
    for skill_md in sorted(base_dir.rglob("SKILL.md")):
        try:
            content = skill_md.read_text(encoding="utf-8")
        except Exception:
            continue
        fm = parse_frontmatter(content)
        name = fm.get("name") or skill_md.parent.name
        description = fm.get("description", "")
        skill_id = f"{source}/{name}"
        try:
            rel_path = str(skill_md.resolve().relative_to(Path.cwd()))
        except ValueError:
            rel_path = str(skill_md)
        skills.append({
            "id": skill_id,
            "name": name,
            "source": source,
            "source_type": source_type,
            "path": rel_path,
            "description": description,
            "l0_summary": first_sentence(description) if description else "",
            "l1_overview": extract_l1_overview(content),
        })
    return skills


def discover_superpowers_extras(base_dir: Path, source: str) -> list[dict]:
    extras = []
    for subdir in ("agents", "commands"):
        d = base_dir / subdir
        if not d.is_dir():
            continue
        for md_file in sorted(d.rglob("*.md")):
            if md_file.name.startswith("README"):
                continue
            try:
                content = md_file.read_text(encoding="utf-8")
            except Exception:
                continue
            fm = parse_frontmatter(content)
            name = fm.get("name") or md_file.stem
            description = fm.get("description", "")
            if not description:
                description = first_sentence(content)
            skill_id = f"{source}/{subdir}/{name}"
            try:
                rel_path = str(md_file.resolve().relative_to(Path.cwd()))
            except ValueError:
                rel_path = str(md_file)
            extras.append({
                "id": skill_id,
                "name": name,
                "source": source,
                "source_type": "submodule",
                "path": rel_path,
                "description": description,
                "l0_summary": first_sentence(description),
                "l1_overview": extract_l1_overview(content),
            })
    return extras


def index_all(vendor_dir: Path, local_dirs: list[Path]) -> list[dict]:
    """Build the full skill catalog from vendor submodules and local directories."""
    catalog: list[dict] = []

    if vendor_dir.is_dir():
        for source_dir in sorted(vendor_dir.iterdir()):
            if not source_dir.is_dir():
                continue
            source_name = source_dir.name
            skills_subdir = source_dir / "skills"
            if skills_subdir.is_dir():
                catalog.extend(discover_agentskills(skills_subdir, source_name, "submodule"))
            else:
                catalog.extend(discover_agentskills(source_dir, source_name, "submodule"))
            if source_name == "superpowers":
                catalog.extend(discover_superpowers_extras(source_dir, source_name))

    for local_dir in local_dirs:
        expanded = Path(os.path.expanduser(str(local_dir)))
        if expanded.is_dir():
            catalog.extend(discover_agentskills(expanded, "local", "local"))

    return catalog
