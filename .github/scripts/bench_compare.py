#!/usr/bin/env python3
"""Advisory bench-baseline comparison for the CI ``bench`` job (T18).

Inputs (CWD-relative, produced by the workflow):
  - bench-current.json   this run's ``cairn bench --save`` payload
  - bench-baseline.json  rolling baseline restored via actions/cache
                         (absent on the first run or after cache eviction)

Outputs:
  - appends a markdown comparison to $GITHUB_STEP_SUMMARY (run summary)
  - writes bench-comment.md, the body for the advisory PR comment posted
    by the workflow's github-script step (keyed on the hidden marker)

ADVISORY ONLY: never exits non-zero. Regressions are surfaced for human
review; they do not gate CI ("advisory first, gate later if stable" --
observability-telemetry plan Phase 2).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

MARKER = "<!-- cairn-bench-advisory -->"
# Looser than the 15% CLI default: shared CI runners are noisy, and this is
# advisory -- the goal is signal for reviewers, not false alarms.
THRESHOLD = 0.25


def _load(path: str) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _render(current: dict, baseline: dict | None) -> str:
    corpus = current.get("corpus", {})
    lines = [
        "## Bench (advisory)",
        "",
        "> **Advisory only** -- this comparison never gates CI. Bench runs on",
        "> shared runners are noisy; treat large deltas as a prompt to run",
        "> `cairn bench --compare` locally, not as a verdict.",
        "",
        f"Corpus: {corpus.get('files', '?')} files, "
        f"{current.get('symbols', 0):,} symbols, "
        f"{current.get('edges', 0):,} edges.",
    ]
    if baseline is None:
        lines += [
            "",
            "No baseline available yet (first run or cache evicted); "
            "current timings:",
            "",
            "| operation | median ms | p95 ms |",
            "|---|---:|---:|",
        ]
        for op in current.get("ops", []):
            lines.append(f"| {op['name']} | {op['median_ms']:.1f} "
                         f"| {op['p95_ms']:.1f} |")
        return "\n".join(lines)

    lines += ["", f"Baseline from **{baseline.get('timestamp', 'unknown time')}**:",
              "", "| operation | baseline ms | current ms | delta |",
              "|---|---:|---:|---:|"]
    from cairn.bench import compare_reports

    deltas = compare_reports(baseline, current, threshold=THRESHOLD)
    if not deltas:
        lines.append("| _(no comparable operations)_ | | | |")
    n_regressed = 0
    for name, d in deltas.items():
        flag = " ⚠️" if d["regressed"] else ""
        if d["regressed"]:
            n_regressed += 1
        lines.append(f"| {name}{flag} | {d['baseline_ms']:.1f} "
                     f"| {d['current_ms']:.1f} | {d['delta_pct']:+.1f}% |")
    if n_regressed:
        lines += ["", f"⚠️ {n_regressed} operation(s) exceeded the "
                  f"{THRESHOLD:.0%} advisory threshold -- see note above."]
    return "\n".join(lines)


def main() -> int:
    current = _load("bench-current.json")
    baseline = _load("bench-baseline.json")

    if current is None:
        body = "_Bench did not produce a result this run (see the job log)._"
    else:
        body = _render(current, baseline)

    Path("bench-comment.md").write_text(f"{MARKER}\n\n{body}\n", encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(body + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
