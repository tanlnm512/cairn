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
  cairn bench --baseline DS-v1             # same, vs benchmarks/baselines/<DS-version>/

Exit-code contract: 0 = clean; 1 = usage / baseline-resolution error (unknown
``--baseline`` version, ``--baseline`` + ``--compare`` together, missing
``--compare`` file); 2 = regressions found by the comparison (the CI signal).
A machine-profile mismatch between the current run and a ``--baseline``
artifact only WARNS and never changes the exit code (D-005: warn, never
normalize) -- timing comparisons across machines stay advisory.
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

# Sentinel for a machine-profile field one side's stamp does not carry.
_UNSTAMPED = object()


def _profile_value(value: object) -> str:
    """Render one machine-profile value for the mismatch warning."""
    return "<unstamped>" if value is _UNSTAMPED else str(value)


def _resolve_baseline_file(version: str, suite: str) -> Path:
    """Resolve ``benchmarks/baselines/<version>/<suite>.json`` or exit 1.

    T014 (FR-004/AC1, TC-008): the error names the missing version, the
    directory searched, and any versions that DO exist; a version directory
    without this suite's artifact names the suite file it lacks. Called
    BEFORE any suite runs, so a typo fails in milliseconds instead of after
    a minutes-long suite ("fails promptly"). Exit 1 (usage/baseline error)
    stays distinct from the regression signal's exit 2.
    """
    from . import display
    from cairn.bench.datasource import default_baselines_root

    root = default_baselines_root()
    searched = root if root is not None else Path.cwd() / "benchmarks" / "baselines"
    version_dir = searched / version
    if root is None or not version_dir.is_dir():
        available = (
            sorted(p.name for p in searched.iterdir() if p.is_dir())
            if searched.is_dir()
            else []
        )
        hint = f" (available: {', '.join(available)})" if available else ""
        display.error(f"Unknown baseline dataset version '{version}': not found under {searched}{hint}")
        sys.exit(1)
    baseline_file = version_dir / f"{suite}.json"
    if not baseline_file.is_file():
        suites = sorted(p.name for p in version_dir.glob("*.json"))
        hint = f" (has: {', '.join(suites)})" if suites else ""
        display.error(
            f"Baseline '{version}' has no {suite} suite result: {baseline_file} missing{hint}"
        )
        sys.exit(1)
    return baseline_file


def _render_baseline_header(version: str, path: Path, data: dict) -> None:
    """Print the dataset-version header for a ``--baseline`` comparison.

    TC-007/AC1: names the resolved baseline and its stamp facts (dataset
    version + tree hash, cairn version, runner class) BEFORE the comparison
    table renders, so the reader knows what the numbers are against. A
    pre-T013 baseline file (no stamp keys) renders ``?`` placeholders rather
    than crashing -- the header degrades the same way the stamp does.
    """
    from . import display

    raw_dataset = data.get("dataset")
    raw_profile = data.get("machine_profile")
    dataset = raw_dataset if isinstance(raw_dataset, dict) else {}
    profile = raw_profile if isinstance(raw_profile, dict) else {}
    tree = dataset.get("tree_hash")
    tree_note = f" (tree {str(tree)[:12]}...)" if tree else ""
    display.info(f"Baseline {version} ({path})")
    display.dim(
        f"  dataset {dataset.get('name', '?')} @ {dataset.get('version') or version}{tree_note}"
    )
    display.dim(
        f"  cairn {data.get('cairn_version') or '?'}"
        f" · runner-class {profile.get('runner_class', '?')}"
        f" · arch {profile.get('arch', '?')}"
        f" · cpus {profile.get('cpu_count', '?')}"
    )


