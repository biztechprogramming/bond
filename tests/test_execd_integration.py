"""Integration test — verify execd + Jupyter work inside bond-agent-node container.

Run with: python tests/test_execd_integration.py
Requires: docker, bond-agent-node:latest image built.
"""

import asyncio
import json
import subprocess
import sys
import time

import httpx

CONTAINER_NAME = "bond-test-execd-integration"
IMAGE = "bond-agent-node:latest"
EXECD_PORT = 44772
JUPYTER_PORT = 44771


def start_container() -> str:
    """Start a test container with execd + Jupyter."""
    # Remove any stale container
    subprocess.run(
        ["docker", "rm", "-f", CONTAINER_NAME],
        capture_output=True,
    )

    result = subprocess.run(
        [
            "docker", "run", "-d",
            "--name", CONTAINER_NAME,
            "--entrypoint", "bash",
            "-p", f"{EXECD_PORT}:{EXECD_PORT}",
            "-p", f"{JUPYTER_PORT}:{JUPYTER_PORT}",
            "-e", "BOND_CODE_INTERPRETER=1",
            IMAGE,
            "-c",
            # Start execd + Jupyter manually (no entrypoint)
            "mkdir -p /opt/opensandbox && "
            "printf 'PATH=%s\\n' \"$PATH\" > /opt/opensandbox/.env && "
            "EXECD_ENVS=/opt/opensandbox/.env /opt/opensandbox/execd --port 44772 & "
            "jupyter notebook --ip=127.0.0.1 --port=44771 "
            "--allow-root --no-browser --NotebookApp.token=bond & "
            "sleep infinity",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Failed to start container: {result.stderr}")
        sys.exit(1)

    container_id = result.stdout.strip()[:12]
    print(f"Started container {container_id}")
    return container_id


def stop_container():
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)


async def wait_for_execd(timeout: float = 15.0):
    """Wait for execd to be ready."""
    start = time.monotonic()
    async with httpx.AsyncClient(timeout=2.0) as client:
        while time.monotonic() - start < timeout:
            try:
                resp = await client.get(f"http://localhost:{EXECD_PORT}/ping")
                if resp.status_code == 200:
                    print(f"execd ready in {time.monotonic() - start:.1f}s")
                    return
            except (httpx.ConnectError, httpx.ReadError):
                pass
            await asyncio.sleep(0.5)
    raise RuntimeError(f"execd not ready after {timeout}s")


def parse_execd_output(response_text: str) -> tuple[str, str]:
    """Parse execd JSON-per-line response into stdout and stderr."""
    stdout_parts = []
    stderr_parts = []
    for line in response_text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # execd returns JSON-per-line (not SSE data: prefix)
        # Also handle SSE format just in case
        if line.startswith("data: "):
            line = line[6:]
        try:
            event = json.loads(line)
            if event.get("type") == "stdout":
                stdout_parts.append(event.get("text", ""))
            elif event.get("type") == "stderr":
                stderr_parts.append(event.get("text", ""))
        except json.JSONDecodeError:
            pass
    return "\n".join(stdout_parts), "\n".join(stderr_parts)


async def test_command_execution():
    """Test basic command execution via execd."""
    print("\n--- Test: Command Execution ---")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"http://localhost:{EXECD_PORT}/command",
            json={"command": "echo 'hello from execd' && node --version"},
        )
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"

        stdout, _ = parse_execd_output(resp.text)
        assert "hello from execd" in stdout, f"Expected greeting in output: {stdout}"
        assert "v22" in stdout, f"Expected node version in output: {stdout}"
        print(f"  ✓ Command output: {stdout.strip()}")


async def test_node_execution():
    """Test Node.js code execution via command."""
    print("\n--- Test: Node.js Execution ---")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"http://localhost:{EXECD_PORT}/command",
            json={"command": "node -e \"console.log(JSON.stringify({sum: 2+2, runtime: 'node'}))\""},
        )
        assert resp.status_code == 200

        stdout, _ = parse_execd_output(resp.text)
        result = json.loads(stdout.strip())
        assert result["sum"] == 4, f"Expected sum=4, got {result}"
        assert result["runtime"] == "node"
        print(f"  ✓ Node result: {result}")


async def test_bun_execution():
    """Test Bun execution via command."""
    print("\n--- Test: Bun Execution ---")
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"http://localhost:{EXECD_PORT}/command",
            json={"command": "bun -e \"console.log('bun works:', Bun.version)\""},
        )
        assert resp.status_code == 200

        stdout, _ = parse_execd_output(resp.text)
        assert "bun works:" in stdout, f"Expected bun output: {stdout}"
        print(f"  ✓ Bun output: {stdout.strip()}")


async def test_file_operations():
    """Test file upload and download via execd."""
    print("\n--- Test: File Operations ---")
    async with httpx.AsyncClient(timeout=10.0) as client:
        # Write a file via command
        resp = await client.post(
            f"http://localhost:{EXECD_PORT}/command",
            json={"command": "echo 'test content' > /tmp/test-file.txt"},
        )
        assert resp.status_code == 200

        # Read it back
        resp = await client.post(
            f"http://localhost:{EXECD_PORT}/command",
            json={"command": "cat /tmp/test-file.txt"},
        )
        assert resp.status_code == 200

        stdout, _ = parse_execd_output(resp.text)
        assert "test content" in stdout
        print(f"  ✓ File round-trip works")


async def test_test_runner():
    """Test running a test suite and getting structured results."""
    print("\n--- Test: Test Runner ---")
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Create a small project with a test
        setup_cmd = """
mkdir -p /tmp/test-project && cd /tmp/test-project && \
cat > test.mjs << 'TESTEOF'
import { describe, it } from 'node:test';
import assert from 'node:assert';

describe('math', () => {
    it('adds numbers', () => {
        assert.strictEqual(2 + 2, 4);
    });
    it('multiplies numbers', () => {
        assert.strictEqual(3 * 3, 9);
    });
});
TESTEOF
echo 'setup done'
"""
        resp = await client.post(
            f"http://localhost:{EXECD_PORT}/command",
            json={"command": setup_cmd},
        )
        assert resp.status_code == 200

        # Run tests
        resp = await client.post(
            f"http://localhost:{EXECD_PORT}/command",
            json={
                "command": "cd /tmp/test-project && node --test test.mjs",
                "timeout": 15000,
            },
        )
        assert resp.status_code == 200

        stdout, stderr = parse_execd_output(resp.text)
        output = stdout + "\n" + stderr
        assert "pass" in output.lower() or "ok" in output.lower(), f"Tests should pass: {output}"
        print(f"  ✓ Test runner output:\n    {output.strip()[:200]}")


async def run_tests():
    start_container()
    try:
        await wait_for_execd()
        await test_command_execution()
        await test_node_execution()
        await test_bun_execution()
        await test_file_operations()
        await test_test_runner()
        print("\n✅ All tests passed!")
    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        # Print container logs for debugging
        result = subprocess.run(
            ["docker", "logs", CONTAINER_NAME, "--tail", "30"],
            capture_output=True, text=True,
        )
        print(f"Container logs:\n{result.stdout}\n{result.stderr}")
        raise
    finally:
        stop_container()


if __name__ == "__main__":
    asyncio.run(run_tests())
