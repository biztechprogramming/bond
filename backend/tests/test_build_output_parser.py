"""Tests for backend.app.agent.build_output_parser."""

from backend.app.agent.build_output_parser import parse_build_output


# ---------------------------------------------------------------------------
# dotnet build
# ---------------------------------------------------------------------------

def test_dotnet_build_success():
    stdout = """Microsoft (R) Build Engine version 17.8.0
  Determining projects to restore...
  All projects are up-to-date for restore.
  MyProject -> /app/bin/Debug/net8.0/MyProject.dll

Build succeeded.
    0 Warning(s)
    0 Error(s)

Time Elapsed 00:00:02.34
"""
    result = parse_build_output(stdout, "", 0)
    assert result is not None
    assert "Build succeeded" in result
    assert "0 warnings" in result


def test_dotnet_build_success_with_warnings():
    stdout = """Microsoft (R) Build Engine version 17.8.0
  Determining projects to restore...
  /app/Foo.cs(12,5): warning CS0168: The variable 'x' is declared but never used
  /app/Bar.cs(30,1): warning CS0219: The variable 'y' is assigned but never used

Build succeeded.
    2 Warning(s)
    0 Error(s)
"""
    result = parse_build_output(stdout, "", 0)
    assert result is not None
    assert "Build succeeded" in result
    assert "2 warnings" in result


def test_dotnet_build_failure():
    stdout = """Microsoft (R) Build Engine version 17.8.0
  Determining projects to restore...
  /app/Program.cs(10,5): error CS1002: ; expected
  /app/Program.cs(15,1): error CS0246: The type or namespace name 'Foo' could not be found

Build FAILED.
    0 Warning(s)
    2 Error(s)
"""
    result = parse_build_output(stdout, "", 1)
    assert result is not None
    assert "Build FAILED" in result
    assert "CS1002" in result
    assert "CS0246" in result


# ---------------------------------------------------------------------------
# dotnet test
# ---------------------------------------------------------------------------

def test_dotnet_test_pass():
    stdout = """Test run for /app/bin/Debug/net8.0/Tests.dll
Starting test execution, please wait...
A total of 1 test files matched the specified pattern.

Passed!  - Failed:     0, Passed:    42, Skipped:     0, Total:    42, Duration: 1.234 s
Total tests: 42. Passed: 42. Failed: 0.
"""
    result = parse_build_output(stdout, "", 0)
    assert result is not None
    assert "42" in result
    assert "passed" in result.lower() or "42/42" in result


def test_dotnet_test_fail():
    stdout = """Test run for /app/bin/Debug/net8.0/Tests.dll
Starting test execution, please wait...

Failed!  - Failed:     2, Passed:    40, Skipped:     0, Total:    42, Duration: 1.234 s
Total tests: 42. Passed: 40. Failed: 2.
  Failed MyApp.Tests.FooTest
    Assert.Equal() Failure
    Expected: 5
    Actual:   3
"""
    result = parse_build_output(stdout, "", 1)
    assert result is not None
    assert "2" in result
    assert "42" in result


# ---------------------------------------------------------------------------
# npm build
# ---------------------------------------------------------------------------

def test_npm_build_success():
    stdout = """
> my-app@1.0.0 build
> vite build

vite v5.0.0 building for production...
✓ 200 modules transformed.
dist/index.html              1.23 kB
dist/assets/index-abc123.js  150.45 kB
✓ built in 2.34s
"""
    result = parse_build_output(stdout, "", 0)
    assert result is not None
    assert "built in" in result.lower() or "succeeded" in result.lower()


def test_npm_build_failure():
    stdout = """
> my-app@1.0.0 build
> tsc && vite build

src/App.tsx(15,3): Error: Property 'foo' does not exist on type 'Bar'.
src/utils.ts(8,1): Error: Module not found: 'nonexistent'
"""
    result = parse_build_output(stdout, "", 1)
    assert result is not None
    assert "failed" in result.lower() or "Error" in result


# ---------------------------------------------------------------------------
# pytest
# ---------------------------------------------------------------------------

