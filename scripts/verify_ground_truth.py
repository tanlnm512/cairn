#!/usr/bin/env python3
"""Ground-truth validator for the T2 dataset: re-verify every expectation on a fresh build.

FR-003/AC5, TC-021/TC-022. The D-004 ground-truth pair under
``benchmarks/datasource/t2/ground_truth/`` (queries.jsonl + expectations.tsv,
authored by T011) promises that each query's expected ``file#symbol`` ids are
real facts about the vendored yarl snapshot. This script re-checks that
promise from scratch:

1. Builds a FRESH graph over ``benchmarks/datasource/t2/yarl`` -- the tree is
   copied to a throwaway workspace and the COPY gets the empty ``.git``
   scanner marker exactly as ``generate_corpus`` does for T1
   (``bench/corpus.py:50-52``); a marker is never written into the committed
   tree (git does not track empty dirs anyway -- tech-spec pitfall).
2. For every expectation, runs the T010 matcher -- ``cairn.eval.match_rank``,
   the two-tier identity-first matcher -- against the fresh build's
   retrieval surfaces. Not a reimplementation: the loader
   (``load_ground_truth``) and the matcher come from ``src/cairn/eval.py``
   verbatim, and the L5 knowledge surface goes through eval's own
   ``_retrieve_l5`` when a bundle is in play.

Verification surfaces (why presence, not a top-10 text-retrieval window):

* The staleness question is "does a fresh build still contain what the
  dataset promises" (TC-022's scratch tamper: an expectation pointing at a
  symbol *absent from the snapshot*), not "does lexical search rank it in the
  top-10 today". Natural-language queries against a bare graph return
  nothing through ``_retrieve_l1`` -- FTS5 MATCH is an implicit AND over the
  sentence's tokens and a fresh build has no embeddings -- so a k=10 window
  would name every entry stale and detect nothing. Recall@10/MRR *scoring*
  over this pair is the baseline job (T015), not the staleness gate.
* L1 (code) expectations are verified against the graph's full symbol
  inventory -- the same ``symbols``/``files`` rows ``find_definition``
  reads -- with ``match_rank(exp, inventory, k=len(inventory))``: tier-1
  (file suffix + exact symbol name) dominates; the substring tier keeps
  matcher parity.
* L5 (knowledge) expectations: T011 authored them as code-symbol ids
  verified against a graph build (no OKF bundle exists for the snapshot),
  so by default they verify through the same graph surface. When an OKF
  knowledge bundle IS available (``--bundle <path>`` or auto-discovered at
  ``<t2>/.knowledge``), its results -- retrieved via eval's
  ``_retrieve_l5`` -- join the verification pool: an expectation verifies
  if it matches EITHER surface.

D-010 -- the validator NEVER rewrites expectations. Stale sets ship as
DS-v2; this script's job is to name the stale entries (query text + missing
symbol) and exit non-zero so a human decides.

Usage:
    uv run python scripts/verify_ground_truth.py                 # committed pair
    uv run python scripts/verify_ground_truth.py --json          # machine-readable
    uv run python scripts/verify_ground_truth.py --dataset /tmp/scratch_gt
    uv run python scripts/verify_ground_truth.py --bundle /tmp/scratch/.knowledge

Exit codes (the contract; precedence 2 > 1 > 0 -- an infrastructure failure
means *nothing was verified*, so it outranks any stale verdict):
    0  verified -- every expectation matched a surface of the fresh build
       (TC-021: all-green summary)
    1  stale entries -- one or more expectations matched nothing; each is
       named by query text + missing symbol_id (+ grade/level/kind).
       Includes unverified grade-1 rows: AC5 says every expectation verifies
       or names the stale entry (TC-022), so grade-1 misses are consciously
       listed, grade-2 misses first.
    2  infrastructure -- missing/unreadable dataset or snapshot dir, a
       malformed dataset (``load_ground_truth`` ValueError), a failed or
       degraded graph build (0 repos, parse errors), or a ``--bundle`` root
       that does not exist. A missing *auto-discovered* bundle is NOT an
       error (L5 falls back to the graph surface); a missing *requested*
       bundle IS -- eval's L5 path would silently 0.0/0.0 every L5 query
       there (``eval.py``'s missing-bundle early return), which is an
       infrastructure condition, never a stale verdict.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Allow running from a source checkout without installing (same pattern as
# scripts/verify_datasource.py). Guarded insert so repeated imports -- e.g.
# the test suite loading this file as a module -- never grow sys.path.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from cairn.eval import load_ground_truth, match_rank  # noqa: E402
import cairn.eval as eval_mod  # noqa: E402

# Default locations: the T011-authored pair and the T005-vendored snapshot.
DEFAULT_DATASET = REPO_ROOT / "benchmarks" / "datasource" / "t2" / "ground_truth"
DEFAULT_SNAPSHOT = REPO_ROOT / "benchmarks" / "datasource" / "t2" / "yarl"

# Exit-code contract (see module docstring). Named because tests and CI assert
# against the meaning, not the number -- same convention as verify_datasource.
EXIT_OK = 0
EXIT_STALE = 1
EXIT_INFRA = 2

# The knowledge bundle auto-discovery point: <t2-root>/.knowledge (beside the
# yarl/ snapshot and the ground_truth/ pair). Committed today: absent.
BUNDLE_DIRNAME = ".knowledge"

# Cap for bundle retrieval when a bundle is in play: the verification pool is
# presence-based, so ask the bundle for far more than the graded window.
BUNDLE_RETRIEVE_LIMIT = 1000


@dataclass(frozen=True)
class StaleEntry:
    """One expectation that matched no surface of the fresh build.

    Carries everything TC-022 requires (query text + missing symbol) plus the
    grade/level/kind context needed to triage: grade-2 rows are primary
    targets and are listed before grade-1 rows in the human report.
    """

    query_id: str
    query_text: str
    level: str
    kind: str
    symbol_id: str
    grade: int

    def to_json(self) -> dict:
        return {
            "query_id": self.query_id,
            "query_text": self.query_text,
            "level": self.level,
            "kind": self.kind,
            "symbol_id": self.symbol_id,
            "grade": self.grade,
        }


@dataclass
class VerifyReport:
    """Everything one invocation learned: infra verdict + per-kind summary + stale list.

    ``errors`` non-empty means infrastructure failed (exit 2) and nothing was
    verified: ``summary``/``stale``/``build`` stay empty rather than carrying
    a verdict made against an untrusted build.
    """

    dataset: str
    snapshot: str
    bundle: Optional[str] = None
    bundle_status: str = "none"  # "explicit" | "auto" | "none"
    errors: List[str] = field(default_factory=list)
    build: Optional[Dict[str, Any]] = None
    # summary[level][kind] -> {"queries", "expectations", "verified", "stale"}
    summary: Dict[str, Dict[str, Dict[str, int]]] = field(default_factory=dict)
    totals: Dict[str, int] = field(default_factory=dict)
    stale: List[StaleEntry] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and not self.stale

    def exit_code(self) -> int:
        if self.errors:
            return EXIT_INFRA
        if self.stale:
            return EXIT_STALE
        return EXIT_OK

    def to_json(self) -> dict:
        return {
            "dataset": self.dataset,
            "snapshot": self.snapshot,
            "bundle": self.bundle,
            "bundle_status": self.bundle_status,
            "ok": self.ok,
            "exit_code": self.exit_code(),
            "errors": list(self.errors),
            "build": dict(self.build) if self.build else None,
            "summary": self.summary,
            "totals": dict(self.totals),
            "stale": [s.to_json() for s in self.stale],
        }


def build_fresh_graph(snapshot: Path, workroot: Path) -> tuple[Dict[str, Any], Path]:
    """Copy ``snapshot`` into ``workroot`` and build a graph over the copy.

    The copy (never the committed tree) receives the empty ``.git`` scanner
    marker, the same idiom as ``generate_corpus`` / the T008 smoke fixture:
    git does not track empty dirs, so the committed snapshot cannot carry the
    marker itself.

    Raises ``ValueError`` (an infrastructure-class error) when the snapshot is
    missing, the build raises, or the build comes out degraded (0 repos or
    parse errors) -- a partial build must not be allowed to mint stale
    verdicts.
    """
    from cairn.graph.builder import build_graph

    if not snapshot.is_dir():
        raise ValueError(f"snapshot directory missing: {snapshot}")

    workspace = workroot / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    repo_copy = workspace / snapshot.name
    if repo_copy.exists():
        shutil.rmtree(repo_copy)
    shutil.copytree(snapshot, repo_copy)
    (repo_copy / ".git").mkdir(exist_ok=True)  # scanner marker, on the COPY only

    db_path = workroot / "graph.db"
    try:
        summary = build_graph(workspace=str(workspace), db_path=str(db_path), verbose=False)
    except Exception as exc:  # noqa: BLE001 - any build failure is infra, not stale
        raise ValueError(f"graph build failed: {exc}") from exc
    if summary.get("repos", 0) < 1:
        raise ValueError(
            f"graph build recognized no repos under {workspace} "
            f"(scanner marker missing or empty workspace)"
        )
    if summary.get("parse_errors", 0):
        raise ValueError(
            f"graph build degraded: {summary['parse_errors']} parse error(s) -- "
            "verifying against a partial graph could mint false stale entries"
        )
    return summary, db_path


def symbol_inventory(db_path: Path) -> List[Dict[str, str]]:
    """The fresh graph's full symbol surface, as matcher-shaped dicts.

    Every ``symbols`` row joined to its file path -- the same rows
    ``find_definition``/``get_callers`` read. This is the L1 verification
    surface and, absent an OKF bundle, the L5 surface too (T011 authored L5
    expectations as code-symbol ids against a graph build).
    """
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT s.name, f.path AS file_path "
            "FROM symbols s JOIN files f ON s.file_id = f.id"
        ).fetchall()
    finally:
        conn.close()
    return [{"name": name, "file_path": file_path} for name, file_path in rows]


def verify_ground_truth(
    dataset: Path | str | None = None,
    snapshot: Path | str | None = None,
    bundle: Path | str | None = None,
    workroot: Path | str | None = None,
) -> VerifyReport:
    """Re-verify every expectation of a D-004 pair against a fresh build.

    Args:
        dataset: ground-truth dir holding queries.jsonl + expectations.tsv
            (default: the committed T011 pair). Scratch copies for TC-022
            tampering go here.
        snapshot: the vendored source tree to build from (default: the
            committed t2 yarl snapshot).
        bundle: OKF knowledge bundle root for the L5 surface. None (default)
            auto-discovers ``<t2>/.knowledge``; an explicitly passed path MUST
            exist (else infrastructure exit 2 -- eval's L5 retrieval would
            silently 0.0/0.0 there, which is never a stale verdict).
        workroot: scratch dir for the workspace copy + graph DB; None creates
            and cleans up a temp dir. Injectable so tests can inspect artifacts.

    Returns:
        A :class:`VerifyReport`; ``report.exit_code()`` is the CLI's code.
    """
    dataset = Path(dataset) if dataset is not None else DEFAULT_DATASET
    snapshot = Path(snapshot) if snapshot is not None else DEFAULT_SNAPSHOT
    report = VerifyReport(dataset=str(dataset), snapshot=str(snapshot))

    # ---- infrastructure preconditions (exit 2 class -- nothing verified) ----
    if not dataset.is_dir():
        report.errors.append(f"dataset directory missing: {dataset}")
        return report
    try:
        queries = load_ground_truth(dataset)
    except ValueError as exc:
        # Malformed pair (missing files, bad schema, dangling query_id...):
        # the loader already names the exact row/field.
        report.errors.append(f"dataset malformed: {exc}")
        return report

    bundle_root: Optional[Path] = None
    if bundle is not None:
        bundle_root = Path(bundle)
        if not bundle_root.is_dir():
            report.errors.append(f"bundle root missing: {bundle_root} (--bundle was requested)")
            return report
        report.bundle, report.bundle_status = str(bundle_root), "explicit"
    else:
        candidate = snapshot.parent / BUNDLE_DIRNAME
        if candidate.is_dir():
            bundle_root = candidate
            report.bundle, report.bundle_status = str(candidate), "auto"

    owns_workroot = workroot is None
    if workroot is None:
        workroot = Path(tempfile.mkdtemp(prefix="cairn-verify-gt-"))
    workroot = Path(workroot)
    try:
        try:
            summary, db_path = build_fresh_graph(snapshot, workroot)
        except ValueError as exc:
            report.errors.append(str(exc))
            return report
        report.build = {
            key: summary.get(key)
            for key in ("repos", "files", "symbols", "edges", "parse_errors")
        }
        inventory = symbol_inventory(db_path)
        if not inventory:
            report.errors.append("fresh build produced an empty symbol inventory")
            return report

        # ---- verification: every expectation through the T010 matcher ----
        summary_out: Dict[str, Dict[str, Dict[str, int]]] = {"L1": {}, "L5": {}}
        totals = {
            "queries": len(queries),
            "expectations": 0,
            "verified": 0,
            "stale": 0,
            "stale_grade2": 0,
            "stale_grade1": 0,
        }
        stale: List[StaleEntry] = []

        for graded in queries:
            pool = inventory
            if graded.level == "L5" and bundle_root is not None:
                # eval's own L5 retrieval joins the pool (concept ids, no
                # file path -> matcher substring tier by design).
                l5 = eval_mod._retrieve_l5(str(bundle_root), graded.text, BUNDLE_RETRIEVE_LIMIT)
                pool = inventory + l5
            kind_bucket = summary_out[graded.level].setdefault(
                graded.kind, {"queries": 0, "expectations": 0, "verified": 0, "stale": 0}
            )
            kind_bucket["queries"] += 1
            for exp in graded.expectations:
                totals["expectations"] += 1
                kind_bucket["expectations"] += 1
                # Presence semantics: the whole pool is the window, so the
                # matcher's tier-1 identity check decides and the substring
                # tier keeps parity (a k=10 text-retrieval window is scoring,
                # not staleness -- see module docstring).
                rank = match_rank(exp.symbol_id, pool, k=len(pool))
                if rank > 0:
                    totals["verified"] += 1
                    kind_bucket["verified"] += 1
                else:
                    entry = StaleEntry(
                        query_id=graded.query_id,
                        query_text=graded.text,
                        level=graded.level,
                        kind=graded.kind,
                        symbol_id=exp.symbol_id,
                        grade=exp.grade,
                    )
                    stale.append(entry)
                    totals["stale"] += 1
                    kind_bucket["stale"] += 1
                    totals["stale_grade2" if exp.grade == 2 else "stale_grade1"] += 1

        report.summary = {
            level: {kind: dict(b) for kind, b in sorted(buckets.items())}
            for level, buckets in summary_out.items()
        }
        report.totals = totals
        # Grade-2 primary targets first in the failure listing (D-004: the
        # primary target outranks must-return context), stable within grade.
        report.stale = sorted(stale, key=lambda s: (s.grade != 2, s.query_id, s.symbol_id))
    finally:
        if owns_workroot:
            shutil.rmtree(workroot, ignore_errors=True)
    return report


def _print_human(report: VerifyReport) -> None:
    """Human summary: build facts, per-kind/level table, then stale entries."""
    if report.errors:
        print(
            "FAIL: infrastructure error -- nothing was verified against a fresh build:",
            file=sys.stderr,
        )
        for err in report.errors:
            print(f"  {err}", file=sys.stderr)
        return

    b = report.build or {}
    print(f"dataset {report.dataset}")
    print(
        f"snapshot {report.snapshot} -> fresh build: repos={b.get('repos')} "
        f"symbols={b.get('symbols')} edges={b.get('edges')} parse_errors={b.get('parse_errors')}"
    )
    if report.bundle:
        print(f"bundle {report.bundle} ({report.bundle_status}) joins the L5 verification pool")
    else:
        print("bundle none -- L5 expectations verify against the graph surface (T011 authoring)")
    for level in ("L1", "L5"):
        for kind, bucket in report.summary.get(level, {}).items():
            verdict = "OK  " if bucket["stale"] == 0 else "FAIL"
            print(
                f"  {verdict} {level} {kind:<12} queries={bucket['queries']:<3} "
                f"expectations={bucket['expectations']:<3} verified={bucket['verified']:<3} "
                f"stale={bucket['stale']}"
            )
    t = report.totals
    if not report.stale:
        print(
            f"OK: {t['verified']}/{t['expectations']} expectations across {t['queries']} queries "
            f"verified on a fresh build (FR-003/AC5, TC-021)."
        )
        return
    print(
        f"FAIL: {t['stale']}/{t['expectations']} expectation(s) stale "
        f"(grade-2: {t['stale_grade2']}, grade-1: {t['stale_grade1']}) "
        f"(FR-003/AC5, TC-022):",
        file=sys.stderr,
    )
    for s in report.stale:
        print(
            f"  [{s.level} {s.kind} grade {s.grade}] query {s.query_id}: "
            f"\"{s.query_text}\" -> missing symbol {s.symbol_id}",
            file=sys.stderr,
        )
    # D-010: stale sets ship as DS-v2; this validator never edits the pair.
    print(
        "  (D-010: nothing was rewritten -- ship the corrected set as a new "
        "dataset version, e.g. DS-v2)",
        file=sys.stderr,
    )


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET,
        help=f"ground-truth dir holding queries.jsonl + expectations.tsv "
        f"(default: {DEFAULT_DATASET})",
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=DEFAULT_SNAPSHOT,
        help=f"vendored source tree to build a fresh graph from "
        f"(default: {DEFAULT_SNAPSHOT})",
    )
    parser.add_argument(
        "--bundle",
        type=Path,
        default=None,
        help="OKF knowledge bundle root for the L5 surface; must exist when "
        "given (default: auto-discover <t2>/.knowledge; absent -> L5 verifies "
        "against the graph surface)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print a machine-readable result (build facts + per-kind summary + stale entries)",
    )
    parser.add_argument(
        "--workroot",
        type=Path,
        default=None,
        help="scratch dir for the snapshot copy + graph DB (default: a cleaned-up temp dir)",
    )
    args = parser.parse_args(argv)

    report = verify_ground_truth(
        dataset=args.dataset, snapshot=args.snapshot, bundle=args.bundle, workroot=args.workroot
    )
    if args.json:
        print(json.dumps(report.to_json(), sort_keys=True))
    else:
        _print_human(report)
    return report.exit_code()


if __name__ == "__main__":
    sys.exit(main())
