"""Retrieval evaluation harness for cairn.

Evaluates recall@10 and MRR (Mean Reciprocal Rank) for code (L1) and knowledge (L5)
retrieval pipelines against ground truth query datasets.

Two query sources (D-008): the legacy yaml fixture via ``load_eval_queries``
(bundled test data, kept as-is) and the maintained graded pair via
``load_ground_truth`` (``queries.jsonl`` + ``expectations.tsv``, D-004 schema)
with identity-first matching and grade-aware scoring. ``split_queries``
(FR-006) turns that ground truth into the seeded tune/validate split used
for held-out lever selection; ``evaluate_on`` (FR-006, TC-019) is the guarded
evaluation seam that enforces it, and ``paired_bootstrap`` (D-006) is the
accept gate any lever must pass on the validate split.

The sweep harness (FR-005, D-007, T004) sits on top of that seam:
``run_sweep`` enumerates lever combinations (each an injected
``RetrievalParams``), evaluates every combo on the tune split only —
held-out enforcement is inherited from the seam, not re-implemented — and
emits the machine-readable multi-row results table in its own schema
(``cairn-quality-sweep/1``). ``evaluate_full_set`` is the post-selection
reporting path (the full set, no split) behind the integrity row and final
numbers. Neither opens a write path anywhere (TC-025): the ground-truth
files are only ever read, and serializing the table is the caller's job
(``format_sweep_json`` gives the canonical bytes).
"""
from __future__ import annotations

import json
import logging
import math
import random
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

import yaml

if TYPE_CHECKING:
    # Runtime import stays lazy (the cairn.graph.queries __getattr__ pattern
    # keeps the heavy graph stack out of module import); annotations only.
    from cairn.graph.semantic import RetrievalParams

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


# --------------------------------------------------------------------------
# Seeded tune/validate split (FR-006, D-006)
#
# Lever selection must run on a tune half and report a held-out validate
# half (adaptive-overfitting control for a fixed 58-query dataset). The
# split uses the repo's seeded ``random.Random(seed)`` pattern (see
# bench/corpus.py and bench/agent_suite.py) and is fixed by DEFAULT_SPLIT_SEED
# so every run, machine, and process sees the identical partition.
# --------------------------------------------------------------------------

#: Fixed split seed: the tune/validate partition of a given id set is
#: byte-identical across runs, machines, and processes. Deliberately distinct
#: from bench's ``DEFAULT_SEED`` (0xC0DE) so the split never moves when the
#: corpus generator's seed is tweaked.
DEFAULT_SPLIT_SEED = 0x5EED


def split_queries(
    queries: Iterable[Any],
    *,
    seed: int = DEFAULT_SPLIT_SEED,
    ratio: float = 0.5,
) -> Tuple[List[str], List[str]]:
    """Split query ids into the seeded ``(tune_ids, validate_ids)`` pair.

    ``queries`` is any iterable of ``GradedQuery`` objects — only the
    ``query_id`` field is read, so the function is level-agnostic: filtering
    to L1 (the 58-query measurement set) is the caller's choice — or of bare
    id strings. The input is never mutated.

    Contract (TC-018):

    * **Reproducible** — ids are deduplicated and *sorted before* the seeded
      ``random.Random(seed).shuffle``, so the split is a pure function of
      (id set, seed, ratio): never of input order, set/dict iteration order,
      or ``PYTHONHASHSEED``. Two calls with the same seed give identical
      halves, in-process or not; a different seed gives a different
      partition.
    * **Disjoint and complete** — the halves partition the input:
      ``set(tune) | set(validate)`` equals the input id set and the halves
      share no id.
    * **Odd counts** — ``ceil(n * ratio)`` ids go to tune; at the default
      ``ratio=0.5`` the tune half gets the extra query (3/2 for five ids).
      The real 58-L1 set splits 29/29.

    ``ratio`` outside ``[0, 1]`` raises ``ValueError`` (fail loudly rather
    than silently degenerating a half to empty). Returned halves keep
    shuffle order (tune first) so re-run diffs are empty (T006 checkpoint).
    """
    if not 0.0 <= ratio <= 1.0:
        raise ValueError(f"split ratio must be within [0.0, 1.0], got {ratio!r}")

    # Dedupe + sort BEFORE shuffling: the sort kills any dependence on input
    # order or hash randomization, making the shuffle deterministic given
    # (id set, seed).
    ids = sorted({item if isinstance(item, str) else item.query_id for item in queries})
    rng = random.Random(seed)
    rng.shuffle(ids)

    # round(..., 10) sheds float noise (0.3 * 10 == 3.0000000000000004);
    # ceil hands the odd element to tune.
    n_tune = math.ceil(round(len(ids) * ratio, 10))
    return ids[:n_tune], ids[n_tune:]


