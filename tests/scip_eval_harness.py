"""Zero-false-exact A/B measurement: index-on vs index-off builds of twin
workspaces scored against an eval.py-format ground truth (FR-014).

The report shape is the TC-021 contract: ``false_exact`` must be 0 — a
nonzero value is a harness failure, not a result.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Tuple

from cairn.eval import evaluate_l1_query, load_ground_truth, parse_symbol_id
from cairn.graph.builder import build_graph

# Tree-identity excludes the twin-only surfaces: the scip config and the
# index itself. Everything else must be byte-identical for the A/B to be
# a same-tree comparison.
_IDENTITY_EXCLUDES = {"cairn.json"}
_IDENTITY_EXCLUDE_SUFFIX = ".scip"

_CALLS_SHARE_SQL = (
    "SELECT SUM(e.resolution = 'exact') AS exact_n, COUNT(*) AS n "
    "FROM edges e WHERE e.kind = 'calls'"
)
_CALLS_SHARE_BY_LANGUAGE_SQL = (
    "SELECT f.language AS language, SUM(e.resolution = 'exact') AS exact_n, "
    "COUNT(*) AS n FROM edges e JOIN symbols s ON e.source_id = s.id "
    "JOIN files f ON s.file_id = f.id "
    "WHERE e.kind = 'calls' GROUP BY f.language"
)

REPORT_SCHEMA = "scip-zero-false-exact/1"


def _tree_digest(workspace: Path) -> str:
    """Digest of the workspace's source files, twin-only surfaces excluded."""
    h = hashlib.sha256()
    for p in sorted(workspace.rglob("*")):
        rel = p.relative_to(workspace)
        if (
            p.is_dir()
            or ".git" in rel.parts
            or p.name in _IDENTITY_EXCLUDES
            or p.suffix == _IDENTITY_EXCLUDE_SUFFIX
        ):
            continue
        h.update(str(p.relative_to(workspace)).encode("utf-8"))
        h.update(p.read_bytes())
    return h.hexdigest()


def _materialize(src: Path, work_dir: Path, name: str) -> Path:
    """Copy a fixture workspace into work_dir with a bare .git marker."""
    ws = work_dir / name
    shutil.copytree(src, ws, ignore=shutil.ignore_patterns(".git"))
    (ws / ".git").mkdir()
    return ws


def _build_leg(src: Path, work_dir: Path, name: str) -> Tuple[str, Dict[str, Any]]:
    """Build one twin into a tmp DB; returns (db path, build summary)."""
    ws = _materialize(src, work_dir, name)
    db = str(work_dir / f"{name}.db")
    summary = build_graph(workspace=str(ws), db_path=db)
    return db, summary


def _exact_share(conn: sqlite3.Connection) -> float:
    """Exact share over the calls pool (all resolutions), TC-020's pool."""
    row = conn.execute(_CALLS_SHARE_SQL).fetchone()
    total = row["n"] or 0
    return round((row["exact_n"] or 0) / total, 4) if total else 0.0


def _exact_share_by_language(conn: sqlite3.Connection) -> Dict[str, float]:
    rows = conn.execute(_CALLS_SHARE_BY_LANGUAGE_SQL).fetchall()
    return {
        r["language"]: round((r["exact_n"] or 0) / r["n"], 4) for r in rows if r["n"]
    }


def _retrieval_leg(
    conn: sqlite3.Connection, graded: List[Any], k: int
) -> List[Dict[str, Any]]:
    """One build's L1 ground-truth results via evaluate_l1_query."""
    out: List[Dict[str, Any]] = []
    for g in graded:
        if g.level != "L1":
            continue
        expect = [parse_symbol_id(e.symbol_id)[1] for e in g.expectations]
        recall, rr = evaluate_l1_query(conn, g.text, expect, k=k)
        out.append(
            {
                "query_id": g.query_id,
                "matched": recall > 0,
                "recall": recall,
                "reciprocal_rank": rr,
            }
        )
    return out


def run_ab_eval(
    workspace_on: Path,
    workspace_off: Path,
    ground_truth_dir: Path,
    work_dir: Path,
    out_path: Path = None,
    k: int = 10,
) -> Tuple[Dict[str, Any], Dict[str, int]]:
    """Build both twins into tmp DBs, run the A/B ground-truth evaluation, and
    return (report, import_record); writes the JSON report when ``out_path``.

    Disagreements/upgrades are read from the index-on build's import record
    (summary['scip']), never re-derived. Raises when the twins are not the
    same tree, when the index-on build carried no scip import record, or when
    false_exact is nonzero.
    """
    workspace_on, workspace_off = Path(workspace_on), Path(workspace_off)
    ground_truth_dir, work_dir = Path(ground_truth_dir), Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    digest_on, digest_off = _tree_digest(workspace_on), _tree_digest(workspace_off)
    if digest_on != digest_off:
        raise ValueError(
            f"twin workspaces are not the same tree: {workspace_on} vs {workspace_off}"
        )

    db_on, summary_on = _build_leg(workspace_on, work_dir, "index-on")
    db_off, summary_off = _build_leg(workspace_off, work_dir, "index-off")

    import_record = summary_on.get("scip")
    if not import_record:
        raise ValueError(
            "index-on build carried no scip import record (overlay did not run)"
        )
    if "scip" in summary_off:
        raise ValueError("index-off build unexpectedly applied a scip overlay")

    graded = load_ground_truth(ground_truth_dir)
    conn_on = sqlite3.connect(db_on)
    conn_off = sqlite3.connect(db_off)
    conn_on.row_factory = sqlite3.Row
    conn_off.row_factory = sqlite3.Row
    try:
        results_on = _retrieval_leg(conn_on, graded, k)
        results_off = _retrieval_leg(conn_off, graded, k)
        share_on = _exact_share(conn_on)
        share_off = _exact_share(conn_off)
        by_lang_on = _exact_share_by_language(conn_on)
        by_lang_off = _exact_share_by_language(conn_off)
    finally:
        conn_on.close()
        conn_off.close()

    matched_on = {r["query_id"] for r in results_on if r["matched"]}
    matched_off = {r["query_id"] for r in results_off if r["matched"]}
    false_exact = sorted(matched_off - matched_on)

    uplift_by_language = {
        lang: {
            "off": by_lang_off.get(lang, 0.0),
            "on": by_lang_on.get(lang, 0.0),
            "uplift": round(by_lang_on.get(lang, 0.0) - by_lang_off.get(lang, 0.0), 4),
        }
        for lang in sorted(set(by_lang_on) | set(by_lang_off))
    }

    n_queries = len(results_on)
    report: Dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "corpus": {
            "workspace_on": str(workspace_on),
            "workspace_off": str(workspace_off),
            "ground_truth": str(ground_truth_dir),
            "tree_digest": digest_on,
            "tree_identical": True,
        },
        "exact_share": {"off": share_off, "on": share_on},
        "exact_share_by_language": uplift_by_language,
        "disagreements": import_record["disagreements"],
        "upgrades": import_record["upgrades"],
        "retrieval": {
            "k": k,
            "n_queries": n_queries,
            "matched_off": len(matched_off),
            "matched_on": len(matched_on),
            "precision_off": round(len(matched_off) / n_queries, 4) if n_queries else 0.0,
            "precision_on": round(len(matched_on) / n_queries, 4) if n_queries else 0.0,
        },
        "false_exact": len(false_exact),
    }
    if out_path is not None:
        Path(out_path).write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if false_exact:
        raise ValueError(
            f"false-exact conversions detected (harness failure, not a result): "
            f"{false_exact}"
        )
    return report, import_record
