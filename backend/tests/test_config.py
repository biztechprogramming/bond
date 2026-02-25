"""Tests for config loading."""

from __future__ import annotations

import json
import os

from backend.app.config import Settings, _deep_merge, get_settings, load_bond_json


def test_deep_merge_basic():
    base = {"a": 1, "b": {"c": 2, "d": 3}}
    overlay = {"b": {"c": 99}, "e": 5}
    result = _deep_merge(base, overlay)
    assert result == {"a": 1, "b": {"c": 99, "d": 3}, "e": 5}


def test_deep_merge_no_mutation():
    base = {"a": {"b": 1}}
    overlay = {"a": {"c": 2}}
    _deep_merge(base, overlay)
    assert base == {"a": {"b": 1}}


def test_load_bond_json_defaults():
    config = load_bond_json()
    assert "llm" in config
    assert config["llm"]["provider"] == "anthropic"


def test_get_settings_returns_settings():
    settings = get_settings()
    assert isinstance(settings, Settings)
    assert settings.backend_port == 18790
    assert settings.llm_provider == "anthropic"


def test_get_settings_env_override():
    os.environ["BOND_BACKEND_PORT"] = "9999"
    try:
        from backend.app.config import get_settings as gs

        gs.cache_clear()
        s = gs()
        assert s.backend_port == 9999
    finally:
        os.environ.pop("BOND_BACKEND_PORT", None)


def test_load_bond_json_with_file(tmp_path):
    bond_json = tmp_path / "bond.json"
    bond_json.write_text(json.dumps({"llm": {"provider": "openai"}}))

    import backend.app.config as cfg

    original = cfg.BOND_JSON_PATH
    cfg.BOND_JSON_PATH = bond_json
    try:
        config = load_bond_json()
        assert config["llm"]["provider"] == "openai"
        # Default model should still be there from merge
        assert config["llm"]["model"] == "claude-sonnet-4-20250514"
    finally:
        cfg.BOND_JSON_PATH = original
