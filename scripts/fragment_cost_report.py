#!/usr/bin/env python3
"""Rank prompt fragments by total USD cost.

Queries Langfuse scores to produce a cost-ranked table of all loaded
fragments. Operators use this to decide what to optimize, consolidate,
or promote/demote between tiers.

Design Doc 064 — Phase 3.

Usage::

    python scripts/fragment_cost_report.py --days 30 --min-loads 50
    python scripts/fragment_cost_report.py --days 7 --format csv
    python scripts/fragment_cost_report.py --days 90 --top 20
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
from datetime import datetime, timedelta, timezone


def _fetch_all_scores(
    lf,  # Langfuse client
    prefix: str,
    from_timestamp: datetime | None = None,
) -> list:
    """Paginate through all Langfuse scores matching a prefix."""
    all_scores = []
    page = 1
    while True:
        kwargs: dict = {"name_starts_with": prefix, "page": page, "limit": 1000}
        if from_timestamp:
            kwargs["from_timestamp"] = from_timestamp
        batch = lf.get_scores(**kwargs)
        all_scores.extend(batch.data)
        if len(batch.data) < 1000:
            break
        page += 1
    return all_scores


def get_fragment_stats(days: int = 30) -> list[dict]:
    """Fetch and aggregate per-fragment cost data from Langfuse.

    Returns a list of dicts sorted by total_cost descending:
    ``[{"name": str, "loads": int, "total_tokens": int, "total_cost": float, "avg_cost": float}, ...]``
    """
    from langfuse import Langfuse

    lf = Langfuse()
    from_ts = datetime.now(timezone.utc) - timedelta(days=days)

    # Fetch all token estimate scores
    scores = _fetch_all_scores(lf, "fragment_token_est:", from_timestamp=from_ts)

    # Aggregate by fragment name
    stats: dict[str, dict] = {}
    for score in scores:
        name = score.name.replace("fragment_token_est:", "")
        if name not in stats:
            stats[name] = {"loads": 0, "total_tokens": 0, "total_cost": 0.0}
        stats[name]["loads"] += 1
        stats[name]["total_tokens"] += score.value

    # Fetch corresponding cost scores
    cost_scores = _fetch_all_scores(lf, "fragment_cost:", from_timestamp=from_ts)
    for score in cost_scores:
        name = score.name.replace("fragment_cost:", "")
        if name in stats:
            stats[name]["total_cost"] += score.value

    # Compute averages and sort by total cost descending
    ranked = sorted(stats.items(), key=lambda x: x[1]["total_cost"], reverse=True)

    result = []
    for name, data in ranked:
        avg_cost = data["total_cost"] / data["loads"] if data["loads"] > 0 else 0.0
        avg_tokens = data["total_tokens"] / data["loads"] if data["loads"] > 0 else 0
        result.append({
            "name": name,
            "loads": data["loads"],
            "total_tokens": data["total_tokens"],
            "avg_tokens": round(avg_tokens),
            "total_cost": data["total_cost"],
            "avg_cost": avg_cost,
        })

    return result


def format_table(stats: list[dict], top: int | None = None) -> str:
    """Format fragment stats as a human-readable table."""
    if top:
        stats = stats[:top]

    if not stats:
        return "No fragment data found."

    header = f"{'Fragment':<40} {'Loads':>6} {'Avg Tokens':>10} {'Total Cost':>12} {'Avg Cost':>12} {'Total Tokens':>13}"
    sep = "-" * len(header)
    lines = [header, sep]

    total_cost_all = 0.0
    for s in stats:
        total_cost_all += s["total_cost"]
        lines.append(
            f"{s['name']:<40} {s['loads']:>6} {s['avg_tokens']:>10,} "
            f"${s['total_cost']:>11.6f} ${s['avg_cost']:>11.8f} {s['total_tokens']:>13,}"
        )

    lines.append(sep)
    lines.append(f"{'TOTAL':<40} {'':>6} {'':>10} ${total_cost_all:>11.6f}")

    return "\n".join(lines)


def format_csv(stats: list[dict]) -> str:
    """Format fragment stats as CSV."""
    output = io.StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=["name", "loads", "avg_tokens", "total_tokens", "total_cost", "avg_cost"],
    )
    writer.writeheader()
    writer.writerows(stats)
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(
        description="Rank prompt fragments by total USD cost (Design Doc 064)."
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Number of days to look back (default: 30)",
    )
    parser.add_argument(
        "--min-loads", type=int, default=0,
        help="Minimum number of loads to include a fragment (default: 0)",
    )
    parser.add_argument(
        "--top", type=int, default=None,
        help="Show only the top N fragments by cost",
    )
    parser.add_argument(
        "--format", choices=["table", "csv"], default="table",
        help="Output format (default: table)",
    )

    args = parser.parse_args()

    print(f"Fetching fragment cost data for the last {args.days} days...", file=sys.stderr)

    stats = get_fragment_stats(days=args.days)

    if args.min_loads > 0:
        stats = [s for s in stats if s["loads"] >= args.min_loads]

    if args.format == "csv":
        print(format_csv(stats))
    else:
        print(format_table(stats, top=args.top))

    print(f"\n{len(stats)} fragments found.", file=sys.stderr)


if __name__ == "__main__":
    main()