def _warn_machine_profile_mismatch(current: dict, stamped: object) -> None:
    """Loud advisory on ANY machine-profile field difference (D-005).

    TC-009: every mismatched field is named with both the baseline's and the
    current value. TC-010: an exact match prints nothing (no false-warning
    marker). TC-011: the warning is advisory only -- it never gates, so the
    exit code stays whatever the regression comparison alone decides. A
    baseline with no machine_profile stamp at all is "unknown", not
    "mismatched": noted, but without the MISMATCH marker.
    """
    from . import display

    if not isinstance(stamped, dict):
        display.warning(
            "Baseline carries no machine_profile stamp; profile comparability unknown."
        )
        return
    mismatched = []
    for key in sorted(set(current) | set(stamped)):
        base = stamped.get(key, _UNSTAMPED)
        cur = current.get(key, _UNSTAMPED)
        if base != cur:
            mismatched.append((key, base, cur))
    if not mismatched:
        return
    display.warning(
        "MACHINE-PROFILE MISMATCH -- timings are NOT comparable across machines;"
        " the comparison below is advisory."
    )
    for key, base, cur in mismatched:
        display.warning(
            f"  {key}: baseline {_profile_value(base)} vs current {_profile_value(cur)}"
        )
    display.warning("  (D-005: warned, not normalized; rendering the comparison anyway.)")



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
    "--baseline",
    default=None,
    help=(
        "Compare against benchmarks/baselines/<DS-version>/<suite>.json "
        "(committed, stamped baseline; mutually exclusive with --compare)."
    ),
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
    baseline,
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
    from cairn.bench.datasource import build_artifact_stamp

    # FR-004 stamp (D-006): computed once per invocation, applied beside the
    # timestamp at every payload site below -- never inside to_dict.
    stamp = build_artifact_stamp()

    # --baseline <DS-version> (T014, FR-004/AC1): resolve the baseline from
    # the committed benchmarks/baselines/ tree instead of an explicit
    # --compare path. Validated BEFORE any suite runs so an unknown version
    # fails in milliseconds, not after a minutes-long suite (TC-008 "fails
    # promptly"); the diff itself reuses the --compare flow verbatim below.
    if compare and baseline:
        display.error(
            "--baseline and --compare are mutually exclusive: pass a dataset "
            "version or an explicit baseline file, not both."
        )
        sys.exit(1)
    baseline_version = None
    if baseline:
        baseline_version = baseline
        compare = str(_resolve_baseline_file(baseline, suite))

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
            # when it was measured (consumed by the CI comparison + humans),
            # and what measured it: dataset identity + cairn version +
            # machine profile (FR-004).
            payload["timestamp"] = datetime.now(timezone.utc).isoformat()
            payload.update(stamp)
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
            # when it was measured (consumed by the CI comparison + humans),
            # and what measured it: dataset identity + cairn version +
            # machine profile (FR-004).
            payload["timestamp"] = datetime.now(timezone.utc).isoformat()
            payload.update(stamp)
            if not as_json:
                report.to_table()
            else:
                # Same content as report.to_json() plus the timestamp above.
                click.echo(json.dumps(payload, indent=2))

        # Save baseline if requested.
        if save:
            Path(save).write_text(json.dumps(payload, indent=2), encoding="utf-8")
            display.success(f"Saved baseline to {save}")

        # Compare against baseline if requested (explicit --compare file, or
        # --baseline <DS-version> resolved from benchmarks/baselines/, T014).
        if compare:
            baseline_path = Path(compare)
            if not baseline_path.exists():
                display.error(f"Baseline file not found: {compare}")
                sys.exit(1)
            baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
            if baseline_version is not None:
                # Dataset-version header + machine-profile check BEFORE the
                # comparison table (FR-004/AC1, TC-007/TC-009): the reader
                # sees what the numbers are against -- and any cross-machine
                # caveat -- before reading them. Advisory only (D-005): a
                # mismatch never changes the exit code.
                _render_baseline_header(baseline_version, baseline_path, baseline_data)
                _warn_machine_profile_mismatch(
                    stamp["machine_profile"], baseline_data.get("machine_profile")
                )
            if suite == "agent":
                deltas = compare_agent_reports(baseline_data, payload, threshold=threshold)
                base_key, cur_key, base_col, cur_col = (
                    "baseline_tokens", "current_tokens", "baseline tok", "current tok",
                )
            else:
                deltas = compare_reports(baseline_data, payload, threshold=threshold)
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
                    f"vs baseline {baseline_version or compare} (threshold {threshold:.0%})",
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
