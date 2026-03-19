# 048 — Self-Optimizing Prompts

## Problem

Tuning the agent loop's prompts and fragments is manual, slow, and requires the developer to notice inefficiencies by reading traces. There's no feedback loop where the system learns from its own performance.

## Goal

After every non-trivial agent turn, a **critic agent** (a full coding agent with repo access) reviews the execution trace, identifies the single most impactful prompt improvement, and writes it as a **lesson candidate**. Candidates are staged and only promoted to the active lessons file once a similar suggestion has recurred **3 or more times** (measured by cosine similarity ≥ 0.3). The developer curates the promoted lessons file at their convenience.

## Design Principles

- **All prompt content lives on the filesystem** — versioned in git, never in the database.
- **Noise filter via recurrence** — one-off suggestions stay in staging; only patterns surface.
- **Human-in-the-loop** — the system proposes, the developer curates.
- **Non-blocking** — critique runs as a background task after the turn is complete; never delays the user-facing response.
- **Simple** — ~200 lines of code, two new files, no new database tables.

## File Layout

```
prompts/_optimization/
    lessons/
        proposed/           # new lessons land here — one .md file per lesson
        approved/           # promoted lessons after human review
    candidates.jsonl        # staging area — raw critic output with embeddings
```

### `lessons/proposed/`

When a lesson is promoted from candidates (recurrence ≥ 3), it's written as an individual `.md` file in `proposed/`. Files are named with a date prefix and slug, e.g. `2026-03-18-verify-file-exists.md`. These await human review.

```markdown
<!-- lessons/proposed/2026-03-18-verify-file-exists.md -->
# Verify file exists before reading

Verify a file exists before attempting to read it. Avoids wasted tool calls and confusing error traces.

- First observed: 2026-03-18
- Recurrences: 3
```

### `lessons/approved/`

After review, the developer moves a lesson from `proposed/` to `approved/`. Only files in `approved/` are injected into the system prompt. The developer may also edit the lesson for clarity before moving it, or delete a proposed lesson they disagree with.

### `candidates.jsonl`

One JSON object per line. Each candidate stores the lesson text, its embedding vector, and the date. This file is disposable — delete it to reset the candidate pool. Add to `.gitignore`.

```jsonl
{"lesson": "Verify a file exists before reading it.", "embedding": [...], "date": "2026-03-18"}
{"lesson": "Check file paths before read attempts.", "embedding": [...], "date": "2026-03-18"}
```

## How It Works

### 1. Trace Capture (in `loop.py`)

During the tool-use loop, collect step metadata:

```python
trace_steps = []

# Around each tool execution:
trace_steps.append({"tool": name, "args": list(args.keys())})
```

Also capture which prompt fragments were active for the turn (from the fragment router).

### 2. Critic Agent Spawn (background, after turn completes)

```python
asyncio.create_task(critique_turn(user_message, trace_steps, trace_fragments))
```

The critic is a full coding agent (Claude Code with `--permission-mode bypassPermissions --print`) spawned with access to the repo. It:

- Reads the prompt fragments that were active during the turn
- Analyzes the step sequence for redundancy or confusion
- Writes ONE lesson (or "NONE") to a temp file
- Exits

The critic runs with a 120-second timeout. If it fails or times out, it's silently ignored.

### 3. Candidate Processing

After the critic returns its lesson:

1. **Embed** the lesson using FastEmbed (`BAAI/bge-small-en-v1.5` — already used by the fragment router).
2. **Load existing candidates** from `candidates.jsonl`.
3. **Find similar candidates** using cosine similarity ≥ 0.3.
4. **Append** the new candidate to the file.
5. **Check recurrence**: if `similar_count + 1 >= 3`:
   - Verify the lesson isn't already in `lessons/proposed/` or `lessons/approved/` (cosine similarity check against existing lesson files).
   - If novel, write a new `.md` file to `lessons/proposed/` with a date-prefix slug.
   - Remove the consumed candidates from `candidates.jsonl`.

### 4. Lessons Injection (in prompt assembly)

```python
approved_dir = PROMPTS_DIR / "_optimization/lessons/approved"
if approved_dir.exists():
    for lesson_file in sorted(approved_dir.glob("*.md")):
        full_system += "\n\n" + lesson_file.read_text()
```

Only lessons in the `approved/` directory are injected into the system prompt. Proposed lessons have no effect on agent behavior until a human moves them to `approved/`.

### 5. Developer Curation

Periodically review `prompts/_optimization/lessons/proposed/`:

- **Approve** — move the file to `lessons/approved/` (it will be injected into the system prompt)
- **Edit** — rewrite for clarity before approving
- **Delete** — remove lessons that are wrong, outdated, or too specific
- **Promote** — move a great approved lesson into the actual prompt fragment file where it belongs, then delete it from `approved/`
- **Git commit** when satisfied

## Thresholds

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Min steps to critique | 3 | Trivial turns have nothing to optimize |
| Cosine similarity threshold | 0.3 | Loose enough to cluster paraphrases |
| Promotion count | 3 | Filters one-off noise while catching real patterns |
| Critic timeout | 120s | Generous but bounded |
| Embedding model | `BAAI/bge-small-en-v1.5` | Already in use by fragment router, fast local inference |

## Cost

- ~$0.02–0.05 per critique (Claude Code turn with minimal tool use)
- Embedding is local (FastEmbed/ONNX), zero API cost
- No database writes

## Files Changed

| File | Change |
|------|--------|
| `backend/app/agent/critic.py` | New — critic agent + candidate processing |
| `backend/app/agent/loop.py` | Add trace collection (~10 lines) + background critic spawn (~3 lines) |
| `prompts/_optimization/lessons/proposed/` | New — individual lesson files awaiting review |
| `prompts/_optimization/lessons/approved/` | New — reviewed lessons injected into system prompt |
| `prompts/_optimization/candidates.jsonl` | New — created on first critique |
| `.gitignore` | Add `prompts/_optimization/candidates.jsonl` |

## Future Extensions

- **Periodic dedup**: when `lessons.md` grows past ~30 lines, run a consolidation pass that merges redundant lessons.
- **Category-specific lessons**: split into `lessons-coding.md`, `lessons-research.md`, etc., injected conditionally based on task classification.
- **DSPy integration**: once enough traces accumulate with quality scores, use MIPRO to do full prompt optimization using the traces as a training set.
- **Dashboard**: surface promotion events and lesson counts in the frontend.
