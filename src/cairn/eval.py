"""Retrieval evaluation harness for cairn.

Evaluates recall@10 and MRR (Mean Reciprocal Rank) for code (L1) and knowledge (L5)
retrieval pipelines against ground truth query datasets.

Two query sources (D-008): the legacy yaml fixture via ``load_eval_queries``
(bundled test data, kept as-is) and the maintained graded pair via
``load_ground_truth`` (``queries.jsonl`` + ``expectations.tsv``, D-004 schema)
with identity-first matching and grade-aware scoring.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)


def _resolve_default_queries_path() -> Path:
    """Locate the bundled eval queries fixture.

    Looked for in two layouts:

    1. Repo-root layout (development checkout, editable install)::

         <repo>/tests/eval/queries.yaml   with this file at <repo>/src/cairn/eval.py

    2. In-package layout (sdist/wheel install)::

         <pkg>/tests/eval/queries.yaml   with this file at <pkg>/eval.py

    Returns the first existing path, else a best-guess path from layout (1) so
    that a helpful error message can reference it. Never raises.
    """
    here = Path(__file__).resolve()
    # Layout 1: src/cairn/eval.py -> up three = repo root.
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


# --------------------------------------------------------------------------
# Graded ground-truth loader (D-004 schema, D-008 second-loader decision)
#
# The yaml fixture above (tests/eval/queries.yaml) is *test fixture data* and
# stays as-is. The maintained ground truth lives as a file pair:
#
#   queries.jsonl     one JSON object per line:
#                     {query_id, level: "L1"|"L5", kind, text, rationale}
#   expectations.tsv  TSV with header (query_id, symbol_id, grade) where
#                     symbol_id is "file#symbol" and grade is 1 (must-return)
#                     or 2 (primary target).
# --------------------------------------------------------------------------

VALID_LEVELS = frozenset({"L1", "L5"})
VALID_GRADES = frozenset({1, 2})


@dataclass(frozen=True)
class Expectation:
    """One qrel row: the symbol expected for a query, and how primary it is.

    ``grade`` 1 = must-return context, 2 = the primary target (D-004).
    """

    symbol_id: str
    grade: int


@dataclass(frozen=True)
class GradedQuery:
    """A query from queries.jsonl joined with its expectations rows."""

    query_id: str
    level: str
    kind: str
    text: str
    rationale: str
    expectations: List[Expectation]


def parse_symbol_id(symbol_id: str) -> Tuple[str, str]:
    """Split a D-004 ``file#symbol`` id into its (file, symbol) components.

    Splits on the *last* ``#`` so that file components containing ``#`` still
    parse (symbol names cannot contain ``#`` in any indexed language).
    """
    file_part, sep, symbol_part = symbol_id.rpartition("#")
    if not sep or not file_part.strip() or not symbol_part.strip():
        raise ValueError(
            f"malformed symbol_id {symbol_id!r}: expected 'file#symbol' "
            "(e.g. 'src/yarl/_url.py#URL')"
        )
    return file_part, symbol_part


def load_ground_truth(ground_truth_dir: Path) -> List[GradedQuery]:
    """Load a D-004 ground-truth pair from ``ground_truth_dir``.

    Reads ``queries.jsonl`` + ``expectations.tsv`` and joins them on
    ``query_id``. Validation errors raise ``ValueError``:

    * a tsv row whose ``query_id`` is not present in queries.jsonl;
    * a query with zero expectation rows;
    * a grade outside ``{1, 2}``;
    * plus structural errors (missing files, malformed JSON lines, missing
      required fields, unknown level, duplicate query_id, malformed
      symbol_id) so a bad dataset fails loudly at load time instead of
      silently scoring zero.
    """
    gt_dir = Path(ground_truth_dir)
    queries_path = gt_dir / "queries.jsonl"
    expectations_path = gt_dir / "expectations.tsv"
    for missing in (queries_path, expectations_path):
        if not missing.exists():
            raise ValueError(
                f"ground-truth file missing: {missing} "
                f"(expected queries.jsonl + expectations.tsv in {gt_dir})"
            )

    queries: Dict[str, Dict[str, str]] = {}
    with open(queries_path, "r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"queries.jsonl line {line_no}: not valid JSON: {exc}") from exc
            if not isinstance(item, dict):
                raise ValueError(f"queries.jsonl line {line_no}: expected a JSON object")
            query_id = item.get("query_id")
            if not query_id or not str(query_id).strip():
                raise ValueError(f"queries.jsonl line {line_no}: missing required 'query_id'")
            query_id = str(query_id)
            if query_id in queries:
                raise ValueError(f"queries.jsonl line {line_no}: duplicate query_id {query_id!r}")
            level = item.get("level")
            if level not in VALID_LEVELS:
                raise ValueError(
                    f"queries.jsonl line {line_no}: query {query_id!r} has level "
                    f"{level!r}; expected one of {sorted(VALID_LEVELS)}"
                )
            text = item.get("text")
            if not text or not str(text).strip():
                raise ValueError(
                    f"queries.jsonl line {line_no}: query {query_id!r} has empty 'text'"
                )
            queries[query_id] = {
                "query_id": query_id,
                "level": level,
                "kind": str(item.get("kind", "")),
                "text": str(text),
                "rationale": str(item.get("rationale", "")),
            }

    if not queries:
        raise ValueError(f"queries.jsonl is empty: {queries_path}")

    expectations_by_query: Dict[str, List[Expectation]] = {qid: [] for qid in queries}
    with open(expectations_path, "r", encoding="utf-8", newline="") as fh:
        header = fh.readline().rstrip("\n\r")
        if header.lower() != "query_id\tsymbol_id\tgrade":
            raise ValueError(
                f"expectations.tsv: unexpected header {header!r}; expected "
                "'query_id\\tsymbol_id\\tgrade'"
            )
        for line_no, line in enumerate(fh, start=2):
            line = line.rstrip("\n\r")
            if not line.strip():
                continue
            fields = line.split("\t")
            if len(fields) != 3:
                raise ValueError(
                    f"expectations.tsv line {line_no}: expected 3 tab-separated "
                    f"fields (query_id, symbol_id, grade), got {len(fields)}"
                )
            query_id, symbol_id, grade_raw = (f.strip() for f in fields)
            if query_id not in queries:
                raise ValueError(
                    f"expectations.tsv line {line_no}: unknown query_id "
                    f"{query_id!r} (not present in queries.jsonl)"
                )
            try:
                grade = int(grade_raw)
            except ValueError as exc:
                raise ValueError(
                    f"expectations.tsv line {line_no}: grade {grade_raw!r} is not "
                    f"an integer (expected 1 or 2)"
                ) from exc
            if grade not in VALID_GRADES:
                raise ValueError(
                    f"expectations.tsv line {line_no}: grade {grade} outside "
                    f"{{1, 2}} for query {query_id!r}"
                )
            parse_symbol_id(symbol_id)  # validates the file#symbol shape
            expectations_by_query[query_id].append(Expectation(symbol_id=symbol_id, grade=grade))

    for query_id, exps in expectations_by_query.items():
        if not exps:
            raise ValueError(
                f"query {query_id!r} has zero expectation rows in expectations.tsv"
            )

    return [
        GradedQuery(
            query_id=queries[qid]["query_id"],
            level=queries[qid]["level"],
            kind=queries[qid]["kind"],
            text=queries[qid]["text"],
            rationale=queries[qid]["rationale"],
            expectations=exps,
        )
        for qid, exps in expectations_by_query.items()
    ]


def evaluate_l1_query(conn: sqlite3.Connection, query: str, expect: List[str], k: int = 10) -> Tuple[float, float]:
    """Evaluate L1 query using semantic_search / search_symbols.

    Returns (recall_at_k, reciprocal_rank).
    """
    from cairn.graph import queries as qmod

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
    from cairn.okf.bundle import OKFBundle

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


# --------------------------------------------------------------------------
# Graded matching + scoring (two-tier identity-first rule, D-008)
# --------------------------------------------------------------------------

def _result_field(result: Any, key: str) -> str:
    """Read a field from a retrieval result, dict or sqlite3.Row.

    ``semantic_search`` returns dicts; ``search_symbols`` returns
    ``sqlite3.Row`` objects (which raise ``IndexError`` on a missing key,
    not ``KeyError``). Returns "" for missing/None so matchers never trip.
    """
    try:
        value = result[key]
    except (KeyError, IndexError, TypeError):
        return ""
    return "" if value is None else str(value)


def match_rank(symbol_id: str, results: List[Any], k: int = 10) -> int:
    """Rank of the first result matching an expected ``file#symbol`` id.

    Two-tier rule (identity first, substring fallback):

    * **Tier 1 — strict identity**: a result matches when its file path
      *ends with* the expectation's file component AND its symbol name
      *equals* the symbol component.
    * **Tier 2 — substring fallback**: when no result in the top-k satisfies
      the identity rule, a result matches when the symbol component appears
      (case-insensitively) as a substring of the result's symbol name — the
      legacy rule from ``evaluate_l1_query`` (``exp.lower() in name.lower()``).

    The identity tier exists because the graded dataset pins expectations to
    exact ``file#symbol`` locations; the substring tier keeps parity with the
    yaml-fixture behavior when retrieval returns the right symbol from a
    different file, or a result carrying no file path at all (L5 concept ids
    can never satisfy tier 1 and always take the fallback — preserving the
    legacy L5 matching exactly).

    Returns the 1-based rank of the first match within the top-k, else 0.
    """
    file_part, symbol_part = parse_symbol_id(symbol_id)

    # Tier 1: strict identity (file suffix + exact symbol name).
    for idx in range(min(k, len(results))):
        name = _result_field(results[idx], "name")
        file_path = _result_field(results[idx], "file_path")
        if name == symbol_part and file_path and file_path.endswith(file_part):
            return idx + 1

    # Tier 2: legacy substring rule (eval.py:81) on the symbol name only.
    symbol_lower = symbol_part.lower()
    for idx in range(min(k, len(results))):
        if symbol_lower in _result_field(results[idx], "name").lower():
            return idx + 1

    return 0


def score_graded_query(
    results: List[Any], expectations: List[Expectation], k: int = 10
) -> Tuple[float, float]:
    """Score one graded query against a ranked result list.

    Returns ``(recall_at_k, reciprocal_rank)`` where:

    * **recall@k** = fraction of grade >= 1 expectations matched in the
      top-k (a match via either matcher tier counts).
    * **MRR contribution** = reciprocal of the rank of the FIRST grade-2
      match when the query has any grade-2 expectation — the primary
      target outranks must-return context, per D-004 — else the first
      grade-1 match. A query whose grade-2 expectations exist but none
      matched scores 0.0: the primary target was missed, and falling back
      to a grade-1 rank would inflate the score.

    ``results`` are the ranked retrieval hits (best first); ranks beyond
    position k are ignored.
    """
    if not expectations:
        return 0.0, 0.0

    ranks = [(exp.grade, match_rank(exp.symbol_id, results, k)) for exp in expectations]
    matched = [rank for _grade, rank in ranks if rank > 0]
    recall = len(matched) / len(expectations)

    has_primary = any(grade == 2 for grade, _rank in ranks)
    pool = [rank for grade, rank in ranks if (grade == 2) == has_primary and rank > 0]
    rank = min(pool) if pool else 0
    rr = 1.0 / rank if rank else 0.0
    return recall, rr


def _retrieve_l1(conn: sqlite3.Connection, query: str, k: int) -> List[Any]:
    """L1 retrieval pipeline (semantic first, lexical fallback), full rows."""
    from cairn.graph import queries as qmod

    try:
        results = list(qmod.semantic_search(conn, query, limit=k))
        if results:
            return results
    except Exception:  # noqa: BLE001 - mirrors evaluate_l1_query's degrade
        pass
    return list(qmod.search_symbols(conn, query, limit=k))


def _retrieve_l5(bundle_root: Optional[str], query: str, k: int) -> List[Any]:
    """L5 retrieval pipeline, normalized to ``{"name", "file_path"}`` dicts.

    OKF concepts carry no file path, so graded matching against them always
    takes the substring tier (see ``match_rank``).
    """
    from cairn.okf.bundle import OKFBundle

    if not bundle_root or not Path(bundle_root).exists():
        return []
    bundle = OKFBundle(bundle_root)
    return [{"name": c.concept_id, "file_path": ""} for c in bundle.search(query, limit=k)]


def evaluate_graded_query(
    conn: sqlite3.Connection,
    bundle_root: Optional[str],
    graded: GradedQuery,
    k: int = 10,
) -> Tuple[float, float]:
    """Evaluate one graded query through its level's retrieval pipeline."""
    if graded.level == "L1":
        results = _retrieve_l1(conn, graded.text, k)
    else:
        results = _retrieve_l5(bundle_root, graded.text, k)
    return score_graded_query(results, graded.expectations, k)


def _run_graded_evaluation(
    conn: sqlite3.Connection,
    bundle_root: Optional[str],
    graded_dir: Path,
    corpus_filter: str = "all",
    k: int = 10,
) -> Dict[str, Any]:
    """Graded counterpart of the yaml loop in ``run_evaluation``.

    Report shape mirrors the yaml one — ``{"L1": {...}, "L5": {...}}`` with
    ``count``/``recall_at_10``/``mrr`` — plus additive ``n_queries`` and
    ``n_expectations`` keys for dataset-size visibility.
    """
    queries = load_ground_truth(graded_dir)

    stats = {
        "L1": {"count": 0, "recall": 0.0, "mrr": 0.0, "n_expectations": 0},
        "L5": {"count": 0, "recall": 0.0, "mrr": 0.0, "n_expectations": 0},
    }

    for graded in queries:
        if corpus_filter != "all" and graded.level != corpus_filter:
            continue
        rec, rr = evaluate_graded_query(conn, bundle_root, graded, k=k)
        bucket = stats[graded.level]
        bucket["count"] += 1
        bucket["recall"] += rec
        bucket["mrr"] += rr
        bucket["n_expectations"] += len(graded.expectations)

    report: Dict[str, Any] = {}
    for c_key in ["L1", "L5"]:
        cnt = stats[c_key]["count"]
        if cnt > 0:
            report[c_key] = {
                "count": cnt,
                "recall_at_10": round(stats[c_key]["recall"] / cnt, 4),
                "mrr": round(stats[c_key]["mrr"] / cnt, 4),
                "n_queries": cnt,
                "n_expectations": stats[c_key]["n_expectations"],
            }
        else:
            report[c_key] = {
                "count": 0,
                "recall_at_10": 0.0,
                "mrr": 0.0,
                "n_queries": 0,
                "n_expectations": 0,
            }
    return report


def run_evaluation(
    conn: sqlite3.Connection,
    bundle_root: Optional[str] = None,
    queries_path: Optional[Path] = None,
    corpus_filter: str = "all",
    k: int = 10,
) -> Dict[str, Any]:
    """Run full evaluation harness across specified corpus ("L1", "L5", or "all").

    ``queries_path`` selects the query source (D-008 — two loaders, one
    harness): a *directory* holding the graded D-004 pair
    (``queries.jsonl`` + ``expectations.tsv``) takes the graded loader and
    the identity-first matcher; anything else (a yaml file, or None for the
    bundled fixture) takes the legacy yaml path unchanged.
    """
    if queries_path is not None and Path(queries_path).is_dir():
        return _run_graded_evaluation(conn, bundle_root, Path(queries_path), corpus_filter, k)

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
