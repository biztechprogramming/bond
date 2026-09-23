#!/usr/bin/env python3
"""Sync bond prompt fragments into Kandev saved prompts.

Every fragment becomes a saved prompt named `bond-<dashed-path>`, referenced
from Kandev prompt fields as `@bond-...`. manifest.yaml tiers become aggregator
prompts you wire into Kandev's prompt hooks once:

  bond-always      -> workflow-level prompt (injected at every step entry)
  bond-planning    -> Spec/Plan step prompt
  bond-implementing-> Work step prompt
  bond-committing  -> PR step prompt
  bond-reviewing   -> Review step prompt

Tier 3 fragments stay individually addressable; type `@bond-` in any prompt
field to pick one.

Dry run by default. Pass --apply to write.
"""

import json
import os
import re
import sys
import urllib.request

API = os.environ.get("KANDEV_API", "http://localhost:38429/api/v1")
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "prompts")
APPLY = "--apply" in sys.argv


def call(method, path, payload=None):
    req = urllib.request.Request(
        API + path,
        method=method,
        data=json.dumps(payload).encode() if payload else None,
        headers={"content-type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req))


def prompt_name(rel):
    # a/b/c/c.md -> bond-a-b-c  (collapse the repeated leaf directory name)
    parts = rel[:-3].split(os.sep)
    out = [p for i, p in enumerate(parts) if i == 0 or p != parts[i - 1]]
    return "bond-" + "-".join(out)


def load_manifest():
    """{relpath: [(tier, phase), ...]} — one fragment may appear under two tiers."""
    tiers, current = {}, None
    with open(os.path.join(ROOT, "manifest.yaml")) as fh:
        for line in fh:
            if m := re.match(r"^([\w./-]+\.md):", line):
                current = m.group(1)
            elif m := re.match(r"^\s+tier:\s*(\d)", line):
                tiers.setdefault(current, []).append([int(m.group(1)), None])
            elif m := re.match(r"^\s+phase:\s*(\w+)", line):
                tiers[current][-1][1] = m.group(1)
    return tiers


def main():
    existing = {p["name"]: p for p in call("GET", "/prompts")["prompts"]}
    manifest = load_manifest()
    buckets = {}

    fragments = []
    for dirpath, _, files in os.walk(ROOT):
        for f in sorted(files):
            if f.endswith(".md"):
                fragments.append(os.path.relpath(os.path.join(dirpath, f), ROOT))

    for rel in sorted(fragments):
        name = prompt_name(rel)
        with open(os.path.join(ROOT, rel)) as fh:
            content = fh.read().strip()
        if content:
            upsert(existing, name, content)
        for tier, phase in manifest.get(rel, []):
            if tier == 1:
                buckets.setdefault("bond-always", []).append(name)
            elif tier == 2 and phase:
                buckets.setdefault("bond-" + phase, []).append(name)

    for name, refs in sorted(buckets.items()):
        upsert(existing, name, "\n".join("@" + r for r in refs))

    print("\nWire these in once (Settings > Workflows):")
    print("  workflow-level prompt : @bond-always")
    for phase in ("planning", "implementing", "reviewing", "committing"):
        if "bond-" + phase in buckets:
            print(f"  {phase:<21} step prompt : @bond-{phase}")
    if not APPLY:
        print("\n(dry run — rerun with --apply to write)")


def upsert(existing, name, content):
    prior = existing.get(name)
    if prior and prior["content"].strip() == content.strip():
        return
    verb = "update" if prior else "create"
    print(f"{verb:>6} @{name}")
    if not APPLY:
        return
    if prior:
        call("PATCH", "/prompts/" + prior["id"], {"content": content})
    else:
        existing[name] = call("POST", "/prompts", {"name": name, "content": content})


main()
