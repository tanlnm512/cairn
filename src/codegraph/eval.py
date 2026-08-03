"""Retrieval evaluation harness for codegraph.

Evaluates recall@10 and MRR (Mean Reciprocal Rank) for code (L1) and knowledge (L5)
retrieval pipelines against ground truth query datasets.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)


def _resolve_default_queries_path() -> Path:
    """Locate the bundled eval queries fixture.

    Looked for in two layouts:

    1. Repo-root layout (development checkout, editable install)::

         <repo>/tests/eval/queries.yaml   with this file at <repo>/src/codegraph/eval.py

    2. In-package layout (sdist/wheel install)::

         <pkg>/tests/eval/queries.yaml   with this file at <pkg>/eval.py

    Returns the first existing path, else a best-guess path from layout (1) so
    that a helpful error message can reference it. Never raises.
    """
    here = Path(__file__).resolve()
    # Layout 1: src/codegraph/eval.py -> up three = repo root.
    repo_root_candidate = here.parents[2] / "tests" / "eval" / "queries.yaml"
    if repo_root_candidate.exists():
        return repo_root_candidate
    # Layout 2: <pkg>/eval.py -> up one = package root.
    pkg_candidate = here.parent / "tests" / "eval" / "queries.yaml"
    if pkg_candidate.exists():
        return pkg_candidate
    # Neither exists — return the repo-root guess (used in the helpful message
    # emitted when zero queries load).
    return repo_root_candidate


DEFAULT_QUERIES_PATH = _resolve_default_queries_path()


def load_eval_queries(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    p = path or DEFAULT_QUERIES_PATH
    if not p.exists():
        return []
    with open(p, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or []


def evaluate_l1_query(conn: sqlite3.Connection, query: str, expect: List[str], k: int = 10) -> Tuple[float, float]:
    """Evaluate L1 query using semantic_search / search_symbols.

    Returns (recall_at_k, reciprocal_rank).
    """
    from codegraph.graph import queries as qmod

    # Try hybrid/semantic retrieval first; fall back to lexical if embeddings are empty
    try:
        results = qmod.semantic_search(conn, query, limit=k)
        retrieved_names = [r.get("name", "") for r in results]
    except Exception:
        results = qmod.search_symbols(conn, query, limit=k)
        retrieved_names = [r.get("name", "") for r in results]

    if not retrieved_names:
        # Fallback to search_symbols if semantic search returned no candidates
        results = qmod.search_symbols(conn, query, limit=k)
        retrieved_names = [r.get("name", "") for r in results]

    rank = 0
    for idx, name in enumerate(retrieved_names, start=1):
        if any(exp.lower() in name.lower() for exp in expect):
            rank = idx
            break

    if rank > 0 and rank <= k:
        return 1.0, 1.0 / rank
    return 0.0, 0.0


def evaluate_l5_query(conn: sqlite3.Connection, bundle_root: Optional[str], query: str, expect: List[str], k: int = 10) -> Tuple[float, float]:
    """Evaluate L5 query using knowledge search.

    Returns (recall_at_k, reciprocal_rank).
    """
    from codegraph.okf.bundle import OKFBundle

    if not bundle_root or not Path(bundle_root).exists():
        return 0.0, 0.0

    bundle = OKFBundle(bundle_root)
    concepts = bundle.search(query, limit=k)
    retrieved_ids = [c.concept_id for c in concepts]

    rank = 0
    for idx, cid in enumerate(retrieved_ids, start=1):
        if any(exp.lower() in cid.lower() for exp in expect):
            rank = idx
            break

    if rank > 0 and rank <= k:
        return 1.0, 1.0 / rank
    return 0.0, 0.0


def run_evaluation(
    conn: sqlite3.Connection,
    bundle_root: Optional[str] = None,
    queries_path: Optional[Path] = None,
    corpus_filter: str = "all",
    k: int = 10,
) -> Dict[str, Any]:
    """Run full evaluation harness across specified corpus ("L1", "L5", or "all")."""
    queries = load_eval_queries(queries_path)

    if not queries:
        resolved = queries_path or DEFAULT_QUERIES_PATH
        logger.warning(
            "no eval queries loaded (looked for %s); "
            "pass --queries <path> or run from a checkout with tests/eval/queries.yaml",
            resolved,
        )

    stats = {
        "L1": {"count": 0, "recall": 0.0, "mrr": 0.0},
        "L5": {"count": 0, "recall": 0.0, "mrr": 0.0},
    }

    for item in queries:
        c_type = item.get("corpus", "L1")
        if corpus_filter != "all" and c_type != corpus_filter:
            continue

        q_text = item["query"]
        expect = item.get("expect", [])

        if c_type == "L1":
            rec, rr = evaluate_l1_query(conn, q_text, expect, k=k)
        else:
            rec, rr = evaluate_l5_query(conn, bundle_root, q_text, expect, k=k)

        stats[c_type]["count"] += 1
        stats[c_type]["recall"] += rec
        stats[c_type]["mrr"] += rr

    report = {}
    for c_key in ["L1", "L5"]:
        cnt = stats[c_key]["count"]
        if cnt > 0:
            report[c_key] = {
                "count": cnt,
                "recall_at_10": round(stats[c_key]["recall"] / cnt, 4),
                "mrr": round(stats[c_key]["mrr"] / cnt, 4),
            }
        else:
            report[c_key] = {"count": 0, "recall_at_10": 0.0, "mrr": 0.0}

    return report
# extra comment
