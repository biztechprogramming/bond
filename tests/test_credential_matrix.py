"""Credential matrix test: discover which auth mechanisms actually work for Claude CLI.

This is a DISCOVERY test — the "expected" results are unknown. Run it to find out
which credential combinations succeed in headless containers, then use the results
to drive the design of the unified credential flow (Design Doc 059).

Each test starts a fresh container with a specific credential combination, runs
`claude --print "Respond with exactly PONG"`, and records success/failure.

Requirements:
  - Docker running
  - bond-agent-worker:latest image built
  - At least one of: ANTHROPIC_API_KEY env var, ~/.claude/.credentials.json

Run (standalone — no backend deps needed):
  python3 backend/tests/test_credential_matrix.py

Or via pytest (requires backend deps):
  pytest backend/tests/test_credential_matrix.py -v -s --tb=short --override-ini="confcutdir=backend/tests/test_credential_matrix.py"
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# Allow standalone execution without pytest
try:
    import pytest
except ImportError:
    pytest = None  # type: ignore

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

IMAGE = "bond-agent-worker:latest"
CONTAINER_PREFIX = "bond-cred-test"
CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
CLAUDE_JSON_PATH = Path.home() / ".claude.json"
SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
PROMPT = "Respond with exactly the word PONG and nothing else."
TIMEOUT = 90  # seconds per test — Claude CLI can be slow to start


def _docker_available() -> bool:
    try:
        return subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10,
        ).returncode == 0
    except Exception:
        return False


def _image_exists() -> bool:
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", IMAGE],
            capture_output=True, timeout=10,
        )
        return result.returncode == 0
    except Exception:
        return False


GATEWAY_URL = os.environ.get("BOND_GATEWAY_URL", "http://localhost:18789")


def _get_api_key() -> str | None:
    """Get ANTHROPIC_API_KEY from env, gateway API, or vault.

    Tries multiple sources in priority order to stay dependency-free.
    """
    key = os.environ.get("ANTHROPIC_API_KEY")
    if key:
        return key

    # Try gateway persistence API (production path)
    try:
        import urllib.request
        req = urllib.request.Request(f"{GATEWAY_URL}/api/v1/provider-api-keys/anthropic")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            val = data.get("encryptedValue", "")
            if val:
                return val
    except Exception:
        pass

    # Try reading from vault credentials file directly
    bond_home = Path(os.environ.get("BOND_HOME", Path.home() / ".bond"))
    creds_file = bond_home / "data" / "credentials.json"
    if creds_file.exists():
        try:
            data = json.loads(creds_file.read_text())
            for k in ("anthropic_api_key", "ANTHROPIC_API_KEY", "anthropic"):
                if data.get(k):
                    return data[k]
        except Exception:
            pass

    return None


def _has_valid_oauth() -> bool:
    if not CREDENTIALS_PATH.exists():
        return False
    try:
        data = json.loads(CREDENTIALS_PATH.read_text())
        oauth = data.get("claudeAiOauth", {})
        if not oauth.get("accessToken"):
            return False
        expires_at = oauth.get("expiresAt", 0)
        # Token valid if expires > 5 minutes from now
        return (expires_at / 1000) > (time.time() + 300)
    except Exception:
        return False


def _make_expired_credentials() -> str:
    """Create a temp file with expired OAuth credentials for testing."""
    if not CREDENTIALS_PATH.exists():
        return ""
    data = json.loads(CREDENTIALS_PATH.read_text())
    oauth = data.get("claudeAiOauth", {})
    if oauth:
        # Set expiry to the past
        oauth["expiresAt"] = int((time.time() - 3600) * 1000)
        # Mangle the access token so it's definitely invalid
        if oauth.get("accessToken"):
            oauth["accessToken"] = "expired_" + oauth["accessToken"][:20] + "_invalid"
        data["claudeAiOauth"] = oauth

    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, prefix="cred-expired-")
    json.dump(data, tmp)
    tmp.close()
    return tmp.name


def _make_invalid_api_key() -> str:
    return "sk-ant-invalid-key-for-testing-00000000000000000000"


if pytest:
    skip_no_docker = pytest.mark.skipif(not _docker_available(), reason="Docker not available")
    skip_no_image = pytest.mark.skipif(not _image_exists(), reason=f"{IMAGE} not built")
else:
    # No-op decorators for standalone mode
    def _noop(cls_or_fn):
        return cls_or_fn
    skip_no_docker = _noop
    skip_no_image = _noop


# ---------------------------------------------------------------------------
# Helper: run Claude CLI in a container with specific credentials
# ---------------------------------------------------------------------------

def _run_claude_in_container(
    test_name: str,
    api_key: str | None = None,
    mount_oauth: bool = False,
    oauth_path: str | None = None,
    mount_claude_json: bool = False,
    mount_settings: bool = False,
    extra_env: dict[str, str] | None = None,
) -> dict:
    """Run claude --print in a fresh container with the given credential setup.

    Returns {"success": bool, "stdout": str, "stderr": str, "exit_code": int, "duration": float}
    """
    container_name = f"{CONTAINER_PREFIX}-{test_name}"

    # Clean up any stale container
    subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

    # Build docker run command — override entrypoint to skip repo clone
    cmd = [
        "docker", "run", "--rm",
        "--name", container_name,
        "--entrypoint", "claude",
        "--network", "host",
    ]

    # Environment
    if api_key:
        cmd.extend(["-e", f"ANTHROPIC_API_KEY={api_key}"])

    # Explicitly unset ANTHROPIC_API_KEY if not provided (ensure clean test)
    if not api_key:
        cmd.extend(["-e", "ANTHROPIC_API_KEY="])

    if extra_env:
        for k, v in extra_env.items():
            cmd.extend(["-e", f"{k}={v}"])

    # Credential file mounts
    cred_source = oauth_path or str(CREDENTIALS_PATH)
    if mount_oauth and Path(cred_source).exists():
        cmd.extend(["-v", f"{cred_source}:/home/bond-agent/.claude/.credentials.json:ro"])

    if mount_claude_json and CLAUDE_JSON_PATH.exists():
        cmd.extend(["-v", f"{CLAUDE_JSON_PATH}:/home/bond-agent/.claude.json:ro"])

    if mount_settings and SETTINGS_PATH.exists():
        cmd.extend(["-v", f"{SETTINGS_PATH}:/home/bond-agent/.claude/settings.json:ro"])

    # Image + claude args
    cmd.extend([IMAGE, "--print", "--max-turns", "1", PROMPT])

    start = time.monotonic()
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=TIMEOUT,
        )
        duration = time.monotonic() - start
        success = result.returncode == 0 and "PONG" in result.stdout.upper()

        return {
            "success": success,
            "stdout": result.stdout[:2000],
            "stderr": result.stderr[:2000],
            "exit_code": result.returncode,
            "duration": round(duration, 1),
        }
    except subprocess.TimeoutExpired:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
        return {
            "success": False,
            "stdout": "",
            "stderr": f"Timed out after {TIMEOUT}s",
            "exit_code": -1,
            "duration": TIMEOUT,
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "exit_code": -1,
            "duration": time.monotonic() - start,
        }


def _report(test_name: str, result: dict) -> None:
    """Print a clear result summary for the test."""
    status = "✅ PASS" if result["success"] else "❌ FAIL"
    print(f"\n{'='*60}")
    print(f"  {status}  {test_name}  ({result['duration']}s)")
    print(f"{'='*60}")
    if result["stdout"].strip():
        print(f"  stdout: {result['stdout'].strip()[:200]}")
    if result["stderr"].strip():
        print(f"  stderr: {result['stderr'].strip()[:200]}")
    print(f"  exit_code: {result['exit_code']}")
    print()


# ---------------------------------------------------------------------------
# Tests — each explores one credential combination
# ---------------------------------------------------------------------------

@skip_no_docker
@skip_no_image
class TestCredentialMatrix:
    """Discovery tests: which credential combos actually work in containers?"""

    # Store results for the summary
    _results: dict[str, dict] = {}

    @pytest.fixture(autouse=True)
    def _record(self, request):
        yield
        # After each test, print result
        name = request.node.name
        if name in self._results:
            _report(name, self._results[name])

    # ------------------------------------------------------------------
    # 1. API key only (no OAuth files at all)
    # ------------------------------------------------------------------

    def test_01_api_key_only(self):
        """ANTHROPIC_API_KEY set, no OAuth credentials, no .claude.json."""
        api_key = _get_api_key()
        if not api_key:
            pytest.skip("No ANTHROPIC_API_KEY available")

        result = _run_claude_in_container(
            "api-key-only",
            api_key=api_key,
            mount_oauth=False,
            mount_claude_json=False,
            mount_settings=False,
        )
        self._results["test_01_api_key_only"] = result
        # We're DISCOVERING — don't assert, just record
        print(f"\n>>> API key only → {'WORKS' if result['success'] else 'FAILS'}")

    # ------------------------------------------------------------------
    # 2. API key + .claude.json (no OAuth)
    # ------------------------------------------------------------------

    def test_02_api_key_with_claude_json(self):
        """ANTHROPIC_API_KEY set, .claude.json mounted, no OAuth."""
        api_key = _get_api_key()
        if not api_key:
            pytest.skip("No ANTHROPIC_API_KEY available")

        result = _run_claude_in_container(
            "api-key-claude-json",
            api_key=api_key,
            mount_oauth=False,
            mount_claude_json=True,
            mount_settings=True,
        )
        self._results["test_02_api_key_with_claude_json"] = result
        print(f"\n>>> API key + .claude.json → {'WORKS' if result['success'] else 'FAILS'}")

    # ------------------------------------------------------------------
    # 3. Fresh OAuth only (no API key)
    # ------------------------------------------------------------------

    def test_03_oauth_only_fresh(self):
        """No API key, fresh OAuth credentials + .claude.json."""
        if not _has_valid_oauth():
            pytest.skip("No valid OAuth credentials")

        result = _run_claude_in_container(
            "oauth-fresh",
            api_key=None,
            mount_oauth=True,
            mount_claude_json=True,
            mount_settings=True,
        )
        self._results["test_03_oauth_only_fresh"] = result
        print(f"\n>>> OAuth only (fresh) → {'WORKS' if result['success'] else 'FAILS'}")

    # ------------------------------------------------------------------
    # 4. Expired OAuth only (no API key)
    # ------------------------------------------------------------------

    def test_04_oauth_only_expired(self):
        """No API key, expired OAuth credentials."""
        if not CREDENTIALS_PATH.exists():
            pytest.skip("No OAuth credentials file to create expired version")

        expired_path = _make_expired_credentials()
        try:
            result = _run_claude_in_container(
                "oauth-expired",
                api_key=None,
                mount_oauth=True,
                oauth_path=expired_path,
                mount_claude_json=True,
                mount_settings=True,
            )
            self._results["test_04_oauth_only_expired"] = result
            print(f"\n>>> OAuth only (expired) → {'WORKS' if result['success'] else 'FAILS'}")
        finally:
            os.unlink(expired_path)

    # ------------------------------------------------------------------
    # 5. Both valid API key and fresh OAuth
    # ------------------------------------------------------------------

    def test_05_both_valid(self):
        """Both ANTHROPIC_API_KEY and fresh OAuth credentials present."""
        api_key = _get_api_key()
        if not api_key:
            pytest.skip("No ANTHROPIC_API_KEY available")
        if not _has_valid_oauth():
            pytest.skip("No valid OAuth credentials")

        result = _run_claude_in_container(
            "both-valid",
            api_key=api_key,
            mount_oauth=True,
            mount_claude_json=True,
            mount_settings=True,
        )
        self._results["test_05_both_valid"] = result
        print(f"\n>>> Both valid → {'WORKS' if result['success'] else 'FAILS'}")

    # ------------------------------------------------------------------
    # 6. Invalid API key + valid OAuth
    # ------------------------------------------------------------------

    def test_06_invalid_key_valid_oauth(self):
        """Invalid ANTHROPIC_API_KEY but valid OAuth credentials."""
        if not _has_valid_oauth():
            pytest.skip("No valid OAuth credentials")

        result = _run_claude_in_container(
            "invalid-key-valid-oauth",
            api_key=_make_invalid_api_key(),
            mount_oauth=True,
            mount_claude_json=True,
            mount_settings=True,
        )
        self._results["test_06_invalid_key_valid_oauth"] = result
        print(f"\n>>> Invalid key + valid OAuth → {'WORKS' if result['success'] else 'FAILS'}")

    # ------------------------------------------------------------------
    # 7. Valid API key + expired OAuth
    # ------------------------------------------------------------------

    def test_07_valid_key_expired_oauth(self):
        """Valid ANTHROPIC_API_KEY but expired OAuth credentials."""
        api_key = _get_api_key()
        if not api_key:
            pytest.skip("No ANTHROPIC_API_KEY available")
        if not CREDENTIALS_PATH.exists():
            pytest.skip("No OAuth credentials file")

        expired_path = _make_expired_credentials()
        try:
            result = _run_claude_in_container(
                "valid-key-expired-oauth",
                api_key=api_key,
                mount_oauth=True,
                oauth_path=expired_path,
                mount_claude_json=True,
                mount_settings=True,
            )
            self._results["test_07_valid_key_expired_oauth"] = result
            print(f"\n>>> Valid key + expired OAuth → {'WORKS' if result['success'] else 'FAILS'}")
        finally:
            os.unlink(expired_path)

    # ------------------------------------------------------------------
    # 8. No credentials at all
    # ------------------------------------------------------------------

    def test_08_no_credentials(self):
        """No API key, no OAuth, no .claude.json — should always fail."""
        result = _run_claude_in_container(
            "no-creds",
            api_key=None,
            mount_oauth=False,
            mount_claude_json=False,
            mount_settings=False,
        )
        self._results["test_08_no_credentials"] = result
        print(f"\n>>> No credentials → {'WORKS' if result['success'] else 'FAILS'}")
        # This one we DO assert — it should never work
        assert not result["success"], "Claude CLI should not work without any credentials!"

    # ------------------------------------------------------------------
    # 9. OAuth with rw mount (can CLI refresh expired tokens?)
    # ------------------------------------------------------------------

    def test_09_oauth_rw_can_refresh(self):
        """Expired OAuth mounted read-write — can Claude CLI refresh the token?"""
        if not CREDENTIALS_PATH.exists():
            pytest.skip("No OAuth credentials file")

        expired_path = _make_expired_credentials()
        try:
            # Mount rw so Claude could write back refreshed tokens
            container_name = f"{CONTAINER_PREFIX}-oauth-rw-refresh"
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

            cmd = [
                "docker", "run", "--rm",
                "--name", container_name,
                "--entrypoint", "claude",
                "--network", "host",
                "-e", "ANTHROPIC_API_KEY=",
                "-v", f"{expired_path}:/home/bond-agent/.claude/.credentials.json:rw",
            ]
            if CLAUDE_JSON_PATH.exists():
                cmd.extend(["-v", f"{CLAUDE_JSON_PATH}:/home/bond-agent/.claude.json:ro"])
            if SETTINGS_PATH.exists():
                cmd.extend(["-v", f"{SETTINGS_PATH}:/home/bond-agent/.claude/settings.json:ro"])

            cmd.extend([IMAGE, "--print", "--max-turns", "1", PROMPT])

            start = time.monotonic()
            proc_result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
            duration = time.monotonic() - start
            success = proc_result.returncode == 0 and "PONG" in proc_result.stdout.upper()

            result = {
                "success": success,
                "stdout": proc_result.stdout[:2000],
                "stderr": proc_result.stderr[:2000],
                "exit_code": proc_result.returncode,
                "duration": round(duration, 1),
            }

            # Check if the file was modified (token refreshed)
            try:
                updated = json.loads(Path(expired_path).read_text())
                oauth = updated.get("claudeAiOauth", {})
                was_refreshed = not oauth.get("accessToken", "").startswith("expired_")
                result["token_refreshed"] = was_refreshed
                print(f"\n  Token refreshed: {was_refreshed}")
            except Exception:
                result["token_refreshed"] = False

            self._results["test_09_oauth_rw_can_refresh"] = result
            print(f"\n>>> OAuth rw (expired, can refresh?) → {'WORKS' if result['success'] else 'FAILS'}")
        finally:
            os.unlink(expired_path)

    # ------------------------------------------------------------------
    # 10. API key passed as env only (no file mounts, simulating get_or_create_container)
    # ------------------------------------------------------------------

    def test_10_api_key_env_only_minimal(self):
        """Minimal container: just ANTHROPIC_API_KEY env var, nothing else.

        This simulates what get_or_create_container would do if it injected
        the key as an env var without any file mounts.
        """
        api_key = _get_api_key()
        if not api_key:
            pytest.skip("No ANTHROPIC_API_KEY available")

        container_name = f"{CONTAINER_PREFIX}-minimal-env"
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)

        cmd = [
            "docker", "run", "--rm",
            "--name", container_name,
            "--entrypoint", "claude",
            "--network", "host",
            "-e", f"ANTHROPIC_API_KEY={api_key}",
            # No .claude.json, no credentials, no settings
            IMAGE, "--print", "--max-turns", "1", PROMPT,
        ]

        start = time.monotonic()
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT)
            duration = time.monotonic() - start
            success = proc.returncode == 0 and "PONG" in proc.stdout.upper()
            result = {
                "success": success,
                "stdout": proc.stdout[:2000],
                "stderr": proc.stderr[:2000],
                "exit_code": proc.returncode,
                "duration": round(duration, 1),
            }
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
            result = {"success": False, "stdout": "", "stderr": "Timeout", "exit_code": -1, "duration": TIMEOUT}

        self._results["test_10_api_key_env_only_minimal"] = result
        print(f"\n>>> API key env only (minimal) → {'WORKS' if result['success'] else 'FAILS'}")


# ---------------------------------------------------------------------------
# Summary fixture — prints the full matrix at the end
# ---------------------------------------------------------------------------

@skip_no_docker
@skip_no_image
def test_zz_summary():
    """Print summary of all credential matrix results.

    This test runs last (alphabetical ordering) and prints the consolidated matrix.
    Note: results are stored per-class instance, so this only works if pytest
    runs them in the same session. For a full summary, read the test output.
    """
    print("\n")
    print("=" * 70)
    print("  CREDENTIAL MATRIX RESULTS")
    print("  Use these results to decide the auth strategy (Design Doc 059)")
    print("=" * 70)
    print()
    print("  Run the full suite with: pytest backend/tests/test_credential_matrix.py -v -s")
    print("  Results are printed inline with each test above.")
    print()
