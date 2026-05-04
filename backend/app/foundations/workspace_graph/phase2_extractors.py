"""Phase 2 enhanced extractors — routes, tests, docs, config, migrations.

Design Doc 110, Phase 2: Regex/heuristic extractors that produce
GraphifyExtractionBatch objects for non-symbol workspace artifacts.
These run as the Bond-native fallback when Graphify is not available,
and can also supplement Graphify output with Bond-specific patterns.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_phase2_artifacts(
    root: Path,
    *,
    file_subset: list[str] | None = None,
) -> "GraphifyExtractionBatch":
    """Run all Phase 2 extractors on a workspace/repo root.

    Returns a GraphifyExtractionBatch with route, test, doc, config,
    and migration nodes plus their relationships.
    """
    from .adapter import ExtractionEdge, ExtractionNode, GraphifyExtractionBatch

    batch = GraphifyExtractionBatch()

    files = _enumerate_target_files(root, file_subset)

    for rel_path, abs_path in files:
        try:
            content = abs_path.read_text(errors="replace")
        except OSError:
            continue

        ext = abs_path.suffix.lower()
        name = abs_path.name.lower()

        # Routes
        _extract_routes(rel_path, content, ext, batch)

        # Tests
        _extract_tests(rel_path, content, ext, name, batch)

        # Docs
        _extract_docs(rel_path, content, ext, name, batch)

        # Config
        _extract_config(rel_path, content, ext, name, batch)

        # Migrations
        _extract_migrations(rel_path, content, ext, name, batch)

    return batch


# ---------------------------------------------------------------------------
# File enumeration
# ---------------------------------------------------------------------------

_SKIP_DIRS = {
    "node_modules", ".venv", "venv", "__pycache__", ".git", ".cache",
    "vendor", "dist", "build", ".tox", ".mypy_cache", ".pytest_cache",
    ".next", ".nuxt", "coverage",
}

_MAX_FILES = 5000


def _enumerate_target_files(
    root: Path,
    file_subset: list[str] | None,
) -> list[tuple[str, Path]]:
    """Return (relative_path, absolute_path) pairs."""
    if file_subset:
        return [
            (rel, root / rel)
            for rel in file_subset
            if (root / rel).is_file()
        ]

    results: list[tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            if fname.startswith(".") and not fname.startswith(".env"):
                continue
            abs_path = Path(dirpath) / fname
            rel_path = str(abs_path.relative_to(root))
            results.append((rel_path, abs_path))
            if len(results) >= _MAX_FILES:
                return results
    return results


# ---------------------------------------------------------------------------
# Route extraction
# ---------------------------------------------------------------------------

# FastAPI/Flask: @app.get("/path"), @router.post("/path"), etc.
_PYTHON_ROUTE_RE = re.compile(
    r'@\w+\.(get|post|put|delete|patch|options|head|route)\(\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# Express: app.get("/path", ...) or router.post("/path", ...)
_EXPRESS_ROUTE_RE = re.compile(
    r'(?:app|router)\.(get|post|put|delete|patch|options|head|all|use)\(\s*["\']([^"\']+)["\']',
    re.IGNORECASE,
)

# Next.js App Router: export async function GET/POST/etc in route.ts/js
_NEXTJS_ROUTE_RE = re.compile(
    r'export\s+(?:async\s+)?function\s+(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\b',
)


def _extract_routes(
    rel_path: str,
    content: str,
    ext: str,
    batch: "GraphifyExtractionBatch",
) -> None:
    from .adapter import ExtractionEdge, ExtractionNode

    patterns: list[tuple[re.Pattern, str]] = []
    if ext in (".py",):
        patterns.append((_PYTHON_ROUTE_RE, "python"))
    elif ext in (".js", ".ts", ".mjs", ".cjs"):
        patterns.append((_EXPRESS_ROUTE_RE, "javascript"))
        # Check for Next.js app router
        if os.path.basename(rel_path).startswith("route."):
            patterns.append((_NEXTJS_ROUTE_RE, "nextjs"))

    for pattern, framework in patterns:
        for match in pattern.finditer(content):
            method = match.group(1).upper()
            if framework == "nextjs":
                # Next.js: derive path from file path
                route_path = "/" + "/".join(
                    p for p in rel_path.replace("\\", "/").split("/")[:-1]
                    if p not in ("app", "src", "pages")
                )
                route_path = route_path.rstrip("/") or "/"
            else:
                route_path = match.group(2)

            line_num = content[:match.start()].count("\n") + 1
            node_id = f"route:{rel_path}:{method}:{route_path}"

            batch.nodes.append(ExtractionNode(
                id=node_id,
                label=f"{method} {route_path}",
                node_type="route",
                source_file=rel_path,
                source_location={"line_start": line_num},
                attributes={
                    "stable_key": node_id,
                    "method": method,
                    "path": route_path,
                    "framework": framework,
                },
            ))

            # route -> handles -> file (the file containing the route)
            file_node_id = f"file:{rel_path}"
            batch.edges.append(ExtractionEdge(
                source=node_id,
                target=file_node_id,
                relation="handles",
                attributes={"source_kind": "regex"},
            ))


# ---------------------------------------------------------------------------
# Test extraction
# ---------------------------------------------------------------------------

# pytest: def test_*, class Test*
_PYTEST_FUNC_RE = re.compile(r'^\s*(?:async\s+)?def\s+(test_\w+)\s*\(', re.MULTILINE)
_PYTEST_CLASS_RE = re.compile(r'^class\s+(Test\w+)\s*[:\(]', re.MULTILINE)

# JS: describe("...", ...) / it("...", ...) / test("...", ...)
_JS_TEST_RE = re.compile(
    r'(?:describe|it|test)\(\s*["\']([^"\']+)["\']',
)

# Go: func Test*(t *testing.T)
_GO_TEST_RE = re.compile(r'^func\s+(Test\w+)\s*\(\s*\w+\s+\*testing\.T\)', re.MULTILINE)


def _is_test_file(rel_path: str, ext: str, name: str) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    if any(p in ("test", "tests", "__tests__", "spec", "specs") for p in parts):
        return True
    if name.startswith("test_") or name.endswith(("_test.py", "_test.go", "_test.ts", "_test.js")):
        return True
    if name.endswith((".spec.ts", ".spec.js", ".test.ts", ".test.js")):
        return True
    return False


def _extract_tests(
    rel_path: str,
    content: str,
    ext: str,
    name: str,
    batch: "GraphifyExtractionBatch",
) -> None:
    from .adapter import ExtractionEdge, ExtractionNode

    if not _is_test_file(rel_path, ext, name):
        return

    found: list[tuple[str, int]] = []

    if ext == ".py":
        for m in _PYTEST_FUNC_RE.finditer(content):
            found.append((m.group(1), content[:m.start()].count("\n") + 1))
        for m in _PYTEST_CLASS_RE.finditer(content):
            found.append((m.group(1), content[:m.start()].count("\n") + 1))
    elif ext in (".js", ".ts", ".mjs", ".cjs", ".jsx", ".tsx"):
        for m in _JS_TEST_RE.finditer(content):
            found.append((m.group(1), content[:m.start()].count("\n") + 1))
    elif ext == ".go":
        for m in _GO_TEST_RE.finditer(content):
            found.append((m.group(1), content[:m.start()].count("\n") + 1))

    for test_name, line_num in found:
        node_id = f"test:{rel_path}::{test_name}"
        batch.nodes.append(ExtractionNode(
            id=node_id,
            label=test_name,
            node_type="test",
            source_file=rel_path,
            source_location={"line_start": line_num},
            attributes={
                "stable_key": node_id,
                "language": ext.lstrip("."),
            },
        ))

        # test -> tests -> file (the file being tested, heuristic)
        tested_file = _guess_tested_file(rel_path)
        if tested_file:
            batch.edges.append(ExtractionEdge(
                source=node_id,
                target=f"file:{tested_file}",
                relation="tests",
                confidence="INFERRED",
                attributes={"source_kind": "heuristic"},
            ))


def _guess_tested_file(test_path: str) -> str | None:
    """Heuristic: test_foo.py likely tests foo.py in a parallel path."""
    parts = test_path.replace("\\", "/").split("/")
    name = parts[-1]

    # Python: test_foo.py -> foo.py
    if name.startswith("test_") and name.endswith(".py"):
        candidate = name[5:]  # strip test_
        # Try replacing tests/ with src/ or removing tests/ dir
        new_parts = [p if p not in ("tests", "test") else "app" for p in parts[:-1]]
        return "/".join(new_parts + [candidate])

    # JS/TS: foo.test.ts -> foo.ts, foo.spec.ts -> foo.ts
    for suffix in (".test.ts", ".test.js", ".spec.ts", ".spec.js",
                    ".test.tsx", ".test.jsx", ".spec.tsx", ".spec.jsx"):
        if name.endswith(suffix):
            base_ext = suffix.split(".")[-1]
            candidate = name[: -len(suffix)] + "." + base_ext
            new_parts = [p for p in parts[:-1] if p not in ("__tests__", "test", "tests", "spec")]
            return "/".join(new_parts + [candidate])

    return None


# ---------------------------------------------------------------------------
# Document extraction
# ---------------------------------------------------------------------------

_HEADING_RE = re.compile(r'^#{1,3}\s+(.+)', re.MULTILINE)


def _extract_docs(
    rel_path: str,
    content: str,
    ext: str,
    name: str,
    batch: "GraphifyExtractionBatch",
) -> None:
    from .adapter import ExtractionNode

    if ext not in (".md", ".rst", ".txt", ".adoc"):
        return

    # Skip very large files
    if len(content) > 100_000:
        return

    node_id = f"document:{rel_path}"
    title = name

    # Try to extract first heading
    if ext == ".md":
        m = _HEADING_RE.search(content)
        if m:
            title = m.group(1).strip()

    batch.nodes.append(ExtractionNode(
        id=node_id,
        label=title,
        node_type="document",
        source_file=rel_path,
        attributes={
            "stable_key": node_id,
            "format": ext.lstrip("."),
        },
    ))


# ---------------------------------------------------------------------------
# Config extraction
# ---------------------------------------------------------------------------

_CONFIG_PATTERNS = {
    ".env", ".env.local", ".env.production", ".env.development",
}
_CONFIG_EXTENSIONS = {
    ".yaml", ".yml", ".toml", ".ini", ".cfg",
}
_CONFIG_NAMES = {
    "tsconfig.json", "package.json", "pyproject.toml", "setup.cfg",
    "setup.py", "cargo.toml", "go.mod", "Makefile", "Dockerfile",
    "docker-compose.yml", "docker-compose.yaml",
    ".eslintrc.json", ".prettierrc", "jest.config.js", "jest.config.ts",
    "webpack.config.js", "vite.config.ts", "vite.config.js",
    "next.config.js", "next.config.mjs", "next.config.ts",
}


def _extract_config(
    rel_path: str,
    content: str,
    ext: str,
    name: str,
    batch: "GraphifyExtractionBatch",
) -> None:
    from .adapter import ExtractionNode

    is_config = (
        name in _CONFIG_NAMES
        or name in _CONFIG_PATTERNS
        or ext in _CONFIG_EXTENSIONS
        or name.startswith(".env")
    )
    if not is_config:
        return

    node_id = f"config_key:{rel_path}"
    batch.nodes.append(ExtractionNode(
        id=node_id,
        label=name,
        node_type="config_key",
        source_file=rel_path,
        attributes={
            "stable_key": node_id,
            "format": ext.lstrip(".") if ext else name,
        },
    ))


# ---------------------------------------------------------------------------
# Migration extraction
# ---------------------------------------------------------------------------

_MIGRATION_DIR_NAMES = {
    "migrations", "migrate", "db", "alembic", "prisma", "drizzle",
    "knex", "sequelize",
}

_SQL_TABLE_RE = re.compile(
    r'CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`"\']?(\w+)[`"\']?',
    re.IGNORECASE,
)


def _extract_migrations(
    rel_path: str,
    content: str,
    ext: str,
    name: str,
    batch: "GraphifyExtractionBatch",
) -> None:
    from .adapter import ExtractionEdge, ExtractionNode

    parts = rel_path.replace("\\", "/").split("/")
    in_migration_dir = any(p.lower() in _MIGRATION_DIR_NAMES for p in parts)

    is_migration = (
        (in_migration_dir and ext in (".sql", ".py", ".js", ".ts"))
        or name.endswith((".up.sql", ".down.sql"))
        or "migration" in name.lower()
        or "migrate" in name.lower()
    )
    if not is_migration:
        return

    node_id = f"migration:{rel_path}"
    batch.nodes.append(ExtractionNode(
        id=node_id,
        label=name,
        node_type="migration",
        source_file=rel_path,
        attributes={
            "stable_key": node_id,
            "language": ext.lstrip(".") if ext else None,
        },
    ))

    # Extract table references from SQL migrations
    if ext == ".sql":
        for m in _SQL_TABLE_RE.finditer(content):
            table_name = m.group(1)
            table_node_id = f"table:{table_name}"
            line_num = content[:m.start()].count("\n") + 1

            # Ensure table node exists
            if not any(n.id == table_node_id for n in batch.nodes):
                batch.nodes.append(ExtractionNode(
                    id=table_node_id,
                    label=table_name,
                    node_type="table",
                    source_file=rel_path,
                    source_location={"line_start": line_num},
                    attributes={"stable_key": table_node_id},
                ))

            # migration -> writes_to -> table
            batch.edges.append(ExtractionEdge(
                source=node_id,
                target=table_node_id,
                relation="writes_to",
                attributes={"source_kind": "regex"},
            ))
