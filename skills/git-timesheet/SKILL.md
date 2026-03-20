---
name: git-timesheet
description: >
  Create solidtime time entries from git commit history. Use when the user asks to
  create time entries, log time, generate a timesheet, or track hours from git history.
  Triggers on phrases like "create time entries from git history", "log time from commits",
  "timesheet from git", "track hours from git". Requires solidtime MCP integration and
  access to git repositories.
---

# Git Timesheet

Generate solidtime time entries by analyzing git commit history.

## Workflow

### 1. Determine Parameters

- **Repo**: If the user didn't specify a repo, ask which one. List available repos if possible (`find ~ -maxdepth 3 -name .git -type d 2>/dev/null`).
- **Time range**: Default to "last 2 weeks" if not specified. Accept natural language ("last week", "since March 1", etc.).
- **Author**: Use the git config user for the repo unless told otherwise.

### 2. Extract Git History

Run in the target repo:

```bash
git log --after="<start_date>" --before="<end_date>" --author="<author>" \
  --pretty=format:"%H|%ad|%s" --date=short --no-merges
```

Also gather diff stats for sizing:

```bash
git log --after="<start_date>" --before="<end_date>" --author="<author>" \
  --pretty=format:"%H" --no-merges | while read h; do
  echo "===COMMIT:$h==="
  git diff --stat "$h^" "$h" 2>/dev/null
done
```

### 3. Group Into Work Chunks

Analyze commits and group them into logical work descriptions, each representing 2–6 hours of work:

- Group by feature, bug fix, or area of the codebase (not by individual commit).
- Merge small related commits into one entry.
- Split large multi-day efforts into daily chunks if they span multiple days.
- Each chunk needs: **date** (or date range), **description** (1–2 sentences of what was done), and a **relative size weight** based on diff stats and commit count.

### 4. Fetch Solidtime Projects

Use solidtime MCP to list available projects. Map each work chunk to the most appropriate project. If the mapping is ambiguous, ask the user.

### 5. Present Proposed Entries

Show the user a table:

| # | Date | Project | Description | Est. Hours |
|---|------|---------|-------------|------------|

- Do NOT include estimated hours yet — just show date, project, and description.
- Ask the user: **"How many total hours did you work during this period?"**
- Ask if any entries should be edited, merged, split, or removed.

### 6. Distribute Hours

Once the user provides total hours:

- Distribute hours across entries proportionally based on the relative size weights from step 3.
- Round to nearest 0.25 hours.
- Ensure the sum equals the user's total (adjust the largest entry if rounding causes drift).
- Present the updated table WITH hours and ask for final approval.

### 7. Create Entries

Only after explicit user approval:

- Create each time entry in solidtime via MCP.
- Report success/failure for each entry.
- Show a summary of total hours logged.

## Important Rules

- **Never create entries without approval.** Always show proposed entries first.
- **Never guess total hours.** The user must provide this number.
- Prefer fewer, well-described entries over many granular ones.
- If a week has no commits, note it and skip — don't fabricate entries.