# --------------------------------------------------------------------------
# Held-out enforcement + paired-bootstrap accept guard (FR-006, TC-019, D-006)
#
# The sweep harness (T004) evaluates every lever combination through ONE
# entrypoint — ``evaluate_on`` — which is where held-out discipline lives.
# Selection runs take purpose="selection" and may only touch the tune half;
# the one legitimate way to read the validate half is purpose="validate",
# which structurally cannot return numbers without the paired-bootstrap
# verdict attached. ``paired_bootstrap`` (D-006: bootstrap/t, not Wilcoxon —
# 58 queries is the TREC 50-topic regime, Smucker et al. 2007) is that
# verdict.
# --------------------------------------------------------------------------

#: Fixed seed for ``paired_bootstrap``: accept/reject verdicts are
#: reproducible across runs, machines, and processes. Deliberately distinct
#: from ``DEFAULT_SPLIT_SEED`` so tweaking one never perturbs the other.
DEFAULT_BOOTSTRAP_SEED = 0xB0057

_PURPOSES = ("selection", "validate")
_METRICS = ("recall_at_10", "mrr")


class HeldOutError(RuntimeError):
    """A selection-stage evaluation tried to read held-out (validate) ids.

    Subclasses ``RuntimeError`` — deliberately NOT ``ValueError`` — so
    generic dataset-error handlers cannot downgrade the violation into a
    "clean" error path: ``eval_cmd``, for instance, catches ``ValueError``
    as "invalid eval dataset" and would mislabel a held-out breach. An
    uncaught ``HeldOutError`` propagates to a non-zero exit, which is
    exactly the TC-019 contract: fail loudly, no results table emitted.
    """


