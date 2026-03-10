# Pull Requests

You have the `repo_pr` tool to propose changes to this repository. Use it whenever you need to create, add, fix, or update code.

## Using repo_pr

The `repo_pr` tool handles everything: creates a feature branch, writes files, commits, pushes, and opens a GitHub PR. You provide:

- **branch**: A descriptive branch name (e.g. `feat/add-weather-tool`, `fix/auth-token-expiry`)
- **title**: Clear PR title following commit message conventions
- **body**: Explain what changed and why. Include key changes, motivation, and how it was verified.
- **files**: An object mapping relative file paths to their **full file contents**. Every file you include will be written exactly as provided.
- **commit_message**: A clear git commit message

### Important: files must contain complete contents

The `files` parameter **overwrites** each file entirely. You must include the **full file content** — not a diff, not a partial snippet. If you're modifying an existing file, read it first with `file_read`, apply your changes, and pass the complete updated content.

### Example

```json
{
  "branch": "feat/add-health-endpoint",
  "title": "Add /health endpoint to API",
  "body": "Adds a lightweight health check endpoint.\n\n## Changes\n- New GET /health route returning 200 OK\n- Added test coverage\n\n## Why\nNeeded for container orchestration liveness probes.",
  "files": {
    "backend/app/api/health.py": "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n@router.get(\"/health\")\nasync def health():\n    return {\"status\": \"ok\"}\n",
    "backend/tests/test_health.py": "import httpx\nimport pytest\n\n@pytest.mark.anyio\nasync def test_health(client):\n    resp = await client.get(\"/health\")\n    assert resp.status_code == 200\n"
  },
  "commit_message": "feat: add /health endpoint for liveness probes"
}
```

### When to use repo_pr

- Adding new tools, endpoints, or features
- Updating prompts or configuration
- Fixing bugs you've identified
- Any change that should be reviewed before merging

### Workflow

1. **Understand** the change needed — read relevant files first
2. **Plan** what files need to be created or modified
3. **Read** any existing files you'll modify (use `file_read`)
4. **Call `repo_pr`** with the complete file contents, a clear title, and descriptive body
5. **Report** the PR URL to the user

### PR Quality

- **Keep it focused**: One logical change per PR
- **Clean body**: Summarize what changed, why, and how it was tested
- **No noise**: Don't include debug code, logs, or unrelated changes
- **Self-review**: Before calling `repo_pr`, mentally diff your changes against the original files to catch mistakes
