#!/usr/bin/env python3
"""Check reducer name inconsistencies between Python and TypeScript."""

import re

# Get all reducer calls from Python files
print("=== Checking reducer name inconsistencies ===\n")

# First, let's get all reducer names from TypeScript
print("1. TypeScript reducers (from spacetimedb/src/index.ts):")
ts_reducers = []
with open("spacetimedb/spacetimedb/src/index.ts", "r") as f:
    content = f.read()
    # Find all export const <name> = spacetimedb.reducer
    matches = re.findall(r'export const (\w+) = spacetimedb\.reducer', content)
    ts_reducers = matches
    for i, reducer in enumerate(sorted(matches)):
        print(f"   {i+1:3}. {reducer}")

print(f"\n   Total: {len(ts_reducers)} reducers")

# Now get all reducer calls from Python files
print("\n2. Python reducer calls:")
python_calls = []
import os
for root, dirs, files in os.walk("backend"):
    for file in files:
        if file.endswith(".py"):
            path = os.path.join(root, file)
            with open(path, "r") as f:
                content = f.read()
                # Find call_reducer calls
                matches = re.findall(r'call_reducer\s*\(\s*"([^"]+)"', content)
                for match in matches:
                    python_calls.append((match, path))

# Group by reducer name
reducer_counts = {}
for reducer, path in python_calls:
    if reducer not in reducer_counts:
        reducer_counts[reducer] = []
    reducer_counts[reducer].append(path)

print("\n   Python calls (sorted by name):")
for reducer in sorted(reducer_counts.keys()):
    paths = reducer_counts[reducer]
    print(f"   - {reducer}")
    for path in paths[:2]:  # Show first 2 occurrences
        print(f"       {path}")
    if len(paths) > 2:
        print(f"       ... and {len(paths)-2} more")

print(f"\n   Total: {len(python_calls)} calls to {len(reducer_counts)} unique reducers")

# Check for inconsistencies
print("\n3. Inconsistencies (Python snake_case vs TypeScript camelCase):")
inconsistencies = []
for python_reducer in sorted(reducer_counts.keys()):
    # Convert Python reducer name to possible TypeScript names
    # Python might use snake_case, TypeScript uses camelCase
    # Try to convert snake_case to camelCase
    parts = python_reducer.split('_')
    camel_case = parts[0] + ''.join(p.capitalize() for p in parts[1:])
    
    # Also check if Python is already using camelCase
    if python_reducer in ts_reducers:
        # Python is using camelCase, matches TypeScript
        continue
    elif camel_case in ts_reducers:
        # Python is using snake_case, TypeScript uses camelCase
        inconsistencies.append((python_reducer, camel_case, "snake_case -> camelCase"))
    else:
        # Not found in TypeScript at all
        inconsistencies.append((python_reducer, "NOT FOUND", "Missing in TypeScript"))

if inconsistencies:
    print("   Found inconsistencies:")
    for python_name, ts_name, issue in inconsistencies:
        print(f"   - Python: '{python_name}' -> TypeScript: '{ts_name}' ({issue})")
else:
    print("   No inconsistencies found!")

# Check the opposite: TypeScript reducers not called from Python
print("\n4. TypeScript reducers not called from Python:")
uncalled = []
for ts_reducer in ts_reducers:
    # Convert camelCase to snake_case to check
    snake_case = re.sub(r'([a-z])([A-Z])', r'\1_\2', ts_reducer).lower()
    
    if ts_reducer not in reducer_counts and snake_case not in reducer_counts:
        uncalled.append(ts_reducer)

if uncalled:
    print(f"   {len(uncalled)} reducers not called from Python:")
    for reducer in sorted(uncalled):
        print(f"   - {reducer}")
else:
    print("   All TypeScript reducers are called from Python!")

print("\n=== Summary ===")
print(f"- TypeScript has {len(ts_reducers)} reducers")
print(f"- Python calls {len(reducer_counts)} unique reducers")
print(f"- Found {len(inconsistencies)} naming inconsistencies")
print(f"- {len(uncalled)} TypeScript reducers not called from Python")