def evaluate_on(
    conn: sqlite3.Connection,
    queries: Iterable[Any],
    *,
    ids: Iterable[str],
    purpose: str = "selection",
    held_out_ids: Optional[Iterable[str]] = None,
    baseline_metrics: Optional[Mapping[str, float]] = None,
    metric: str = "recall_at_10",
    bundle_root: Optional[str] = None,
    k: int = 10,
    n_resamples: int = 10000,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    params: Optional["RetrievalParams"] = None,
    timer: Callable[[], float] = time.perf_counter,
) -> Dict[str, Any]:
    """Evaluate the queries named in ``ids`` under held-out discipline.

    This is the seam the sweep harness (T004) is contractually built on;
    ``run_evaluation`` (the reporting path) stays untouched. ``queries`` is
    the loaded ground truth (``load_ground_truth`` output — only
    ``query_id``/``level``/``text``/``expectations`` are read).

    WHY this seam cannot be bypassed silently:

    * The lower-level primitives (``evaluate_graded_query`` and the
      ``_*_retrieve`` helpers) carry no id knowledge — split discipline can
      only be enforced at an entrypoint that sees the requested ids, and
      this is the only evaluation entrypoint that does.
    * **purpose="selection"** structurally requires ``held_out_ids`` (the
      validate half from ``split_queries``) and intersects the requested
      ids against them *before any retrieval runs*: a violating call raises
      :class:`HeldOutError` naming the offending ids and the mode, so no
      query is scored and no results table can be emitted for that run.
      Omitting ``held_out_ids`` raises ``ValueError`` — selection without a
      declared held-out set is a harness bug, not a silent pass.
    * **purpose="validate"** is the one legitimate held-out read, and it
      cannot return numbers without the paired-bootstrap verdict attached:
      ``baseline_metrics`` (the incumbent's per-query values for the same
      ids) is mandatory, the candidate/baseline arrays are paired by query
      id, and ``paired_bootstrap`` runs before the report is returned.
      "Read the validate split and decide by eyeball" is not a
      constructible call.

    Returns a report with ``purpose``, ``n_queries``, ``recall_at_10``,
    ``mrr``, ``per_query`` (``{qid: {"recall_at_10", "mrr"}}`` — the
    candidate per-query arrays future validate runs pair against), and
    ``durations_ms`` (``{qid: wall-clock milliseconds}`` — per-query
    retrieval wall time, the input the sweep harness percentiles into its
    ``p95_ms`` column; raw floats, never rounded here). In validate mode the
    report additionally carries ``metric``, ``baseline_mean``, and
    ``bootstrap`` (the full :func:`paired_bootstrap` verdict).

    ``params`` (D-008, FR-005) threads through to every
    ``evaluate_graded_query`` retrieval call — the injection channel the
    sweep harness uses; ``None`` (and every ``None`` field) preserves
    today's retrieval behavior exactly. ``timer`` is the wall-clock source
    for ``durations_ms`` (injectable so deterministic tests can pin the
    timing column; ``time.perf_counter`` in production).

    ``ValueError`` is raised for malformed calls (unknown purpose/metric,
    empty or unknown ids, selection without ``held_out_ids``, validate
    without complete ``baseline_metrics``); :class:`HeldOutError` for the
    held-out violation itself.
    """
    if purpose not in _PURPOSES:
        raise ValueError(
            f"unknown purpose {purpose!r}; expected one of {list(_PURPOSES)}"
        )
    if metric not in _METRICS:
        raise ValueError(
            f"unknown metric {metric!r}; expected one of {list(_METRICS)}"
        )

    by_id: Dict[str, Any] = {}
    for q in queries:
        if isinstance(q, str):
            raise ValueError(
                "evaluate_on requires GradedQuery objects (load_ground_truth "
                "output); bare id strings carry no expectations to score"
            )
        by_id[q.query_id] = q
    id_list = sorted(set(ids))
    if not id_list:
        raise ValueError("evaluate_on received an empty id set")
    unknown = [qid for qid in id_list if qid not in by_id]
    if unknown:
        raise ValueError(
            f"evaluate_on received {len(unknown)} unknown query id(s) "
            f"{unknown} not present in the loaded ground truth"
        )

    # --- The guard: enforced before any retrieval or scoring happens. ----
    if purpose == "selection":
        if held_out_ids is None:
            raise ValueError(
                "selection-stage evaluation requires held_out_ids (the "
                "validate half from split_queries); without it, held-out "
                "discipline cannot be enforced (FR-006)"
            )
        violation = sorted(set(id_list) & set(held_out_ids))
        if violation:
            raise HeldOutError(
                f"held-out violation: selection-stage evaluation "
                f"(purpose='selection') attempted to read {len(violation)} "
                f"validation-split query id(s) {violation}; validate ids are "
                f"reserved for the paired-bootstrap accept guard — use "
                f"purpose='validate' with baseline_metrics for a held-out "
                f"measurement, or restrict ids to the tune split (FR-006, TC-019)"
            )

    # --- Evaluate (only reachable with a legal id set for the mode). -----
    per_query: Dict[str, Dict[str, float]] = {}
    durations_ms: Dict[str, float] = {}
    for qid in id_list:
        graded = by_id[qid]
        started = timer()
        rec, rr = evaluate_graded_query(conn, bundle_root, graded, k=k, params=params)
        durations_ms[qid] = (timer() - started) * 1000.0
        per_query[qid] = {"recall_at_10": rec, "mrr": rr}

    report: Dict[str, Any] = {
        "purpose": purpose,
        "n_queries": len(id_list),
        "recall_at_10": round(sum(p["recall_at_10"] for p in per_query.values()) / len(id_list), 4),
        "mrr": round(sum(p["mrr"] for p in per_query.values()) / len(id_list), 4),
        "per_query": per_query,
        "durations_ms": durations_ms,
    }
    if purpose == "selection":
        return report

    # --- Validate mode: the bootstrap guard runs before results return. --
    if baseline_metrics is None:
        raise ValueError(
            "purpose='validate' requires baseline_metrics (the incumbent's "
            "per-query values keyed by query id): the paired-bootstrap "
            "accept guard must run before held-out results are returned "
            "(FR-006, D-006)"
        )
    missing = [qid for qid in id_list if qid not in baseline_metrics]
    if missing:
        raise ValueError(
            f"baseline_metrics is missing {len(missing)} evaluated query "
            f"id(s) {missing}: the paired bootstrap needs candidate and "
            f"baseline arrays paired query-for-query"
        )
    bad = sorted(
        qid
        for qid in id_list
        if not isinstance(baseline_metrics[qid], (int, float))
        or isinstance(baseline_metrics[qid], bool)
    )
    if bad:
        raise ValueError(
            f"baseline_metrics values must be numbers; got non-numeric "
            f"values for {bad}"
        )

    candidate = [per_query[qid][metric] for qid in id_list]
    baseline = [float(baseline_metrics[qid]) for qid in id_list]
    report["metric"] = metric
    report["baseline_mean"] = round(sum(baseline) / len(baseline), 4)
    report["bootstrap"] = paired_bootstrap(
        candidate, baseline, n_resamples=n_resamples, seed=seed
    )
    return report