def test_pytest_pass():
    stdout = """============================= test session starts ==============================
platform linux -- Python 3.12.0
collected 15 items

tests/test_foo.py ....          [ 26%]
tests/test_bar.py ...........   [100%]

============================== 15 passed in 0.45s ==============================
"""
    result = parse_build_output(stdout, "", 0)
    assert result is not None
    assert "15 passed" in result


def test_pytest_fail():
    stdout = """============================= test session starts ==============================
platform linux -- Python 3.12.0
collected 15 items

tests/test_foo.py ....          [ 26%]
tests/test_bar.py .....F...F.   [100%]

FAILED tests/test_bar.py::test_something - AssertionError: assert 1 == 2
FAILED tests/test_bar.py::test_other - ValueError

=========================== 2 failed, 13 passed in 0.78s ======================
"""
    result = parse_build_output(stdout, "", 1)
    assert result is not None
    assert "2 failed" in result
    assert "13 passed" in result


# ---------------------------------------------------------------------------
# cargo
# ---------------------------------------------------------------------------

def test_cargo_build_success():
    stdout = """   Compiling my-crate v0.1.0 (/app)
    Finished dev [unoptimized + debuginfo] target(s) in 2.34s
"""
    result = parse_build_output(stdout, "", 0)
    assert result is not None
    assert "Finished" in result


def test_cargo_test_pass():
    stdout = """   Compiling my-crate v0.1.0 (/app)
    Finished test [unoptimized + debuginfo] target(s) in 2.34s
     Running unittests src/lib.rs

running 10 tests
test tests::test_foo ... ok
test tests::test_bar ... ok
test tests::test_baz ... ok

test result: ok. 10 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.05s
"""
    result = parse_build_output(stdout, "", 0)
    assert result is not None
    assert "10" in result
    assert "passed" in result


def test_cargo_test_fail():
    stdout = """running 10 tests
test tests::test_foo ... ok
test tests::test_bar ... FAILED

failures:

---- tests::test_bar stdout ----
thread 'tests::test_bar' panicked at 'assertion failed: false'

test result: FAILED. 9 passed; 1 failed; 0 ignored
"""
    result = parse_build_output(stdout, "", 1)
    assert result is not None
    assert "1 failed" in result


# ---------------------------------------------------------------------------
# Generic fallback
# ---------------------------------------------------------------------------

def test_generic_fallback_success_large():
    """Large output from unknown tool returns None (handled by tool_result_filter instead)."""
    lines = [f"line {i}: some output" for i in range(200)]
    stdout = "\n".join(lines)
    result = parse_build_output(stdout, "", 0)
    assert result is None  # no build pattern detected, let normal filter handle it


def test_generic_fallback_failure_large():
    """Large failed output from unknown tool returns None (no build pattern detected)."""
    lines = [f"line {i}: some output" for i in range(100)]
    lines[50] = "ERROR: something went wrong"
    lines[51] = "Error: another issue"
    stdout = "\n".join(lines)
    result = parse_build_output(stdout, "", 1)
    assert result is None  # no build pattern detected


def test_short_output_passthrough():
    """Short output (< 500 chars) returns None to pass through unchanged."""
    stdout = "Hello world\nDone."
    result = parse_build_output(stdout, "", 0)
    assert result is None


def test_none_when_no_pattern_short():
    """Returns None when no pattern recognized and output is short."""
    stdout = "Some random short output that matches no build tool."
    result = parse_build_output(stdout, "", 0)
    assert result is None


# ---------------------------------------------------------------------------
# jest/vitest
# ---------------------------------------------------------------------------

def test_jest_pass():
    stdout = """PASS src/App.test.tsx
PASS src/utils.test.ts

Test Suites: 2 passed, 2 total
Tests:       15 passed, 15 total
Snapshots:   0 total
Time:        2.345 s
"""
    result = parse_build_output(stdout, "", 0)
    assert result is not None
    assert "15 passed" in result


def test_jest_fail():
    stdout = """FAIL src/App.test.tsx
  ● Component > should render

    Expected: "hello"
    Received: "world"

Test Suites: 1 failed, 1 passed, 2 total
Tests:       1 failed, 14 passed, 15 total
"""
    result = parse_build_output(stdout, "", 1)
    assert result is not None
    assert "1 failed" in result
