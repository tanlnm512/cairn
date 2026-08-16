"""cairn bench: performance, scalability, and agent-effort benchmarks.

Three suites, mirroring how ``cairn eval`` and ``cairn metrics`` already work:

  cairn bench                              # perf suite on a generated corpus
  cairn bench --suite perf                 # explicit (default)
  cairn bench --suite scaling --sizes 100,500,1000,5000
  cairn bench --suite agent                # tool calls + context cost vs grep
  cairn bench --workspace PATH             # perf/agent against an existing repo
  cairn bench --json                       # JSON for CI
  cairn bench --save baseline.json         # save a baseline
  cairn bench --compare baseline.json      # flag regressions > --threshold (15%)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import click

from .main import main


@main.command()
@click.option(
    "--suite",
    type=click.Choice(["perf", "scaling", "agent"]),
    default="perf",
    help="Which benchmark suite to run.",
)
@click.option(
    "--workspace",
    default=None,
    help="Target workspace (perf suite). Defaults to a generated synthetic corpus.",
)
@click.option(
    "--sizes",
    default="100,500,1000,5000",
    help="Comma-separated corpus sizes for the scaling suite.",
)
@click.option(
    "--n-files",
    default=300,
    type=int,
    help="Synthetic corpus size for the perf suite (when --workspace is unset).",
)
@click.option(
    "--complexity",
    type=click.Choice(["low", "medium", "high"]),
    default="medium",
    help="Synthetic corpus complexity.",
)
@click.option(
    "--embed-backend",
    default="hash",
    help="Embedding backend for the perf suite (default: dep-free hash).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON (for CI / piping).")
@click.option("--save", default=None, help="Save the result JSON to this file (baseline).")
@click.option(
    "--compare",
    default=None,
    help="Compare against a saved baseline JSON file; flag regressions.",
)
@click.option(
    "--threshold",
    default=0.15,
    type=float,
    help="Regression threshold for --compare (fraction; default 0.15 = 15%).",
)
@click.option("--repeats", default=3, type=int, help="Timed repeats per operation (perf).")
@click.option(
    "--runs",
    default=3,
    type=int,
    help="Measured runs per task (agent suite; medians reported).",
)
def bench(
    suite,
    workspace,
    sizes,
    n_files,
    complexity,
    embed_backend,
    as_json,
    save,
    compare,
    threshold,
    repeats,
    runs,
):
    """Run performance, scalability, or agent-effort benchmarks."""
    from . import display
    from cairn.bench import (
        generate_corpus,
        run_perf_suite,
        run_scaling_suite,
        compare_reports,
    )
    from cairn.bench.agent_suite import compare_agent_reports, run_agent_suite

    tmp_root = None
    tmp_db = None  # cg_bench_db_* dir created only by the perf/agent suites
    try:
        if suite == "scaling":
            size_list = [int(s.strip()) for s in sizes.split(",") if s.strip()]
            tmp_root = Path(tempfile.mkdtemp(prefix="cg_bench_"))
            report = run_scaling_suite(
                tmp_root,
                sizes=size_list,
                complexity=complexity,
                embed_backend=embed_backend,
            )
            payload = report.to_dict()
            # Stamp the machine-readable payload so a saved baseline records
            # when it was measured (consumed by the CI comparison + humans).
            payload["timestamp"] = datetime.now(timezone.utc).isoformat()
            if not as_json:
                report.to_table()
            else:
                # Same content as report.to_json() plus the timestamp above.
                click.echo(json.dumps(payload, indent=2))
        else:
            # Perf or agent suite: use the given workspace, else generate a corpus.
            if workspace:
                ws = workspace
            else:
                tmp_root = Path(tempfile.mkdtemp(prefix="cg_bench_"))
                corpus = generate_corpus(tmp_root, n_files, complexity=complexity)
                ws = str(corpus)
            db_path_dir = Path(tempfile.mkdtemp(prefix="cg_bench_db_"))
            tmp_db = db_path_dir
            db_path = str(db_path_dir / "bench.db")
            os.environ["CAIRN_DB"] = db_path
            if suite == "agent":
                report = run_agent_suite(
                    ws,
                    db_path,
                    runs=runs,
                    embed_backend=embed_backend,
                )
            else:
                report = run_perf_suite(
                    ws,
                    db_path,
                    embed_backend=embed_backend,
                    repeats=repeats,
                )
            payload = report.to_dict()
            # Stamp the machine-readable payload so a saved baseline records
            # when it was measured (consumed by the CI comparison + humans).
            payload["timestamp"] = datetime.now(timezone.utc).isoformat()
            if not as_json:
                report.to_table()
            else:
                # Same content as report.to_json() plus the timestamp above.
                click.echo(json.dumps(payload, indent=2))

        # Save baseline if requested.
        if save:
            Path(save).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            display.success(f"Saved baseline to {save}")

        # Compare against baseline if requested.
        if compare:
            baseline_path = Path(compare)
            if not baseline_path.exists():
                display.error(f"Baseline file not found: {compare}")
                sys.exit(1)
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
            if suite == "agent":
                deltas = compare_agent_reports(baseline, payload, threshold=threshold)
                base_key, cur_key, base_col, cur_col = (
                    "baseline_tokens", "current_tokens", "baseline tok", "current tok",
                )
            else:
                deltas = compare_reports(baseline, payload, threshold=threshold)
                base_key, cur_key, base_col, cur_col = (
                    "baseline_ms", "current_ms", "baseline ms", "current ms",
                )
            if deltas:
                rows = []
                any_regressed = False
                for name, d in deltas.items():
                    marker = " ⚠ REGRESSED" if d["regressed"] else ""
                    if d["regressed"]:
                        any_regressed = True
                    rows.append([
                        name + marker,
                        f"{d[base_key]:.1f}",
                        f"{d[cur_key]:.1f}",
                        f"{d['delta_pct']:+.1f}%",
                    ])
                display.print_table(
                    f"vs baseline {compare} (threshold {threshold:.0%})",
                    columns=["operation", base_col, cur_col, "delta"],
                    rows=rows,
                )
                if any_regressed:
                    sys.exit(2)  # CI signal: regressions found
            else:
                display.success("No comparable operations vs baseline.")
    finally:
        if tmp_root and tmp_root.exists():
            shutil.rmtree(tmp_root, ignore_errors=True)
        # The perf suite creates a separate cg_bench_db_* dir for its SQLite
        # DB path; clean that up too or every `cairn bench` (no --workspace)
        # would orphan a temp directory.
        if tmp_db is not None and tmp_db.exists():
            shutil.rmtree(tmp_db, ignore_errors=True)