# --------------------------------------------------------------------------
# Paired bootstrap + paired-t cross-check (D-006; Smucker et al., CIKM 2007)
# --------------------------------------------------------------------------


def paired_bootstrap(
    metric_per_query_a: Sequence[float],
    metric_per_query_b: Sequence[float],
    n_resamples: int = 10000,
    seed: int = DEFAULT_BOOTSTRAP_SEED,
    confidence: float = 0.95,
) -> Dict[str, Any]:
    """Bootstrap the paired per-query metric difference ``A - B``.

    The IR-significance regime for this dataset is ~29 queries per split —
    the TREC 50-topic territory Smucker et al. (CIKM 2007) studied, where
    the bootstrap and the paired t-test are interchangeable and Wilcoxon is
    anti-conservative; Urbano et al. confirm the choice. Per D-006 this
    function is the accept gate: a lever ships only if its validate-split
    delta passes here.

    Method (all resampling from one seeded ``random.Random`` — same inputs,
    seed, and ``n_resamples`` give byte-identical output):

    * ``delta`` = mean of the paired differences ``a_i - b_i`` (positive =
      candidate A better than baseline B on average).
    * ``ci_low``/``ci_high`` = percentile bootstrap confidence interval of
      the mean difference at ``confidence`` (default 95%): resample the
      difference vector with replacement, percentile the resampled means.
    * ``p_value`` = two-sided bootstrap hypothesis test of H0: mean
      difference = 0 — the differences are recentered on the null (each
      ``d_i - delta``), resampled, and p is the fraction of resampled null
      means at least as extreme as ``|delta|`` (Davison & Hinkley's shifted
      null, with the add-one correction so p is never 0). The accept rule
      is ``p_value < alpha`` where ``alpha = 1 - confidence``.
    * ``t_statistic``/``p_value_t`` = the paired t-test on the same
      difference vector as a cross-check (Smucker: interchangeable with the
      bootstrap at this n; large disagreement flags a pathological sample,
      not a second opinion to shop between).

    Returns ``{delta, ci_low, ci_high, p_value, significant, t_statistic,
    p_value_t, n_queries, n_resamples, confidence}``.

    ``ValueError`` on empty or length-mismatched inputs (the bootstrap is
    strictly paired — silently truncating would drop queries), or on a
    non-positive ``n_resamples``/``confidence`` outside (0, 1).
    """
    if len(metric_per_query_a) != len(metric_per_query_b):
        raise ValueError(
            f"paired bootstrap requires equal-length per-query arrays, got "
            f"{len(metric_per_query_a)} and {len(metric_per_query_b)}"
        )
    if not metric_per_query_a:
        raise ValueError("paired bootstrap requires at least one query pair")
    if n_resamples <= 0:
        raise ValueError(f"n_resamples must be positive, got {n_resamples}")
    if not 0.0 < confidence < 1.0:
        raise ValueError(f"confidence must be within (0.0, 1.0), got {confidence}")

    n = len(metric_per_query_a)
    diffs = [float(a) - float(b) for a, b in zip(metric_per_query_a, metric_per_query_b)]
    delta = sum(diffs) / n

    rng = random.Random(seed)

    # Percentile CI: resample the observed difference vector.
    boot_means: List[float] = []
    for _ in range(n_resamples):
        total = 0.0
        for _ in range(n):
            total += diffs[rng.randrange(n)]
        boot_means.append(total / n)
    boot_means.sort()
    alpha = 1.0 - confidence
    ci_low = _percentile(boot_means, 100.0 * (alpha / 2.0))
    ci_high = _percentile(boot_means, 100.0 * (1.0 - alpha / 2.0))

    # Two-sided p: recenter the differences on the null (mean 0) and count
    # resamples at least as extreme as the observed |delta| (add-one).
    null_diffs = [d - delta for d in diffs]
    extreme = 0
    for _ in range(n_resamples):
        total = 0.0
        for _ in range(n):
            total += null_diffs[rng.randrange(n)]
        if abs(total / n) >= abs(delta):
            extreme += 1
    p_value = (1.0 + extreme) / (1.0 + n_resamples)

    t_statistic, p_value_t = _paired_t(diffs, delta)

    return {
        "delta": delta,
        "ci_low": ci_low,
        "ci_high": ci_high,
        "p_value": p_value,
        "significant": p_value < alpha,
        "t_statistic": t_statistic,
        "p_value_t": p_value_t,
        "n_queries": n,
        "n_resamples": n_resamples,
        "confidence": confidence,
    }


