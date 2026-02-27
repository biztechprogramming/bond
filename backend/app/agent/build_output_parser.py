"""Smart build/test output parser for code_execute results.

Detects known build tool patterns (dotnet, npm, pytest, cargo, etc.) and
compresses verbose output to a concise summary. Returns None if no known
pattern is detected and output is short enough to pass through unchanged.
"""

from __future__ import annotations

import re

# Strip ANSI escape codes
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _count_warnings(text: str) -> int:
    m = re.search(r"(\d+)\s+Warning", text)
    if m:
        return int(m.group(1))
    return len(re.findall(r"warning\s+(CS|MSB|NU)\d+", text, re.IGNORECASE))


def _parse_dotnet_build(combined: str, exit_code: int) -> str | None:
    """Parse dotnet build/publish output."""
    if not any(kw in combined for kw in ("Build succeeded", "Build FAILED", "error MSB", "Determining projects to restore")):
        return None

    if "Build succeeded" in combined and exit_code == 0:
        warnings = _count_warnings(combined)
        return f"Build succeeded ({warnings} warnings)"

    # Failure: extract error lines
    error_lines = []
    for line in combined.splitlines():
        stripped = line.strip()
        if re.search(r"error\s+(CS|MSB|NU|FS)\d+", stripped, re.IGNORECASE) or ": error " in stripped:
            error_lines.append(stripped)
    if error_lines:
        return "Build FAILED:\n" + "\n".join(error_lines[:20])
    return "Build FAILED (no parseable error lines)"


def _parse_dotnet_test(combined: str, exit_code: int) -> str | None:
    """Parse dotnet test output."""
    if not any(kw in combined for kw in ("Test run for", "Passed!", "Failed!", "Total tests:")):
        return None

    # Look for summary line like "Total tests: 42. Passed: 40. Failed: 2."
    m = re.search(r"Total tests:\s*(\d+).*?Passed:\s*(\d+).*?Failed:\s*(\d+)", combined, re.DOTALL)
    if m:
        total, passed, failed = m.group(1), m.group(2), m.group(3)
        if int(failed) == 0:
            return f"Tests passed: {passed}/{total}"
        # Failure: extract failed test info
        fail_lines = []
        for line in combined.splitlines():
            stripped = line.strip()
            if "Failed " in stripped or "Assert." in stripped or "Expected:" in stripped or "Actual:" in stripped:
                fail_lines.append(stripped)
        summary = f"Tests failed: {failed}/{total}"
        if fail_lines:
            summary += "\n" + "\n".join(fail_lines[:20])
        return summary

    if "Passed!" in combined and exit_code == 0:
        return "Tests passed"
    if "Failed!" in combined:
        return "Tests failed (see output for details)"
    return None


def _parse_npm_build(combined: str, exit_code: int) -> str | None:
    """Parse npm/yarn/pnpm/webpack/vite/tsc build output."""
    indicators = ("npm run", "webpack", "vite", "tsc", "Successfully compiled", "compiled successfully",
                  "Build error", "Module not found", "npm ERR!")
    if not any(kw in combined for kw in indicators):
        return None

    clean = _strip_ansi(combined)

    if exit_code == 0:
        # Look for a meaningful summary line
        for line in reversed(clean.splitlines()):
            stripped = line.strip()
            if any(kw in stripped.lower() for kw in ("compiled", "built in", "ready in", "successfully")):
                return stripped
        return "Build succeeded"

    # Failure: extract error lines
    error_lines = []
    for line in clean.splitlines():
        stripped = line.strip()
        if any(kw in stripped for kw in ("Error:", "ERROR", "error ", "Module not found", "SyntaxError", "TypeError")):
            error_lines.append(stripped)
    if error_lines:
        return "Build failed:\n" + "\n".join(error_lines[:20])
    return "Build failed"


def _parse_pytest(combined: str, exit_code: int) -> str | None:
    """Parse pytest output."""
    if not any(kw in combined for kw in ("test session starts", "passed", "failed", "PASSED", "FAILED", "pytest")):
        return None

    # Look for the summary line: "X passed, Y failed" or "X passed"
    m = re.search(r"=+\s*(.*(?:passed|failed|error).*?)\s*=+", combined)
    if m:
        summary_line = m.group(1).strip()
        if exit_code == 0:
            return summary_line

        # Failure: extract FAILED test names and short errors
        fail_lines = []
        lines = combined.splitlines()
        in_failure = False
        for line in lines:
            if line.startswith("FAILED ") or "FAILED" in line and "::" in line:
                fail_lines.append(line.strip())
            elif line.startswith("E ") or line.startswith("    E "):
                fail_lines.append(line.strip())
            elif "AssertionError" in line or "AssertionError" in line or "assert " in line:
                fail_lines.append(line.strip())
        result = summary_line
        if fail_lines:
            result += "\n" + "\n".join(fail_lines[:20])
        return result

    return None


