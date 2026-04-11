"""Tests for the database capability layer (Design Doc 109)."""

import pytest

from backend.app.mcp.database_capability import (
    BOND_TO_FAUCET,
    DATABASE_TOOL_DEFINITIONS,
    DATABASE_TOOL_MAP,
    FULL_CONTROL_TOOLS,
    READ_ONLY_TOOLS,
    ResolvedDatabaseAssignment,
    _normalize,
    fuzzy_resolve,
    get_effective_tools,
)


# ── Normalization ───────────────────────────────────────────────

class TestNormalize:
    def test_lowercase(self):
        assert _normalize("Resume Prettier") == "resume prettier"

    def test_collapse_punctuation(self):
        assert _normalize("resume-prettier") == "resume prettier"
        assert _normalize("resume_prettier") == "resume prettier"

    def test_collapse_multiple_spaces(self):
        assert _normalize("resume   prettier") == "resume prettier"


# ── Fuzzy resolution ────────────────────────────────────────────

def _make_assignment(name: str, tier: str = "read_only", db_id: str = "") -> ResolvedDatabaseAssignment:
    return ResolvedDatabaseAssignment(
        database_id=db_id or f"db_{_normalize(name).replace(' ', '_')}",
        database_name=name,
        driver="postgres",
        status="active",
        access_tier=tier,
        faucet_role=f"role_{name}",
    )


class TestFuzzyResolve:
    def test_exact_id(self):
        a = _make_assignment("analytics", db_id="db_analytics")
        result = fuzzy_resolve("db_analytics", [a])
        assert result is a

    def test_exact_name(self):
        a = _make_assignment("analytics")
        result = fuzzy_resolve("analytics", [a])
        assert result is a

    def test_case_insensitive(self):
        a = _make_assignment("analytics")
        result = fuzzy_resolve("Analytics", [a])
        assert result is a

    def test_prefix_match(self):
        a = _make_assignment("analytics_prod")
        result = fuzzy_resolve("analytics", [a])
        assert result is a

    def test_ambiguous(self):
        a1 = _make_assignment("analytics_prod")
        a2 = _make_assignment("analytics_staging")
        result = fuzzy_resolve("analytics", [a1, a2])
        assert isinstance(result, str)
        assert "Ambiguous" in result

    def test_no_match(self):
        a = _make_assignment("analytics")
        result = fuzzy_resolve("billing", [a])
        assert isinstance(result, str)
        assert "No attached database" in result

    def test_empty_assignments(self):
        result = fuzzy_resolve("anything", [])
        assert isinstance(result, str)
        assert "No databases" in result


# ── Effective tools ─────────────────────────────────────────────

class TestEffectiveTools:
    def test_no_assignments(self):
        assert get_effective_tools([]) == []

    def test_read_only(self):
        a = _make_assignment("db1", "read_only")
        tools = get_effective_tools([a])
        names = {t["function"]["name"] for t in tools}
        assert names == READ_ONLY_TOOLS

    def test_full_control(self):
        a = _make_assignment("db1", "full_control")
        tools = get_effective_tools([a])
        names = {t["function"]["name"] for t in tools}
        assert names == FULL_CONTROL_TOOLS

    def test_mixed_tiers_broadest_wins(self):
        a1 = _make_assignment("db1", "read_only")
        a2 = _make_assignment("db2", "full_control")
        tools = get_effective_tools([a1, a2])
        names = {t["function"]["name"] for t in tools}
        assert names == FULL_CONTROL_TOOLS


# ── Tool definitions integrity ──────────────────────────────────

class TestToolDefinitions:
    def test_all_bond_tools_have_definitions(self):
        for tool_name in BOND_TO_FAUCET:
            assert tool_name in DATABASE_TOOL_MAP, f"Missing definition for {tool_name}"

    def test_definitions_have_function_key(self):
        for d in DATABASE_TOOL_DEFINITIONS:
            assert "type" in d
            assert d["type"] == "function"
            assert "function" in d
            assert "name" in d["function"]

    def test_read_only_subset_of_full(self):
        assert READ_ONLY_TOOLS < FULL_CONTROL_TOOLS


# ── Assignment allowed_tools ────────────────────────────────────

class TestResolvedAssignment:
    def test_read_only_tools(self):
        a = _make_assignment("db1", "read_only")
        assert a.allowed_tools == READ_ONLY_TOOLS

    def test_full_control_tools(self):
        a = _make_assignment("db1", "full_control")
        assert a.allowed_tools == FULL_CONTROL_TOOLS

    def test_normalized_names(self):
        a = _make_assignment("Resume Prettier")
        assert "resume prettier" in a.normalized_names
        assert "resumeprettier" in a.normalized_names
        assert "resume-prettier" in a.normalized_names
