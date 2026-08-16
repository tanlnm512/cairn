#!/usr/bin/env python3
"""Assert-mode validator for the T1 datasource pin + T2 size budgets.

Two independent assertions per run (FR-001/AC2 content, FR-002/AC4 size):

1. Content (FR-001/AC2): reads ``benchmarks/datasource/manifest.json``,
   regenerates the synthetic corpus per its recorded recipe (seed,
   complexity) at every declared size into a throwaway temp root, and
   asserts the regenerated tree hashes to the pinned value with the pinned
   counts. This is the check that makes the manifest a promise rather than a
   wish: if any generation input drifts (a generator edit, a seed change,
   even a Python RNG change) without the manifest being re-minted,
   regeneration stops matching the pin and this exits non-zero naming the
   mismatched fact (TC-003).

Why regeneration instead of committing the corpus: the corpus is a pure
function of (seed, size, complexity), so regenerating on any runner and
comparing the path-order-independent tree hash (decision D-003) proves
byte-for-byte equality without shipping ~28 MB of generated files across
sizes 100..5000. The comparison names the size and the mismatched fact
(hash / count field), not the offending file -- the manifest pins one
aggregate digest, so per-file attribution is impossible by design; the
expected/actual pair is the debugging handle.

Manifest schema errors are deliberately a DISTINCT failure class from
content drift: a manifest that fails ``validate_manifest`` was never
compared at all (exit 2), so CI can tell "the pin is stale" (exit 1) from
"the pin is malformed" (exit 2).

2. Size budgets (FR-002/AC4): EVERY invocation also asserts the committed
tree's byte size -- ``benchmarks/datasource/t2/`` <= 3072 KB (the vendored
snapshot, decision D-002) and ``benchmarks/datasource/`` <= 5120 KB total --
so one CI step gets content and budget enforcement for free. Trees are
measured as the sum of file sizes (``sum(f.stat().st_size)``), NOT
``du``-style disk blocks: block accounting varies by filesystem and block
size (APFS vs ext4 vs tmpfs), byte sums do not, so the budget means the same
thing on every runner. This check is load-bearing, not ceremony: the
pre-commit ``check-added-large-files --maxkb=500`` hook caps a single FILE,
and nothing else caps the tree -- fifty compliant 400 KB files would sail
past it straight into repo bloat (survey FR-002). ``--budget`` requests a
fast budget-only run (the manifest is still schema-validated -- cheap -- but
no corpus is regenerated); budgets are checked on every run regardless.

Usage:
    uv run python scripts/verify_datasource.py             # all declared sizes
    uv run python scripts/verify_datasource.py --size 100  # one size (CI cost)
    uv run python scripts/verify_datasource.py --budget    # budgets only (fast)
    uv run python scripts/verify_datasource.py --json      # machine-readable
    uv run python scripts/verify_datasource.py --manifest /tmp/scratch.json

Exit codes (the contract the CI step depends on):
    0  verified -- every requested size matched tree-hash AND counts AND both
       size budgets held (TC-002, TC-017)
    1  content drift -- a hash and/or count mismatch (TC-003)
    2  unusable manifest -- unreadable/invalid JSON, schema errors, or a
       --size the manifest does not declare (nothing was compared)
    3  size-budget breach -- the committed tree exceeds a budget (TC-018).
       Precedence when several fire: 2 > 1 > 3 -- an unusable pin means
       nothing was compared, and drift outranks size because the pin
       contract is the primary fact.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# Allow running from a source checkout without installing (same pattern as
# scripts/measure_memory_health.py). Guarded insert so repeated imports --
# e.g. the test suite loading this file as a module -- never grow sys.path.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from cairn.bench.corpus import corpus_stats, generate_corpus  # noqa: E402
from cairn.bench.datasource import load_manifest, tree_hash, validate_manifest  # noqa: E402

# Default pin location: the serial-spine artifact minted by T002 (and later
# extended by T019). Overridable via --manifest so scratch experiments and
# tests never touch the committed file.
DEFAULT_MANIFEST = REPO_ROOT / "benchmarks" / "datasource" / "manifest.json"

# Exit-code contract (see module docstring). Named because tests and the CI
# step assert against the meaning, not the number.
EXIT_OK = 0
EXIT_DRIFT = 1
EXIT_MANIFEST = 2
EXIT_BUDGET = 3

# Size budgets (FR-002/AC4), keyed by repo-relative path with limits in KiB
# (1 KB = 1024 bytes, matching du -sk and the pre-commit --maxkb convention).
# Ordered subtree-before-total so the report reads inside-out.
T2_BUDGET_KB = 3072  # the vendored snapshot alone: "<= 3 MB" (FR-002)
DATASOURCE_BUDGET_KB = 5120  # the whole datasource tree: "5 MB total" (FR-002)
BUDGETS: tuple[tuple[str, int], ...] = (
    ("benchmarks/datasource/t2", T2_BUDGET_KB),
    ("benchmarks/datasource", DATASOURCE_BUDGET_KB),
)
KB = 1024

# The count fields corpus_stats produces; the manifest's REQUIRED_COUNT_KEYS
# is the same triple -- iterate it explicitly so a mismatch report's field
# order is stable regardless of dict iteration order.
COUNT_FIELDS = ("files", "lines", "bytes")


@dataclass
class SizeResult:
    """Outcome of regenerating one declared size and comparing to its pin.

    ``status`` is exactly one of "ok" | "hash_mismatch" | "count_mismatch"
    (hash wins when both drift -- it is the stronger fact: counts can match
    while bytes differ, never the useful reverse).
    """

    size: int
    status: str
    expected_hash: str
    actual_hash: str
    actual_counts: dict = field(default_factory=dict)
    count_mismatches: list[dict] = field(default_factory=list)

    def to_json(self) -> dict:
        # actual_counts stays out of the JSON on purpose: for a mismatch the
        # per-field expected/actual pairs in count_mismatches carry the news,
        # and for an OK row the counts equal the manifest's own numbers.
        return {
            "size": self.size,
            "status": self.status,
            "expected_hash": self.expected_hash,
            "actual_hash": self.actual_hash,
            "count_mismatches": list(self.count_mismatches),
        }


@dataclass(frozen=True)
class BudgetResult:
    """Measured size of one budget scope against its limit (FR-002/AC4).

    ``actual_bytes`` is the raw fact; ``breached`` compares exact bytes (so
    rounding in the KB view can never flip a verdict) and the limit itself
    passes (the budget is "<=", not "<").
    """

    path: str
    limit_kb: int
    actual_bytes: int

    @property
    def breached(self) -> bool:
        return self.actual_bytes > self.limit_kb * KB

    @property
    def actual_kb(self) -> float:
        return round(self.actual_bytes / KB, 1)

    def to_json(self) -> dict:
        return {
            "path": self.path,
            "actual_kb": self.actual_kb,
            "limit_kb": self.limit_kb,
            "breached": self.breached,
        }


def tree_bytes(root: Path) -> int:
    """Total size of ``root``'s tree: the sum of every file's ``st_size``.

    Bytes, not disk blocks, on purpose: block accounting (allocation size,
    sparse holes, filesystem overhead) varies across APFS/ext4/tmpfs and
    would make the same tree measure differently per runner; a byte sum is
    portable. ``__pycache__`` directories are skipped: they are git-ignored
    build noise (a local graph build over t2/ drops ~1.4 MB of .pyc into the
    vendored tree; a fresh CI checkout has none), and this budget guards the
    COMMITTED tree (D-002), not a dev machine's litter. A missing root
    measures 0 -- an absent tree has no bytes to guard. The vendored snapshot
    contains no symlinks; if one appeared, ``stat()`` follows it and counts
    the target's size.
    """
    if not root.is_dir():
        return 0
    return sum(
        p.stat().st_size
        for p in root.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts
    )


def verify_budgets(
    repo_root: Path | str | None = None,
    budgets: tuple[tuple[str, int], ...] = BUDGETS,
) -> list[BudgetResult]:
    """Measure every budget scope under ``repo_root`` (default: this repo).

    ``repo_root`` resolves ``REPO_ROOT`` at CALL time so tests can point the
    whole budget pass at a scratch tree via ``monkeypatch.setattr(vd,
    "REPO_ROOT", tmp)`` (same injectable-paths pattern as ``verify_size``).
    Budget paths are repo-relative, so a scratch root mirrors the layout
    ``benchmarks/datasource/t2/...`` under its own tmp dir.
    """
    root = Path(REPO_ROOT) if repo_root is None else Path(repo_root)
    return [BudgetResult(path=rel, limit_kb=limit, actual_bytes=tree_bytes(root / rel)) for rel, limit in budgets]


@dataclass
class VerifyReport:
    """Everything one invocation learned: schema verdict + per-size + budgets.

    ``budgets`` is attached by :func:`main` (the CLI always checks them);
    :func:`verify_manifest` alone stays a pure manifest comparison so
    scratch-manifest tests are not coupled to the real tree's size.
    ``budget_only`` marks a ``--budget`` run: no corpus was regenerated, so
    empty ``results`` is expected, not a missing comparison.
    """

    manifest_path: str
    schema_errors: list[str] = field(default_factory=list)
    results: list[SizeResult] = field(default_factory=list)
    recipe: dict = field(default_factory=dict)
    budgets: list[BudgetResult] = field(default_factory=list)
    budget_only: bool = False

    @property
    def ok(self) -> bool:
        return (
            not self.schema_errors
            and all(r.status == "ok" for r in self.results)
            and not any(b.breached for b in self.budgets)
        )

    def to_json(self) -> dict:
        return {
            "manifest": self.manifest_path,
            "ok": self.ok,
            "schema_errors": list(self.schema_errors),
            "recipe": dict(self.recipe),
            "results": [r.to_json() for r in self.results],
            "budget_only": self.budget_only,
            "budgets": [b.to_json() for b in self.budgets],
        }

    def exit_code(self) -> int:
        if self.schema_errors:
            return EXIT_MANIFEST
        if any(r.status != "ok" for r in self.results):
            return EXIT_DRIFT
        if any(b.breached for b in self.budgets):
            return EXIT_BUDGET
        return EXIT_OK


def verify_size(t1: dict, size: int, root: Path) -> SizeResult:
    """Regenerate the corpus for one size under ``root`` and compare to its pin.

    Split out from :func:`verify_manifest` so tests drive the comparison as a
    library call against scratch manifests instead of via subprocess. The
    caller guarantees ``t1`` is schema-valid and ``size`` is declared.
    """
    entry = t1["entries"][str(size)]
    repo = generate_corpus(
        root / f"size_{size}", size, complexity=t1["complexity"], seed=t1["seed"]
    )
    actual_hash = tree_hash(repo)
    actual_counts = corpus_stats(repo)
    mismatches = [
        {"field": name, "expected": entry["counts"].get(name), "actual": actual_counts.get(name)}
        for name in COUNT_FIELDS
        if actual_counts.get(name) != entry["counts"].get(name)
    ]
    if actual_hash != entry["tree_hash"]:
        status = "hash_mismatch"
    elif mismatches:
        status = "count_mismatch"
    else:
        status = "ok"
    return SizeResult(
        size=size,
        status=status,
        expected_hash=entry["tree_hash"],
        actual_hash=actual_hash,
        actual_counts=actual_counts,
        count_mismatches=mismatches,
    )


def verify_manifest(
    manifest_path: Path | str, sizes: list[int] | None = None, workroot: Path | str | None = None
) -> VerifyReport:
    """Validate the manifest's schema, then verify each requested size.

    Args:
        manifest_path: the pin to verify against (DEFAULT_MANIFEST for the
            committed artifact; tests pass scratch copies).
        sizes: declared sizes to verify; None means every declared size.
            A size missing from the manifest is a schema-class error (exit 2)
            -- there is no entry to compare, so it is not drift.
        workroot: directory to regenerate corpora under; None creates and
            cleans up a temp dir. Injectable so tests can inspect artifacts.

    Returns:
        A :class:`VerifyReport`; ``report.exit_code()`` is the CLI's code.
    """
    report = VerifyReport(manifest_path=str(manifest_path))
    try:
        manifest = load_manifest(manifest_path)
    except (OSError, ValueError) as exc:
        # Unreadable/not-JSON is an unusable manifest, not drift.
        report.schema_errors.append(f"manifest: {exc}")
        return report
    report.schema_errors.extend(validate_manifest(manifest))
    t1 = manifest.get("t1")
    if not isinstance(t1, dict) or report.schema_errors:
        # Never regenerate against a pin whose shape we could not trust.
        return report
    report.recipe = {
        "generator_git_sha": t1.get("generator_git_sha"),
        "seed": t1.get("seed"),
        "complexity": t1.get("complexity"),
    }

    declared = t1["sizes"]
    requested = sorted(declared) if sizes is None else list(sizes)
    for size in requested:
        if size not in declared:
            report.schema_errors.append(
                f"--size {size} is not declared by the manifest (declared: {sorted(declared)})"
            )
    if report.schema_errors:
        return report

    if workroot is None:
        workroot = Path(tempfile.mkdtemp(prefix="cairn-verify-datasource-"))
        try:
            report.results = [verify_size(t1, s, workroot) for s in requested]
        finally:
            shutil.rmtree(workroot, ignore_errors=True)
    else:
        report.results = [verify_size(t1, s, Path(workroot)) for s in requested]
    return report


def _print_budgets(report: VerifyReport) -> None:
    """One line per budget scope, verdict last, failures to stderr.

    Budget lines are manifest-independent, so they print on every path --
    even when the manifest was unusable, the tree was still measured.
    """
    if not report.budgets:
        return
    width = max(len(b.path) for b in report.budgets)
    for b in report.budgets:
        if b.breached:
            print(
                f"FAIL: budget {b.path} breached: {b.actual_kb} KB exceeds "
                f"limit {b.limit_kb} KB (FR-002/AC4).",
                file=sys.stderr,
            )
        else:
            print(f"  budget {b.path:<{width}}: OK      {b.actual_kb} / {b.limit_kb} KB")
    good = sum(1 for b in report.budgets if not b.breached)
    total = len(report.budgets)
    if good == total:
        print(f"OK: {good}/{total} size budget(s) within limits (FR-002/AC4, TC-017).")
    else:
        print(
            f"FAIL: {total - good}/{total} size budget(s) breached (FR-002/AC4, TC-018).",
            file=sys.stderr,
        )


def _print_human(report: VerifyReport) -> None:
    """Human summary: one line per size, verdict last, failures to stderr."""
    if report.budget_only:
        # --budget: nothing was regenerated; the budgets are the whole story.
        _print_budgets(report)
        return
    if report.schema_errors:
        print(
            f"FAIL: manifest {report.manifest_path} is unusable -- nothing was compared:",
            file=sys.stderr,
        )
        for err in report.schema_errors:
            print(f"  {err}", file=sys.stderr)
        _print_budgets(report)
        return
    r = report.recipe
    print(
        f"manifest {report.manifest_path} -- "
        f"generator {r.get('generator_git_sha')}, seed {r.get('seed')}, "
        f"complexity {r.get('complexity')}"
    )
    for res in report.results:
        if res.status == "ok":
            counts = res.actual_counts
            print(
                f"  size {res.size:>5}: OK      hash {res.actual_hash[:12]}...  "
                f"counts files={counts.get('files')} lines={counts.get('lines')} "
                f"bytes={counts.get('bytes')}"
            )
        else:
            print(
                f"FAIL: size {res.size}: tree-hash mismatch",
                file=sys.stderr,
            )
            print(f"  expected {res.expected_hash}", file=sys.stderr)
            print(f"  actual   {res.actual_hash}", file=sys.stderr)
            for m in res.count_mismatches:
                print(
                    f"FAIL: size {res.size}: count mismatch: {m['field']} "
                    f"expected {m['expected']}, got {m['actual']}",
                    file=sys.stderr,
                )
    total = len(report.results)
    good = sum(1 for res in report.results if res.status == "ok")
    # Sizes-only verdict: report.ok folds budgets in too, so using it here
    # would mislabel a verified pin as "drifted" when only the tree is fat.
    if good == total:
        print(f"OK: {good}/{total} size(s) match the pinned tree-hash and counts (FR-001/AC2).")
    else:
        print(
            f"FAIL: {total - good}/{total} size(s) drifted from the manifest pin (TC-003).",
            file=sys.stderr,
        )
    _print_budgets(report)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    # Mutually exclusive: --size asks for regeneration at a size, --budget
    # asks for none at all. Together they would promise contradictory work.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--size",
        type=int,
        default=None,
        help="verify only this declared size (CI cost control; default: all)",
    )
    mode.add_argument(
        "--budget",
        action="store_true",
        help="check ONLY the size budgets -- no corpus regeneration "
        "(budgets are checked on every run regardless)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print a machine-readable result (per-size status + expected/actual)",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"manifest to verify against (default: {DEFAULT_MANIFEST})",
    )
    args = parser.parse_args(argv)

    if args.budget:
        sizes: list[int] | None = []  # budget-only: nothing to regenerate
    elif args.size is not None:
        sizes = [args.size]
    else:
        sizes = None

    report = verify_manifest(args.manifest, sizes=sizes)
    report.budget_only = args.budget
    # Budgets run on EVERY invocation (docstring: CI gets both for free).
    report.budgets = verify_budgets()
    if args.json:
        print(json.dumps(report.to_json(), sort_keys=True))
    else:
        _print_human(report)
    return report.exit_code()


if __name__ == "__main__":
    sys.exit(main())
