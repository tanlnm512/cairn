"""cg bench: performance + scalability benchmarks.

Two suites, mirroring how ``cg eval`` and ``cg metrics`` already work:

  cg bench                              # perf suite on a generated corpus
  cg bench --suite perf                 # explicit (default)
  cg bench --suite scaling --sizes 100,500,1000,5000
  cg bench --workspace PATH             # perf against an existing repo
  cg bench --json                       # JSON for CI
  cg bench --save baseline.json         # save a baseline
  cg bench --compare baseline.json      # flag regressions > --threshold (15%)
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

import click

from .main import main


@main.command()
@click.option(
    "--suite",
    type=click.Choice(["perf", "scaling"]),
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
):
    """Run performance or scalability benchmarks."""
    from . import display
    from codegraph.bench import (
        generate_corpus,
        run_perf_suite,
        run_scaling_suite,
        compare_reports,
    )

    tmp_root = None
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
            if not as_json:
                report.to_table()
            else:
                click.echo(report.to_json())
        else:
            # Perf suite: use the given workspace, else generate a corpus.
            if workspace:
                ws = workspace
            else:
                tmp_root = Path(tempfile.mkdtemp(prefix="cg_bench_"))
                corpus = generate_corpus(tmp_root, n_files, complexity=complexity)
                ws = str(corpus)
            db_path = str(Path(tempfile.mkdtemp(prefix="cg_bench_db_")) / "bench.db")
            os.environ["CODEGRAPH_DB"] = db_path
            report = run_perf_suite(
                ws,
                db_path,
                embed_backend=embed_backend,
                repeats=repeats,
            )
            payload = report.to_dict()
            if not as_json:
                report.to_table()
            else:
                click.echo(report.to_json())

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
            deltas = compare_reports(baseline, payload, threshold=threshold)
            if deltas:
                rows = []
                any_regressed = False
                for name, d in deltas.items():
                    marker = " ⚠ REGRESSED" if d["regressed"] else ""
                    if d["regressed"]:
                        any_regressed = True
                    rows.append([
                        name + marker,
                        f"{d['baseline_ms']:.1f}",
                        f"{d['current_ms']:.1f}",
                        f"{d['delta_pct']:+.1f}%",
                    ])
                display.print_table(
                    f"vs baseline {compare} (threshold {threshold:.0%})",
                    columns=["operation", "baseline ms", "current ms", "delta"],
                    rows=rows,
                )
                if any_regressed:
                    sys.exit(2)  # CI signal: regressions found
            else:
                display.success("No comparable operations vs baseline.")
    finally:
        if tmp_root and tmp_root.exists():
            shutil.rmtree(tmp_root, ignore_errors=True)
