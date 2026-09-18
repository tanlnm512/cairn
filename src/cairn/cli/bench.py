"""cairn bench: performance, scalability, and agent-effort benchmarks."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
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


def _profile_class(key: str, value: object) -> object:
    """Bucket a profile value into its comparison CLASS (rolling baselines).

    The rolling CI baseline (bench-baseline.json minted on main, compared
    PR-over-PR on the same hosted pool) must not fire the profile-mismatch
    warning for fields that merely identify *which instance of the same class*
    produced the number:

    * ``runner_class`` -- GitHub stamps ``ci-<slug(RUNNER_NAME)>`` and the
      hosted pool names instances with trailing digits
      (``ci-github-actions-12`` vs ``ci-github-actions-1000001128``).
      Strip the trailing instance number; different runner *names* still
      mismatch, and ``reference-local`` never buckets with anything.
    * ``os`` -- hosted runners drift kernel revisions over time
      (``Linux-6.17.0-1022-azure...`` vs ``Linux-6.20...``). Two Linux
      kernels are the same hosted class; macOS point releases stay exact
      (the reference minter's OS delta is a real comparability fact).

    Everything else (arch, cpu, cpu_count) stays exact-equality -- those
    are real comparability signals. This is metadata bucketing only: the
    TIMINGS themselves are never normalized.
    """
    if value is _UNSTAMPED:
        return value
    text = str(value)
    if key == "runner_class":
        return re.sub(r"-\d+$", "", text)
    if key == "os" and text.startswith("Linux-"):
        return "Linux"
    return text


def _resolve_baseline_file(version: str, suite: str) -> Path:
    """Resolve ``benchmarks/baselines/<version>/<suite>.json`` or exit 1.

    The error names the missing version, the directory searched, and any
    versions that DO exist; a version directory without this suite's
    artifact names the suite file it lacks. Called BEFORE any suite runs,
    so a typo fails in milliseconds instead of after a minutes-long suite
    ("fails promptly"). Exit 1 (usage/baseline error) stays distinct from
    the regression signal's exit 2.
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


# Task count for the swe-bench smoke slice (--smoke).
SWE_BENCH_SMOKE_TASKS = 2


def _default_swe_bench_manifest() -> Path | None:
    """Locate the frozen swe-bench pin manifest; None when absent.

    Same two-candidate precedence as the datasource defaults: the working
    directory first (how CI and maintainers invoke ``cairn bench``), then
    the source tree the package lives in.
    """
    name = Path("benchmarks") / "datasource" / "swe-bench-subset.json"
    candidates = [
        Path.cwd() / name,
        Path(__file__).resolve().parents[3] / name,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _parse_swe_bench_slice(text: str) -> slice:
    """Parse a START:END positional slice over the pinned subset; exits 1 on
    malformed bounds."""
    from . import display

    parts = text.split(":")
    if not text.strip() or len(parts) != 2:
        display.error(f"--slice expects START:END (e.g. 0:2), got {text!r}")
        sys.exit(1)
    try:
        start = int(parts[0]) if parts[0].strip() else 0
        stop = int(parts[1]) if parts[1].strip() else None
    except ValueError:
        display.error(f"--slice expects integer bounds, got {text!r}")
        sys.exit(1)
    if start < 0 or (stop is not None and stop < 0):
        display.error(f"--slice bounds must be non-negative, got {text!r}")
        sys.exit(1)
    return slice(start, stop)


def _swe_bench_workspaces(
    tasks: list[dict], cache_root: Path | None = None, *, quiet: bool = False
) -> dict[str, str]:
    """Map each task's instance_id to its checked-out workspace tree.

    Workspaces live under ``<CAIRN_HOME>/cache/swe-bench/<repo>-<base_commit>``
    -- keyed by the commit sha, so a directory's presence means that exact
    content is already checked out and the clone is skipped (warm reruns
    never touch the network). The clone stages into a pid-suffixed temp dir
    and is renamed into place; a run that loses the rename to a concurrent
    run uses the winner's checkout. ``quiet`` suppresses progress output
    (machine-readable stdout).
    """
    from . import display

    if cache_root is None:
        home = os.environ.get("CAIRN_HOME", str(Path.home() / ".cairn"))
        cache_root = Path(os.path.expanduser(home)) / "cache" / "swe-bench"
    cache_root.mkdir(parents=True, exist_ok=True)
    git_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
    workspaces: dict[str, str] = {}
    for task in tasks:
        repo, sha = task["repo"], task["base_commit"]
        target = cache_root / f"{repo.replace('/', '__')}-{sha}"
        workspaces[task["instance_id"]] = str(target)
        if (target / ".git").is_dir():
            continue
        if not quiet:
            display.dim(f"swe-bench: checking out {repo} @ {sha[:12]} (one-time clone)...")
        staging = cache_root / f".tmp-{sha[:12]}-{os.getpid()}"
        shutil.rmtree(staging, ignore_errors=True)
        try:
            subprocess.run(
                ["git", "clone", "--quiet", "--filter=blob:none",
                 f"https://github.com/{repo}.git", str(staging)],
                env=git_env, check=True, capture_output=True, text=True,
            )
            subprocess.run(
                ["git", "-C", str(staging), "checkout", "--quiet", sha],
                env=git_env, check=True, capture_output=True, text=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("checking out SWE-bench workspaces needs git on PATH") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip().splitlines()
            raise RuntimeError(
                f"checkout of {repo} @ {sha[:12]} failed: "
                f"{detail[-1] if detail else exc}"
            ) from exc
        try:
            os.replace(staging, target)
        except OSError:
            # Target already populated (e.g. by a concurrent run): use it.
            shutil.rmtree(staging, ignore_errors=True)
    return workspaces


def _swe_bench_stamp(
    base: dict, manifest: dict, manifest_path: Path, slice_label: str | None
) -> dict:
    """Swe-bench artifact stamp: pinned revision + manifest digest + size.

    Replaces only the ``dataset`` block of the invocation stamp; the shared
    stamp builder's output gains no keys.
    """
    from cairn.bench.swe_bench import DATASET_NAME

    dataset = {
        "name": manifest.get("dataset", DATASET_NAME),
        "schema": manifest["schema"],
        "revision_sha": manifest["dataset_revision_sha"],
        "manifest_digest": hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest(),
        "instance_count": len(manifest["subset"]),
    }
    if slice_label:
        dataset["slice"] = slice_label
    return {**base, "dataset": dataset}


def _persistable_swe_bench_report(report: dict) -> dict:
    """Suite report kept for persistence: the wall-clock figures removed.

    Reruns must persist identical reports, so only the deterministic call and
    token figures are kept; ``wall_ms``/``time_ratio`` never persist.
    """
    def arm(arm_dict: dict) -> dict:
        return {k: v for k, v in arm_dict.items() if k != "wall_ms"}

    tasks = []
    for row in report["tasks"]:
        row = dict(row)
        row["cairn"] = arm(row["cairn"])
        row["control"] = arm(row["control"])
        row["reduction"] = {
            k: v for k, v in row["reduction"].items() if k != "time_ratio"
        }
        tasks.append(row)
    return {
        "tasks": tasks,
        "medians": {name: arm(arm_dict) for name, arm_dict in report["medians"].items()},
        "runs": report["runs"],
        "chars_per_token": report["chars_per_token"],
    }


def _render_swe_bench_report(payload: dict) -> None:
    """Per-task effort rows for both arms plus the cross-task medians."""
    from . import display

    rows = []
    for row in payload["tasks"]:
        cairn, control = row["cairn"], row["control"]
        rows.append([
            row["instance_id"],
            str(cairn["tool_calls"]),
            str(control["tool_calls"]),
            f"{cairn['est_tokens']:,}",
            f"{control['est_tokens']:,}",
            f"{row['reduction']['tokens_pct']:.0f}%",
        ])
    med_cairn = payload["medians"]["cairn"]
    med_control = payload["medians"]["control"]
    rows.append([
        "MEDIAN",
        str(med_cairn["tool_calls"]),
        str(med_control["tool_calls"]),
        f"{med_cairn['est_tokens']:,}",
        f"{med_control['est_tokens']:,}",
        (
            f"{(1 - med_cairn['est_tokens'] / med_control['est_tokens']) * 100:.0f}%"
            if med_control["est_tokens"]
            else "-"
        ),
    ])
    display.print_table(
        f"cairn SWE-bench benchmark  ({len(payload['tasks'])} tasks,"
        f" {payload['runs']} runs, tokens = chars/{payload['chars_per_token']})",
        columns=[
            "task", "cairn calls", "grep calls",
            "cairn tok", "grep tok", "tok saved",
        ],
        rows=rows,
    )


def _render_baseline_header(version: str, path: Path, data: dict) -> None:
    """Print the dataset-version header for a ``--baseline`` comparison.

    Names the resolved baseline and its stamp facts (dataset
    version + tree hash, cairn version, runner class) BEFORE the comparison
    table renders, so the reader knows what the numbers are against. A
    baseline file without stamp keys renders ``?`` placeholders rather
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
    """Loud advisory on machine-profile CLASS differences.

    Every mismatched field is named with both the baseline's and
    the current value. An exact match prints nothing (no false-warning
    marker). The warning is advisory only -- it never gates, so the
    exit code stays whatever the regression comparison alone decides. A
    baseline with no machine_profile stamp at all is "unknown", not
    "mismatched": noted, but without the MISMATCH marker.

    Fields are compared at class level (``_profile_class``): the rolling
    CI baseline runs PR-over-PR on GitHub's hosted pool, where
    ``runner_class`` carries a per-instance suffix and ``os`` carries a
    drifting kernel revision -- same class, not a mismatch. Cross-class
    pairs (``reference-local`` vs ``ci-*``, macOS vs Linux, x86_64 vs
    arm64, different CPU counts) still warn with both values.
    """
    from . import display

    if not isinstance(stamped, dict):
        display.warning(
            "Baseline carries no machine_profile stamp; profile comparability unknown."
        )
        return
    mismatched = []
    for key in sorted(set(current) | set(stamped)):
        base_class = _profile_class(key, stamped.get(key, _UNSTAMPED))
        cur_class = _profile_class(key, current.get(key, _UNSTAMPED))
        if base_class != cur_class:
            # Display the RAW stamps (exactly what each side recorded);
            # only the comparison buckets to class level.
            mismatched.append((key, stamped.get(key, _UNSTAMPED),
                               current.get(key, _UNSTAMPED)))
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
    display.warning("  (warned, not normalized; rendering the comparison anyway.)")



@main.command()
@click.option(
    "--suite",
    type=click.Choice(["perf", "scaling", "agent", "swe-bench"]),
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
    help="Measured runs per task (agent/swe-bench suites; medians reported).",
)
@click.option(
    "--slice",
    "slice_expr",
    default=None,
    help="Swe-bench suite: START:END positional slice of the pinned subset (e.g. 0:2).",
)
@click.option(
    "--manifest",
    default=None,
    help=(
        "Swe-bench suite: pin manifest path "
        "(default: benchmarks/datasource/swe-bench-subset.json)."
    ),
)
@click.option(
    "--smoke",
    is_flag=True,
    default=False,
    help=f"Swe-bench suite: run the first {SWE_BENCH_SMOKE_TASKS} pinned tasks.",
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
    slice_expr,
    manifest,
    smoke,
):
    """Run performance, scalability, agent-effort, or SWE-bench benchmarks."""
    from . import display
    from cairn.bench import (
        generate_corpus,
        run_perf_suite,
        run_scaling_suite,
        compare_reports,
    )
    from cairn.bench.agent_suite import compare_agent_reports, run_agent_suite
    from cairn.bench.datasource import build_artifact_stamp

    # Swe-bench-only flags on another suite are a usage error, not a silent
    # ignore; the manifest itself is resolved + validated before any suite
    # work so a bad pin fails promptly (same discipline as --baseline).
    for flag, value in (("--smoke", smoke), ("--slice", slice_expr), ("--manifest", manifest)):
        if value and suite != "swe-bench":
            display.error(f"{flag} applies to the swe-bench suite only.")
            sys.exit(1)
    swe_slice: slice | None = None
    slice_label: str | None = None
    manifest_path: Path | None = None
    pin_manifest: dict | None = None
    if suite == "swe-bench":
        if smoke and slice_expr:
            display.error("--smoke and --slice are mutually exclusive: pass one, not both.")
            sys.exit(1)
        from cairn.bench.swe_bench import load_pin_manifest

        manifest_path = Path(manifest) if manifest else _default_swe_bench_manifest()
        if manifest_path is None:
            display.error(
                "Pin manifest not found: benchmarks/datasource/swe-bench-subset.json "
                "(or pass --manifest PATH)."
            )
            sys.exit(1)
        try:
            pin_manifest = load_pin_manifest(manifest_path)
        except (OSError, ValueError) as exc:
            display.error(str(exc))
            sys.exit(1)
        if smoke:
            swe_slice = slice(0, SWE_BENCH_SMOKE_TASKS)
            slice_label = f"0:{SWE_BENCH_SMOKE_TASKS}"
        elif slice_expr:
            swe_slice = _parse_swe_bench_slice(slice_expr)
            slice_label = slice_expr
        else:
            swe_slice = slice(None)
        start, stop, _ = swe_slice.indices(len(pin_manifest["subset"]))
        if start >= stop:
            display.error(
                f"--slice {slice_label} selects 0 of {len(pin_manifest['subset'])} "
                "pinned tasks"
            )
            sys.exit(1)

    # Artifact stamp: computed once per invocation, applied beside the
    # timestamp at every payload site below -- never inside to_dict.
    stamp = build_artifact_stamp()

    # --baseline <DS-version>: resolve the baseline from
    # the committed benchmarks/baselines/ tree instead of an explicit
    # --compare path. Validated BEFORE any suite runs so an unknown version
    # fails in milliseconds, not after a minutes-long suite ("fails
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
            # machine profile.
            payload["timestamp"] = datetime.now(timezone.utc).isoformat()
            payload.update(stamp)
            if not as_json:
                report.to_table()
            else:
                # Same content as report.to_json() plus the timestamp above.
                click.echo(json.dumps(payload, indent=2))
        elif suite == "swe-bench":
            # Manifest already resolved + validated above (fail promptly);
            # the first run fetches the pinned split and clones the task
            # repos, warm reruns are fully offline.
            from cairn.bench.swe_bench import load_tasks
            from cairn.bench.swe_bench_suite import run_swe_bench_suite

            if not as_json:
                display.info(
                    f"Loading pinned task rows (revision "
                    f"{pin_manifest['dataset_revision_sha'][:12]})..."
                )
            try:
                tasks = load_tasks(pin_manifest)
            except (ImportError, OSError, ValueError) as exc:
                display.error(str(exc))
                sys.exit(1)
            start, stop, _ = swe_slice.indices(len(tasks))
            tasks = tasks[start:stop]
            try:
                workspaces = _swe_bench_workspaces(tasks, quiet=as_json)
                db_path_dir = Path(tempfile.mkdtemp(prefix="cg_bench_db_"))
                tmp_db = db_path_dir
                report = run_swe_bench_suite(
                    tasks,
                    workspaces,
                    str(db_path_dir / "bench.db"),
                    runs=runs,
                )
            except (RuntimeError, ValueError) as exc:
                display.error(str(exc))
                sys.exit(1)
            # No timestamp and no wall-clock figures here: the persisted
            # swe-bench report must be identical across reruns; the stamp
            # carries the pin identity instead.
            payload = _persistable_swe_bench_report(report)
            payload.update(_swe_bench_stamp(stamp, pin_manifest, manifest_path, slice_label))
            if not as_json:
                _render_swe_bench_report(payload)
            else:
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
            # machine profile.
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
        # --baseline <DS-version> resolved from benchmarks/baselines/).
        if compare:
            baseline_path = Path(compare)
            if not baseline_path.exists():
                display.error(f"Baseline file not found: {compare}")
                sys.exit(1)
            baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
            if baseline_version is not None:
                # Dataset-version header + machine-profile check BEFORE the
                # comparison table: the reader
                # sees what the numbers are against -- and any cross-machine
                # caveat -- before reading them. Advisory only: a
                # mismatch never changes the exit code.
                _render_baseline_header(baseline_version, baseline_path, baseline_data)
                _warn_machine_profile_mismatch(
                    stamp["machine_profile"], baseline_data.get("machine_profile")
                )
            if suite in ("agent", "swe-bench"):
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
