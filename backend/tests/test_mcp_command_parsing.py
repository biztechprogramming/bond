"""Tests for MCP command parsing (Design Doc 106)."""
import pytest
from backend.app.mcp.manager import parse_command


class TestParseCommand:
    """Test parse_command() auto-splitting behavior."""

    def test_simple_command_no_args(self):
        """Single word command with no args stays as-is."""
        cmd, args = parse_command("node", [])
        assert cmd == "node"
        assert args == []

    def test_full_command_line_auto_split(self):
        """Full command line is auto-split when args is empty."""
        cmd, args = parse_command("node /path/to/script.js -s user", [])
        assert cmd == "node"
        assert args == ["/path/to/script.js", "-s", "user"]

    def test_quoted_path_with_spaces(self):
        """Quoted paths with spaces are preserved."""
        cmd, args = parse_command('node "/path/to/my script.js" --flag', [])
        assert cmd == "node"
        assert args == ["/path/to/my script.js", "--flag"]

    def test_already_split_not_modified(self):
        """When args are already provided, command is not modified."""
        cmd, args = parse_command("node /path/to/script.js -s user", ["-s", "user"])
        assert cmd == "node /path/to/script.js -s user"
        assert args == ["-s", "user"]

    def test_windows_path(self):
        """Windows-style paths are handled correctly."""
        cmd, args = parse_command("node /mnt/c/dev/automation/solidtime/mcp-solidtime/dist/index.js -s user", [])
        assert cmd == "node"
        assert args == ["/mnt/c/dev/automation/solidtime/mcp-solidtime/dist/index.js", "-s", "user"]

    def test_empty_command(self):
        """Empty command returns empty."""
        cmd, args = parse_command("", [])
        assert cmd == ""
        assert args == []

    def test_whitespace_only_command(self):
        """Whitespace-only command returns empty."""
        cmd, args = parse_command("   ", [])
        assert cmd == ""
        assert args == []

    def test_npx_command(self):
        """npx-style commands split correctly."""
        cmd, args = parse_command("npx -y @modelcontextprotocol/server-github", [])
        assert cmd == "npx"
        assert args == ["-y", "@modelcontextprotocol/server-github"]

    def test_uvx_command(self):
        """uvx-style commands split correctly."""
        cmd, args = parse_command("uvx mcp-server-fetch", [])
        assert cmd == "uvx"
        assert args == ["mcp-server-fetch"]

    def test_command_with_equals_args(self):
        """Commands with --key=value args split correctly."""
        cmd, args = parse_command("node server.js --port=3000 --host=localhost", [])
        assert cmd == "node"
        assert args == ["server.js", "--port=3000", "--host=localhost"]
