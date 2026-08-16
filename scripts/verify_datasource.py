#!/usr/bin/env python3
"""Assert-mode validator for the T1 benchmark datasource pin (FR-001/AC2).

Reads ``benchmarks/datasource/manifest.json``, regenerates the synthetic
corpus per its recorded recipe (seed, complexity) at every declared size into
a throwaway temp root, and asserts the regenerated tree hashes to the pinned
value with the pinned counts. This is the check that makes the manifest a
promise rather than a wish: if any generation input drifts (a generator edit,
a seed change, even a Python RNG change) without the manifest being
re-minted, regeneration stops matching the pin and this exits non-zero
naming the mismatched fact (TC-003).

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

Usage:
    uv run python scripts/verify_datasource.py             # all declared sizes
    uv run python scripts/verify_datasource.py --size 100  # one size (CI cost)
    uv run python scripts/verify_datasource.py --json      # machine-readable
    uv run python scripts/verify_datasource.py --manifest /tmp/scratch.json

Exit codes (the contract the CI step depends on):
    0  verified -- every requested size matched tree-hash AND counts (TC-002)
    1  content drift -- a hash and/or count mismatch (TC-003)
    2  unusable manifest -- unreadable/invalid JSON, schema errors, or a
       --size the manifest does not declare (nothing was compared)
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


@dataclass
class VerifyReport:
    """Everything one invocation learned: schema verdict + per-size results."""

    manifest_path: str
    schema_errors: list[str] = field(default_factory=list)
    results: list[SizeResult] = field(default_factory=list)
    recipe: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.schema_errors and all(r.status == "ok" for r in self.results)

    def to_json(self) -> dict:
        return {
            "manifest": self.manifest_path,
            "ok": self.ok,
            "schema_errors": list(self.schema_errors),
            "recipe": dict(self.recipe),
            "results": [r.to_json() for r in self.results],
        }

    def exit_code(self) -> int:
        if self.schema_errors:
            return EXIT_MANIFEST
        return EXIT_OK if self.ok else EXIT_DRIFT


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


def _print_human(report: VerifyReport) -> None:
    """Human summary: one line per size, verdict last, failures to stderr."""
    if report.schema_errors:
        print(
            f"FAIL: manifest {report.manifest_path} is unusable -- nothing was compared:",
            file=sys.stderr,
        )
        for err in report.schema_errors:
            print(f"  {err}", file=sys.stderr)
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
    if report.ok:
        print(f"OK: {good}/{total} size(s) match the pinned tree-hash and counts (FR-001/AC2).")
    else:
        print(
            f"FAIL: {total - good}/{total} size(s) drifted from the manifest pin (TC-003).",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--size",
        type=int,
        default=None,
        help="verify only this declared size (CI cost control; default: all)",
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

    report = verify_manifest(
        args.manifest, sizes=[args.size] if args.size is not None else None
    )
    if args.json:
        print(json.dumps(report.to_json(), sort_keys=True))
    else:
        _print_human(report)
    return report.exit_code()


if __name__ == "__main__":
    sys.exit(main())
