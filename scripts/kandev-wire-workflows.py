#!/usr/bin/env python3
"""Wire the bond aggregator prompts into every Kandev workflow.

Saved prompts are global; workflows are per-workspace, so the workflow-level
prompt and step prompts have to be set once per workflow. This does all of
them. Run kandev-sync-prompts.py --apply first.

Existing prompts are appended to, never replaced. A step prompt that gains
content also gains {{task_prompt}} — without it Kandev treats the step prompt
as the whole visible prompt and drops the task description.

Dry run by default. Pass --apply to write.
"""

import json
import os
import re
import sys
import urllib.request

API = os.environ.get("KANDEV_API", "http://localhost:38429/api/v1")
APPLY = "--apply" in sys.argv
WORKFLOW_PROMPT = "@bond-always"

# step name keyword -> saved prompts to attach
STEP_REFS = {
    "spec": ["@bond-planning"],
    "plan": ["@bond-planning"],
    "in progress": ["@bond-implementing", "@bond-committing"],
    "work": ["@bond-implementing", "@bond-committing"],
    "implementation": ["@bond-implementing", "@bond-committing"],
    "review": ["@bond-reviewing"],
    "qa": ["@bond-reviewing"],
    "pr": ["@bond-committing"],
}


def call(method, path, payload=None):
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(payload).encode() if payload else None,
        headers={"content-type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req))


def unwrap(resp, key):
    return resp[key] if isinstance(resp, dict) else resp


def refs_for(step_name):
    # Short keywords ("pr", "qa") must match a whole word, or "Approved" and
    # "Preparation" pick up PR refs. Longer ones match a word prefix so
    # "Reviewing" and "Planning" still resolve. First hit wins, so "In
    # Progress" beats the stricter "pr".
    name = step_name.lower()
    for keyword, refs in STEP_REFS.items():
        tail = r"\b" if len(keyword) <= 3 else ""
        if re.search(r"\b" + re.escape(keyword) + tail, name):
            return refs
    return []


def merged(existing, refs, needs_task_prompt):
    """Append missing refs to existing prompt text, or None when nothing changes."""
    missing = [r for r in refs if r not in existing]
    if not missing:
        return None
    body = existing.strip()
    if not body and needs_task_prompt:
        body = "{{task_prompt}}"
    return (body + "\n\n" + "\n".join(missing)).strip()


def main():
    for wf in unwrap(call("GET", "/workflows"), "workflows"):
        print(f"\n{wf['name']}  ({wf['id']})")

        prompt = merged(wf.get("prompt") or "", [WORKFLOW_PROMPT], needs_task_prompt=False)
        if prompt:
            print(f"  workflow prompt  += {WORKFLOW_PROMPT}")
            if APPLY:
                call("PATCH", f"/workflows/{wf['id']}", {"prompt": prompt})

        steps = unwrap(call("GET", f"/workflows/{wf['id']}/workflow/steps"), "steps")
        for step in steps:
            refs = refs_for(step["name"])
            if not refs:
                continue
            prompt = merged(step.get("prompt") or "", refs, needs_task_prompt=True)
            if not prompt:
                continue
            print(f"  {step['name']:<14} += {' '.join(refs)}")
            if APPLY:
                call("PUT", f"/workflow/steps/{step['id']}", {"prompt": prompt})

    if not APPLY:
        print("\n(dry run — rerun with --apply to write)")


def selftest():
    # Step names that must not pick up refs from a substring match.
    assert refs_for("Approved") == [], refs_for("Approved")
    assert refs_for("Preparation") == []
    assert refs_for("Backlog") == refs_for("Done") == refs_for("Ideas") == []
    # Names that must match, including trailing partials.
    assert refs_for("In Progress") == ["@bond-implementing", "@bond-committing"]
    assert refs_for("Reviewing") == ["@bond-reviewing"]
    assert refs_for("Planning") == ["@bond-planning"]
    assert refs_for("Draft PR") == ["@bond-committing"]
    # Empty step prompt gains {{task_prompt}}; a rerun is a no-op.
    first = merged("", ["@bond-reviewing"], needs_task_prompt=True)
    assert first == "{{task_prompt}}\n\n@bond-reviewing", repr(first)
    assert merged(first, ["@bond-reviewing"], needs_task_prompt=True) is None
    # A hand-edited prompt keeps its text and gains only what is missing.
    grown = merged("{{task_prompt}}\n\nMine.", ["@bond-reviewing"], needs_task_prompt=True)
    assert grown.startswith("{{task_prompt}}\n\nMine.") and grown.endswith("@bond-reviewing")
    # Workflow-level prompt never injects {{task_prompt}}.
    assert merged("", ["@bond-always"], needs_task_prompt=False) == "@bond-always"
    print("selftest ok")


if "--selftest" in sys.argv:
    selftest()
else:
    main()