def _percentile(sorted_values: Sequence[float], pct: float) -> float:
    """Linear-interpolation percentile of a pre-sorted sequence.

    Matches numpy's default ``percentile`` method without the dependency:
    rank ``pct/100 * (n - 1)`` interpolated between neighbors.
    """
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])
    rank = (pct / 100.0) * (n - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return float(sorted_values[int(rank)])
    frac = rank - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def _paired_t(diffs: Sequence[float], delta: float) -> Tuple[float, float]:
    """Paired t-statistic and two-sided p on a difference vector.

    The p-value comes from the regularized incomplete beta function
    (pure stdlib — scipy is not a dependency of this project):
    ``p = I_x(df/2, 1/2)`` with ``x = df / (df + t^2)``, evaluated via the
    continued-fraction expansion (Numerical Recipes; verified against
    t-table critical values to < 5e-5). Degenerate zero-variance samples
    get ``|t| = inf`` (p = 0) for a nonzero mean and ``t = 0`` (p = 1) for
    an exactly-zero mean.
    """
    n = len(diffs)
    df = n - 1
    if n < 2 or df < 1:
        return 0.0, 1.0
    variance = sum((d - delta) ** 2 for d in diffs) / df
    if variance == 0.0:
        if delta == 0.0:
            return 0.0, 1.0
        return math.copysign(math.inf, delta), 0.0
    sem = math.sqrt(variance) / math.sqrt(n)
    t_statistic = delta / sem
    x = df / (df + t_statistic * t_statistic)
    return t_statistic, _betainc_reg(df / 2.0, 0.5, x)


def _betainc_reg(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta function ``I_x(a, b)`` (NR 6.4)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    ln_bt = (
        math.lgamma(a + b)
        - math.lgamma(a)
        - math.lgamma(b)
        + a * math.log(x)
        + b * math.log1p(-x)
    )
    bt = math.exp(ln_bt)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _betacf(a: float, b: float, x: float, max_iter: int = 300, eps: float = 3e-12) -> float:
    """Continued fraction for the incomplete beta (NR ``betacf``)."""
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, max_iter + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < eps:
            break
    return h


# --------------------------------------------------------------------------
# Sweep harness core (FR-005, D-007, T004)
#
# Enumerates lever combinations and evaluates each on the TUNE split only,
# through the guarded evaluate_on seam — held-out discipline is inherited
# from the seam (purpose="selection" + held_out_ids=validate), never
# re-implemented here. The results table is a separate artifact shape from
# quality.json (D-007): ``cairn-quality-sweep/1``, destined for
# benchmarks/quality/ablation.json (AC1) — committing it is T024's job;
# this module only RETURNS the table (no write path anywhere, TC-025).
# --------------------------------------------------------------------------

#: Schema tag of the sweep results table (D-007: own artifact shape, never
#: inside quality.json's exact-key contract).
SWEEP_SCHEMA = "cairn-quality-sweep/1"

#: Row name of the implicit all-levers-off combo — ``params=None``, today's
#: retrieval exactly. This is the integrity row T006 re-measures against
#: DS-v1 (full-set recall@10 0.4174 / MRR 0.2862).
ALL_LEVERS_OFF = "all-levers-off"


def _normalize_combos(
    combos: Iterable[Any],
) -> List[Tuple[str, Optional["RetrievalParams"]]]:
    """Validate and freeze the sweep grid into ``(name, params)`` pairs.

    Each combo is a mapping ``{"name": str, "params": RetrievalParams | None}``
    (``params`` may be omitted — omitted means ``None`` means today's
    defaults). Raises ``ValueError`` on a non-mapping combo, a missing or
    blank name, a ``params`` that is neither ``None`` nor a
    ``RetrievalParams`` instance, or a duplicate name (rows are keyed by
    name; a duplicate would silently overwrite results downstream).
    """
    from cairn.graph.semantic import RetrievalParams  # lazy: keeps import light

    normalized: List[Tuple[str, Optional["RetrievalParams"]]] = []
    seen: Set[str] = set()
    for index, combo in enumerate(combos):
        if not isinstance(combo, Mapping):
            raise ValueError(
                f"combo #{index} must be a mapping "
                f"{{'name': str, 'params': RetrievalParams | None}}, "
                f"got {type(combo).__name__}"
            )
        name = combo.get("name")
        params = combo.get("params", None)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"combo #{index} needs a non-empty string 'name', got {name!r}"
            )
        if params is not None and not isinstance(params, RetrievalParams):
            raise ValueError(
                f"combo {name!r}: 'params' must be a RetrievalParams instance "
                f"or None, got {type(params).__name__}"
            )
        if name in seen:
            raise ValueError(
                f"duplicate combo name {name!r}: sweep rows are keyed by name"
            )
        seen.add(name)
        normalized.append((name, params))
    return normalized


def run_sweep(
    conn: sqlite3.Connection,
    queries: Iterable[Any],
    *,
    combos: Sequence[Mapping[str, Any]],
    split_seed: int = DEFAULT_SPLIT_SEED,
    ids: Optional[Iterable[str]] = None,
    baseline: Optional[str] = None,
    metric: str = "recall_at_10",
    dataset_name: str = "ground-truth",
    dataset_version: str = "1",
    bundle_root: Optional[str] = None,
    k: int = 10,
    timer: Callable[[], float] = time.perf_counter,
) -> Dict[str, Any]:
    """Run every lever combination on the tune split; return the D-007 table.

    This is the library core (T005's CLI is a thin consumer). For each combo
    ``{"name": ..., "params": RetrievalParams | None}`` the harness calls the
    guarded seam — ``evaluate_on(ids=..., purpose="selection",
    held_out_ids=validate)`` — so held-out enforcement is the seam's, not a
    copy of it: any requested id that intersects the validate half raises
    :class:`HeldOutError` *before any retrieval runs* (FR-006, TC-019).

    Query-subset selection (the FR-005 gap: ``corpus_filter`` selects level
    only, ``load_ground_truth`` full-loads) lives in the seam's ``ids=``
    parameter: by default the harness selects the tune half of
    ``split_queries(queries, seed=split_seed)``; pass ``ids=`` explicitly for
    a narrower tune-side subset (still guarded — validate intersection
    raises). ``queries`` is the loaded ground truth (``load_ground_truth``
    output); a generator is fine, it is materialized once.

    Rows carry ``{combo, recall_at_10, mrr, p95_ms, n_queries}`` where
    ``p95_ms`` is the 95th-percentile per-query retrieval wall time measured
    by the seam (``timer`` injectable for deterministic tests). The implicit
    **all-levers-off** row (``params=None``, named :data:`ALL_LEVERS_OFF`) is
    prepended FIRST whenever the caller's grid carries no ``params=None``
    combo of its own — the integrity row T006 depends on; an explicit
    ``params=None`` combo suppresses the implicit one (never evaluated
    twice).

    Returns the D-007 document::

        {"schema": "cairn-quality-sweep/1",
         "dataset": {"name", "version", "split_seed", "split", "metric",
                     "n_queries"},
         "rows": [...],
         "baseline": {"combo", "metric", "per_query"}}

    ``baseline`` selects the incumbent combo by name (default: the
    all-levers-off row) — the emitted ``per_query`` map carries its
    selection-metric values keyed by query id, exactly what a downstream
    ``evaluate_on(purpose="validate", baseline_metrics=...)`` run pairs
    against (T011/T012 flow). All metrics are rounded to 4 decimals
    deterministically.

    Read-only by construction (TC-025): the harness opens no write path —
    the ground-truth files behind ``queries`` are only ever read, no file is
    created, and the only database statements issued are retrieval reads.
    Serializing (and committing to ``benchmarks/quality/ablation.json``) is
    the caller's job — :func:`format_sweep_json` gives the canonical bytes.

    ``ValueError`` is raised for an unknown metric, malformed or
    duplicate-named combos, or a ``baseline`` naming no combo in the sweep.
    """
    if metric not in _METRICS:
        raise ValueError(
            f"unknown metric {metric!r}; expected one of {list(_METRICS)}"
        )

    # Materialize once: split_queries and every seam call iterate queries.
    query_list = list(queries)
    normalized = _normalize_combos(combos)

    # The integrity row: today's retrieval (params=None), first, whenever
    # the grid doesn't already carry a params-None combo.
    if not any(params is None for _name, params in normalized):
        normalized.insert(0, (ALL_LEVERS_OFF, None))

    names = {name for name, _params in normalized}
    if baseline is not None and baseline not in names:
        raise ValueError(
            f"baseline {baseline!r} names no combo in the sweep "
            f"(combos: {sorted(names)})"
        )

    # The split (T001) and the guard it feeds. held_out_ids is handed to the
    # seam unconditionally — the seam IS the enforcement.
    tune_ids, validate_ids = split_queries(query_list, seed=split_seed)
    id_list = tune_ids if ids is None else sorted(set(ids))

    reports: Dict[str, Dict[str, Any]] = {}
    rows: List[Dict[str, Any]] = []
    for name, params in normalized:
        report = evaluate_on(
            conn,
            query_list,
            ids=id_list,
            purpose="selection",
            held_out_ids=validate_ids,
            metric=metric,
            bundle_root=bundle_root,
            k=k,
            params=params,
            timer=timer,
        )
        reports[name] = report
        durations = sorted(report["durations_ms"].values())
        rows.append(
            {
                "combo": name,
                "recall_at_10": report["recall_at_10"],
                "mrr": report["mrr"],
                "p95_ms": round(_percentile(durations, 95.0), 4),
                "n_queries": report["n_queries"],
            }
        )

    baseline_name = baseline if baseline is not None else next(
        name for name, params in normalized if params is None
    )
    base_report = reports[baseline_name]
    return {
        "schema": SWEEP_SCHEMA,
        "dataset": {
            "name": dataset_name,
            "version": dataset_version,
            "split_seed": split_seed,
            "split": "tune",
            "metric": metric,
            "n_queries": len(id_list),
        },
        "rows": rows,
        "baseline": {
            "combo": baseline_name,
            "metric": metric,
            "per_query": {
                qid: base_report["per_query"][qid][metric]
                for qid in sorted(base_report["per_query"])
            },
        },
    }


def evaluate_full_set(
    conn: sqlite3.Connection,
    queries: Iterable[Any],
    *,
    params: Optional["RetrievalParams"] = None,
    bundle_root: Optional[str] = None,
    k: int = 10,
    timer: Callable[[], float] = time.perf_counter,
) -> Dict[str, Any]:
    """Evaluate the FULL query set — every id, no split, no guard.

    The reporting path behind the sweep's integrity row (T006: the
    all-levers-off full-set run reproduces DS-v1's L1 recall@10 0.4174 /
    MRR 0.2862 at 4 decimals) and Phase 5's final numbers. It is NOT a
    selection path: lever decisions run through :func:`run_sweep` on the
    tune split and the bootstrap guard on validate — this function is only
    legitimate *after* those decisions, with the split disclosed wherever
    its numbers are reported (FR-006).

    ``queries`` is the loaded ground truth (level filtering is the caller's
    choice — hand it the 58 L1 queries for the DS-v1 figure); ``params`` is
    the injected ``RetrievalParams`` (``None`` = today's retrieval, the
    integrity config).

    Returns the seam's report shape — ``{purpose: "full-set", n_queries,
    recall_at_10, mrr, per_query, durations_ms}`` with recall/MRR rounded
    to 4 decimals. ``ValueError`` on an empty query set or bare id strings
    (same contract as the seam).
    """
    by_id: Dict[str, Any] = {}
    for q in queries:
        if isinstance(q, str):
            raise ValueError(
                "evaluate_full_set requires GradedQuery objects "
                "(load_ground_truth output); bare id strings carry no "
                "expectations to score"
            )
        by_id[q.query_id] = q
    id_list = sorted(by_id)
    if not id_list:
        raise ValueError("evaluate_full_set received no queries")

    per_query: Dict[str, Dict[str, float]] = {}
    durations_ms: Dict[str, float] = {}
    for qid in id_list:
        started = timer()
        rec, rr = evaluate_graded_query(
            conn, bundle_root, by_id[qid], k=k, params=params
        )
        durations_ms[qid] = (timer() - started) * 1000.0
        per_query[qid] = {"recall_at_10": rec, "mrr": rr}

    n = len(id_list)
    return {
        "purpose": "full-set",
        "n_queries": n,
        "recall_at_10": round(sum(p["recall_at_10"] for p in per_query.values()) / n, 4),
        "mrr": round(sum(p["mrr"] for p in per_query.values()) / n, 4),
        "per_query": per_query,
        "durations_ms": durations_ms,
    }


def format_sweep_json(doc: Mapping[str, Any]) -> str:
    """Canonical byte serialization of a sweep table (D-007).

    ``json.dumps`` with sorted keys and a trailing newline: the same
    document always serializes to identical bytes, so re-runs diff clean
    and the committed ``benchmarks/quality/ablation.json`` (AC1, T024) has
    exactly one canonical shape. Write it with ``open(path, "w")`` at the
    CLI layer — this function stays read-only (TC-025).
    """
    return json.dumps(doc, indent=2, sort_keys=True) + "\n"


def evaluate_l1_query(
    conn: sqlite3.Connection,
    query: str,
    expect: List[str],
    k: int = 10,
    params: Optional["RetrievalParams"] = None,
) -> Tuple[float, float]:
    """Evaluate L1 query using semantic_search / search_symbols.

    Returns (recall_at_k, reciprocal_rank). ``params`` (D-008) is threaded
    through to ``semantic_search`` verbatim; ``None`` keeps today's defaults.
    """
    from cairn.graph import queries as qmod

    # Try hybrid/semantic retrieval first; fall back to lexical if embeddings are empty
    try:
        results = qmod.semantic_search(conn, query, limit=k, params=params)
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


def _retrieve_l1(
    conn: sqlite3.Connection,
    query: str,
    k: int,
    params: Optional["RetrievalParams"] = None,
) -> List[Any]:
    """L1 retrieval pipeline (semantic first, lexical fallback), full rows.

    ``params`` (D-008) reaches the ``semantic_search`` call; the lexical
    fallback leg takes no retrieval tunables today.
    """
    from cairn.graph import queries as qmod

    try:
        results = list(qmod.semantic_search(conn, query, limit=k, params=params))
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
    params: Optional["RetrievalParams"] = None,
) -> Tuple[float, float]:
    """Evaluate one graded query through its level's retrieval pipeline.

    ``params`` (D-008) applies to the L1 (semantic) leg only -- L5 retrieval
    is bundle search with no retrieval tunables.
    """
    if graded.level == "L1":
        results = _retrieve_l1(conn, graded.text, k, params=params)
    else:
        results = _retrieve_l5(bundle_root, graded.text, k)
    return score_graded_query(results, graded.expectations, k)