def _parse_jest_vitest(combined: str, exit_code: int) -> str | None:
    """Parse jest/vitest output."""
    if not any(kw in combined for kw in ("Tests:", "Test Suites:", "PASS ", "FAIL ")):
        return None

    clean = _strip_ansi(combined)

    # Look for summary lines
    summary_parts = []
    for line in clean.splitlines():
        stripped = line.strip()
        if stripped.startswith("Tests:") or stripped.startswith("Test Suites:"):
            summary_parts.append(stripped)

    if summary_parts:
        if exit_code == 0:
            return " | ".join(summary_parts)

        # Failure: also extract FAIL lines
        fail_lines = []
        for line in clean.splitlines():
            stripped = line.strip()
            if stripped.startswith("FAIL ") or "Expected:" in stripped or "Received:" in stripped:
                fail_lines.append(stripped)
        result = " | ".join(summary_parts)
        if fail_lines:
            result += "\n" + "\n".join(fail_lines[:20])
        return result

    return None


def _parse_cargo(combined: str, exit_code: int) -> str | None:
    """Parse cargo build/test output."""
    if not any(kw in combined for kw in ("Compiling", "Finished", "cargo test", "test result:")):
        return None

    # cargo test result line: "test result: ok. X passed; Y failed; Z ignored"
    m = re.search(r"test result:.*?(\d+) passed.*?(\d+) failed", combined)
    if m:
        passed, failed = m.group(1), m.group(2)
        if int(failed) == 0:
            return f"Tests passed: {passed} passed, 0 failed"
        # Extract failed test names
        fail_lines = []
        for line in combined.splitlines():
            if "FAILED" in line or "panicked" in line:
                fail_lines.append(line.strip())
        result = f"Tests failed: {passed} passed, {failed} failed"
        if fail_lines:
            result += "\n" + "\n".join(fail_lines[:20])
        return result

    # cargo build
    if "Finished" in combined and exit_code == 0:
        for line in combined.splitlines():
            if "Finished" in line:
                return line.strip()
        return "Build succeeded"

    if exit_code != 0:
        error_lines = []
        for line in combined.splitlines():
            stripped = line.strip()
            if stripped.startswith("error"):
                error_lines.append(stripped)
        if error_lines:
            return "Build failed:\n" + "\n".join(error_lines[:20])
        return "Build failed"

    return None


def _generic_fallback(stdout: str, stderr: str, exit_code: int) -> str | None:
    """Generic fallback for unknown build tools with large output."""
    combined = stdout + stderr
    if len(combined) <= 1000:
        return None

    lines = combined.splitlines()

    if exit_code == 0:
        # Success: keep first 3 + last 5
        if len(lines) <= 8:
            return None
        omitted = len(lines) - 8
        return "\n".join(lines[:3]) + f"\n[{omitted} lines omitted]\n" + "\n".join(lines[-5:])

    # Failure: first 3 + error lines + last 10
    error_lines = []
    for line in lines[3:]:
        if any(kw in line for kw in ("error", "Error", "ERROR")):
            error_lines.append(line)

    head = lines[:3]
    tail = lines[-10:] if len(lines) > 10 else lines
    parts = head
    if error_lines:
        parts += ["", "--- errors ---"] + error_lines[:20]
    omitted = max(0, len(lines) - len(head) - len(tail))
    if omitted > 0:
        parts.append(f"[{omitted} lines omitted]")
    parts += tail
    return "\n".join(parts)


def parse_build_output(stdout: str, stderr: str, exit_code: int) -> str | None:
    """Parse build/test tool output into a concise summary.

    Returns the parsed summary string, or None if no known build tool
    pattern is detected (allowing the original output to pass through).
    """
    combined = stdout + "\n" + stderr if stderr else stdout

    # Try each parser in order
    for parser in (
        _parse_dotnet_build,
        _parse_dotnet_test,
        _parse_npm_build,
        _parse_pytest,
        _parse_jest_vitest,
        _parse_cargo,
    ):
        result = parser(combined, exit_code)
        if result is not None:
            return result

    # Generic fallback for large output
    return _generic_fallback(stdout, stderr, exit_code)