def _run_graded_evaluation(
    conn: sqlite3.Connection,
    bundle_root: Optional[str],
    graded_dir: Path,
    corpus_filter: str = "all",
    k: int = 10,
    params: Optional["RetrievalParams"] = None,
) -> Dict[str, Any]:
    """Graded counterpart of the yaml loop in ``run_evaluation``.

    Report shape mirrors the yaml one — ``{"L1": {...}, "L5": {...}}`` with
    ``count``/``recall_at_10``/``mrr`` — plus additive ``n_queries`` and
    ``n_expectations`` keys for dataset-size visibility. ``params`` (D-008)
    threads through to every retrieval call.
    """
    queries = load_ground_truth(graded_dir)

    stats = {
        "L1": {"count": 0, "recall": 0.0, "mrr": 0.0, "n_expectations": 0},
        "L5": {"count": 0, "recall": 0.0, "mrr": 0.0, "n_expectations": 0},
    }

    for graded in queries:
        if corpus_filter != "all" and graded.level != corpus_filter:
            continue
        rec, rr = evaluate_graded_query(conn, bundle_root, graded, k=k, params=params)
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
    params: Optional["RetrievalParams"] = None,
) -> Dict[str, Any]:
    """Run full evaluation harness across specified corpus ("L1", "L5", or "all").

    ``queries_path`` selects the query source (D-008 — two loaders, one
    harness): a *directory* holding the graded D-004 pair
    (``queries.jsonl`` + ``expectations.tsv``) takes the graded loader and
    the identity-first matcher; anything else (a yaml file, or None for the
    bundled fixture) takes the legacy yaml path unchanged.

    ``params`` (D-008, FR-005) is an explicit frozen ``RetrievalParams``
    threaded through to every L1 ``semantic_search`` call in both paths —
    the injection channel the sweep harness uses instead of mutating the
    environment (in-process env writes would leak across combinations).
    ``None`` (the default, and every ``None`` field) preserves today's
    retrieval behavior exactly.
    """
    if queries_path is not None and Path(queries_path).is_dir():
        return _run_graded_evaluation(
            conn, bundle_root, Path(queries_path), corpus_filter, k, params=params
        )

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
            rec, rr = evaluate_l1_query(conn, q_text, expect, k=k, params=params)
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
