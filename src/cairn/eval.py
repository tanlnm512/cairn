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
accept gate any lever must pass on the validate split. ``kfold_partitions``
(FR-001) rotates the same determinism recipe into k >= 5 seeded folds in
which every query is held out exactly once, and ``run_sweep_kfold``
(FR-001) runs the lever sweep once per fold through that unchanged seam —
selection ids = all minus the fold's, the fold's own ids handed to the
seam as the flat ``held_out_ids`` iterable.

The sweep harness (FR-005, D-007, T004) sits on top of that seam:
``run_sweep`` enumerates lever combinations (each an injected
``RetrievalParams``), evaluates every combo on the tune split only —
held-out enforcement is inherited from the seam, not re-implemented — and
emits the machine-readable multi-row results table in its own schema
(``cairn-quality-sweep/2``). Combos may also name a corpus recipe
(``variant`` — T014, FR-002): the runner re-embeds the measurement DB
under that variant through the content-hash staleness flow before
evaluating the combo, and every row carries db_mb + chunk size bounds
(the per-recipe size accounting the survey flagged missing). Every combo
measures under its OWN declared embedding state — variant combos under
their recipe, non-variant combos under the session baseline, which
``_EmbeddingStateMachine`` snapshots once and restores before any
non-variant combo that follows a variant one (a fold's leftover recipe
never leaks into the next fold's rows). The
ground-truth files stay read-only (TC-025 — byte-identical after every
sweep, recipe or not); the only write path is the measurement DB's
embeddings table, via ``embed_all``, for variant combos (plus the
rowid-exact baseline restores).
``evaluate_full_set`` is the post-selection reporting path (the full set,
no split) behind the integrity row and final numbers. Serializing the
table is the caller's job (``format_sweep_json`` gives the canonical
bytes).
"""
from __future__ import annotations

import inspect
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
# Seeded k-fold rotation partition (FR-001, TC-002, TC-004)
#
# The single seeded split above rotates: the same determinism recipe
# (dedupe + sort BEFORE the seeded shuffle) cut into k contiguous slices,
# so every id is held out exactly once across the rotation. The >= 5 fold
# floor is deliberate — a weaker fold count must be refused loudly, never
# silently run.
# --------------------------------------------------------------------------

#: Fold-count floor for k-fold rotation (FR-001): 5 is the minimum the
#: harness will run; anything less is a configuration error (TC-004).
MIN_KFOLD_K = 5


def kfold_partitions(
    ids: Iterable[Any],
    *,
    k: int = MIN_KFOLD_K,
    seed: int = DEFAULT_SPLIT_SEED,
) -> List[List[str]]:
    """Partition query ids into k seeded rotation folds (FR-001).

    ``ids`` is any iterable of id strings or objects exposing a
    ``query_id`` field (same coercion as :func:`split_queries` — only the
    id is read, so the function is level-agnostic; filtering is the
    caller's choice). The input is never mutated.

    The determinism recipe is :func:`split_queries`'s exactly: ids are
    deduplicated and *sorted before* the seeded ``random.Random(seed)
    .shuffle``, then the shuffled list is cut into k contiguous slices —
    the first ``n mod k`` folds take one extra id, so fold sizes differ
    by at most one. The partition is a pure function of (id set, k,
    seed): never of input order, set/dict iteration order, or
    ``PYTHONHASHSEED``.

    Contract (TC-002, TC-004):

    * **Reproducible** — two calls with the same (ids, k, seed) return
      identical folds, in-process or not; the default seed is the fixed
      :data:`DEFAULT_SPLIT_SEED`.
    * **Rotation-exact** — the folds partition the input: the union of
      all k folds equals the id set and no id appears in two folds (each
      id is held out exactly once and serves as tuning material in all
      other folds).
    * **Non-degenerate** — every fold is non-empty: ``k`` folds need at
      least ``k`` distinct ids.
    * **Floor** — ``k < 5`` raises ``ValueError`` naming the minimum;
      the fold count is a floor, not a suggestion, and the default runs
      at exactly 5.

    Returned folds keep shuffle order (fold 0 first) so re-run diffs are
    empty.
    """
    if k < MIN_KFOLD_K:
        raise ValueError(
            f"k-fold rotation requires k >= {MIN_KFOLD_K} folds, got {k!r}: "
            f"the fold-count floor is {MIN_KFOLD_K} — a weaker evaluation "
            "is refused rather than silently run (FR-001, TC-004)"
        )

    # Dedupe + sort BEFORE shuffling: the sort kills any dependence on
    # input order or hash randomization, making the shuffle deterministic
    # given (id set, seed) — the same rule split_queries relies on.
    shuffled = sorted(
        {item if isinstance(item, str) else item.query_id for item in ids}
    )
    if len(shuffled) < k:
        raise ValueError(
            f"k-fold rotation needs at least {k} distinct ids to form {k} "
            f"non-empty folds, got {len(shuffled)}"
        )
    rng = random.Random(seed)
    rng.shuffle(shuffled)

    # Contiguous slices; the first (n mod k) folds carry the remainder.
    base, extra = divmod(len(shuffled), k)
    folds: List[List[str]] = []
    start = 0
    for fold_index in range(k):
        size = base + 1 if fold_index < extra else base
        folds.append(shuffled[start : start + size])
        start += size
    return folds


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
# Sweep harness core (FR-005, D-007, T004; recipe extension T014, FR-002)
#
# Enumerates lever combinations and evaluates each on the TUNE split only,
# through the guarded evaluate_on seam — held-out discipline is inherited
# from the seam (purpose="selection" + held_out_ids=validate), never
# re-implemented here. The results table is a separate artifact shape from
# quality.json (D-007): ``cairn-quality-sweep/2``, destined for
# benchmarks/quality/ablation.json (AC1) — committing it is T024's job;
# this module only RETURNS the table.
#
# Recipe combos (T014): a combo carrying ``variant`` re-embeds the
# measurement DB through ``embeddings.embed_all(conn, variant=...)``
# BEFORE it is evaluated. The content-hash staleness flow in embeddings.py
# recomputes every symbol's chunk under the requested recipe, so any
# recipe change flips every ``_chunk_hash`` and forces a full re-embed
# (rowid-stable upsert keeps the vec0 keys aligned); re-running the same
# variant is idempotent — the hashes already match. Per-variant
# measurement runs are serial machine time, not agent time.
#
# Per-combo embedding state: every combo measures under its OWN declared
# state — the variant's for recipe combos, the session baseline (the state
# the DB entered the sweep in) for every non-variant combo. Re-embedding is
# destructive, so ``_EmbeddingStateMachine`` snapshots the baseline table
# once before the first re-embed and restores it before any non-variant
# combo that follows a variant one. Without that, a later combo — the
# prepended integrity row of a k-fold fold >= 1, or any variant-less combo
# ordered after a variant — would measure under the last variant's
# leftovers: grid position and fold position, never the combo's own
# declaration, would decide the state its numbers were taken under.
#
# Read-only scope (TC-025): the ground-truth files behind ``queries`` are
# only ever read and no file other than the caller's chosen output is
# written; the measurement DB's embeddings table IS written, but only for
# variant combos (through ``embed_all``) and baseline restoration (the
# rowid-exact snapshot rewrite, never a model call). The DB ends the sweep
# in the LAST EVALUATED combo's declared state — the final variant's
# recipe when a variant combo closes the grid, the restored session
# baseline otherwise — so run recipe sweeps on a disposable copy when the
# starting state must survive.
# --------------------------------------------------------------------------

#: Schema tag of the sweep results table (D-007: own artifact shape, never
#: inside quality.json's exact-key contract). v2 (T014) extends the row
# shape ADDITIVELY: every row gains the size-accounting fields
#: ``db_mb``/``chunk_chars_max``/``chunk_chars_mean`` (measured on the
#: embedding state the row evaluated under — FR-002's per-recipe size
#: bounds), and recipe combos additionally carry the optional ``variant``
#: field marking the corpus recipe they re-embedded to. A v1 consumer that
#: ignores unknown row fields keeps working; one that validates row keys
#: exactly must move to the v2 shape.
SWEEP_SCHEMA = "cairn-quality-sweep/2"

#: Row name of the implicit all-levers-off combo — ``params=None``, today's
#: retrieval exactly. This is the integrity row T006 re-measures against
#: DS-v1 (full-set recall@10 0.4174 / MRR 0.2862).
ALL_LEVERS_OFF = "all-levers-off"

#: Schema tag of the k-fold sweep document (the rotation counterpart of
#: :data:`SWEEP_SCHEMA`). The document carries one entry per fold in
#: ``folds`` — each with the :data:`SWEEP_SCHEMA` row shape over that fold's
#: selection material — plus the ``aggregate`` block (the pooled paired
#: bootstrap verdict and descriptive rotation statistics), and is extended
#: additively by later consumers (CLI emission); consumers must tolerate
#: unknown keys.
KFOLD_SWEEP_SCHEMA = "cairn-quality-sweep-kfold/1"


def _normalize_combos(
    combos: Iterable[Any],
) -> List[Tuple[str, Optional["RetrievalParams"], Optional[str]]]:
    """Validate and freeze the sweep grid into ``(name, params, variant)``.

    Each combo is a mapping ``{"name": str, "params": RetrievalParams |
    None, "variant": str (optional)}`` (``params`` may be omitted — omitted
    means ``None`` means today's defaults; ``variant`` omitted means no
    re-embed, the combo evaluates under the current embedding state).
    Variant names are NOT restricted to a known set — the recipe registry
    is additive beyond A/B/C (T013's field-dropout variants), and an
    unknown name is ``embed_all``'s loud business, not the grid parser's.
    Raises ``ValueError`` on a non-mapping combo, a missing or blank name,
    a ``params`` that is neither ``None`` nor a ``RetrievalParams``
    instance, a ``variant`` that is neither ``None`` nor a non-empty
    string, or a duplicate name (rows are keyed by name; a duplicate would
    silently overwrite results downstream).
    """
    from cairn.graph.semantic import RetrievalParams  # lazy: keeps import light

    normalized: List[Tuple[str, Optional["RetrievalParams"], Optional[str]]] = []
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
        variant = combo.get("variant", None)
        if not isinstance(name, str) or not name.strip():
            raise ValueError(
                f"combo #{index} needs a non-empty string 'name', got {name!r}"
            )
        if params is not None and not isinstance(params, RetrievalParams):
            raise ValueError(
                f"combo {name!r}: 'params' must be a RetrievalParams instance "
                f"or None, got {type(params).__name__}"
            )
        if variant is not None and (
            not isinstance(variant, str) or not variant.strip()
        ):
            raise ValueError(
                f"combo {name!r}: 'variant' must be a non-empty string "
                f"(a corpus recipe name) or None, got {variant!r}"
            )
        if name in seen:
            raise ValueError(
                f"duplicate combo name {name!r}: sweep rows are keyed by name"
            )
        seen.add(name)
        normalized.append((name, params, variant))
    return normalized


def _accepts_variant_kwarg(func: Callable[..., Any]) -> bool:
    """True when ``func`` can be called with ``variant=``.

    Accepts either a named ``variant`` parameter or a ``**kwargs`` catch-all
    (the shape a test double or wrapper naturally takes). A function that
    cannot be introspected is given the benefit of the doubt — the call
    itself will fail loudly if the keyword really is unsupported.
    """
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):  # non-introspectable callable
        return True
    return any(
        p.name == "variant" or p.kind is inspect.Parameter.VAR_KEYWORD
        for p in sig.parameters.values()
    )


def _reembed_for_variant(conn: sqlite3.Connection, variant: str) -> Dict[str, Any]:
    """Re-embed the measurement corpus under ``variant`` (T014, FR-002).

    Delegates to ``embeddings.embed_all(conn, variant=variant)`` — T013's
    contract: the variant threads down to ``chunk_for_symbol``, the
    content-hash staleness flow inside recomputes every chunk under the
    requested recipe (any recipe change flips every ``_chunk_hash`` and
    forces a full re-embed), and the rowid-stable upsert keeps the vec0
    index keys aligned. Re-running the same variant is idempotent (the
    stored hashes already match).

    The ``variant`` keyword is verified against the *resolved* ``embed_all``
    at runtime, not at import: T013 lands in parallel with this module, so
    a recipe sweep in an install without the contract fails with ONE loud
    ``RuntimeError`` naming the seam instead of a bare ``TypeError`` deep
    in a call chain.
    """
    from cairn.graph import embeddings as emb

    if not _accepts_variant_kwarg(emb.embed_all):
        raise RuntimeError(
            "recipe sweep requires embed_all to accept a 'variant' keyword "
            "(T013 contract: variant: str | None = None threaded to "
            "chunk_for_symbol); this install's embed_all does not — re-embed "
            "orchestration is unavailable (FR-002)"
        )
    return emb.embed_all(conn, variant=variant)


class _EmbeddingStateMachine:
    """Install each combo's declared embedding state before it is evaluated.

    A sweep grid mixes two kinds of combos: variant combos, which re-embed
    the measurement DB under their recipe through :func:`_reembed_for_variant`,
    and non-variant combos, whose declared state is the SESSION BASELINE —
    the embedding state the DB entered the sweep in (the integrity row's
    measurement state, D-009/T011 doctrine). Re-embedding is destructive
    (the previous state's rows are overwritten in place), so the machine
    snapshots the baseline ``embeddings`` table once, lazily, at the last
    moment that state is still installed (right before the first
    re-embed), and restores that snapshot before every non-variant combo
    that follows a variant one. One snapshot per sweep at most, one restore
    per variant-to-baseline transition — never anything per combo "just in
    case".

    Why a snapshot rather than re-embedding back: the baseline state's
    recipe is recorded nowhere the harness can read (the embeddings table
    stores content hashes, not recipe names), so re-embedding under
    ``variant=None`` would merely ASSUME the env-default recipe produced the
    baseline — untrue for a DB that entered the sweep mid-variant or with
    no embeddings at all. The snapshot restores the baseline
    byte-identically in every case and never invokes the embed model.
    Variant installs stay unconditional per variant combo (the
    ``embed_all`` call per recipe combo is an observable orchestration
    contract, and its content-hash staleness already makes a repeated
    variant a no-op).

    The restore is rowid-exact (DELETE + INSERT carrying the snapshot's
    rowids), so a vec0 ANN index keyed on ``embeddings.rowid`` stays exactly
    as aligned as the re-embed seam itself leaves it. Only the
    ``embeddings`` table is restored: it is the only table the base embed
    flow writes that retrieval reads — ``term_df`` is rebuilt from the
    symbols table, which no recipe touches, so a re-embed never changes it.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        #: Recipe currently installed; None means the session baseline.
        self._installed: Optional[str] = None
        #: Lazy snapshot of the baseline embeddings rows (None until the
        #: first re-embed is about to destroy that state).
        self._baseline_rows: Optional[List[Tuple[Any, ...]]] = None

    def install(self, variant: Optional[str]) -> None:
        """Ensure the DB is in ``variant``'s state (``None`` = baseline).

        Non-variant grids never leave the baseline, so they never snapshot,
        never restore, and never touch the DB here at all — their sweeps
        stay byte-identical to a build without this class.
        """
        if variant is not None:
            self._leave_baseline_into(variant)
        else:
            self._restore_baseline()

    def _leave_baseline_into(self, variant: str) -> None:
        if self._baseline_rows is None:
            # Last moment the session-baseline state is still installed.
            self._baseline_rows = [
                tuple(row)
                for row in self._conn.execute(
                    "SELECT rowid, symbol_id, model, dim, vec, chunk, "
                    "content_hash, embedded_at FROM embeddings"
                )
            ]
        _reembed_for_variant(self._conn, variant)
        self._installed = variant

    def _restore_baseline(self) -> None:
        rows = self._baseline_rows
        if rows is None:
            return  # never left the baseline — nothing to restore
        self._conn.execute("DELETE FROM embeddings")
        self._conn.executemany(
            "INSERT INTO embeddings (rowid, symbol_id, model, dim, vec, "
            "chunk, content_hash, embedded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        self._installed = None


def _size_accounting(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Measure the CURRENT embedding state's size figures (FR-002 gap).

    Returns the three size-accounting columns every sweep row carries:

    * ``db_mb`` — the main database's size in MiB, measured from the DB
      FILE when it is file-backed (the honest on-disk artifact); an
      in-memory/temp database has no file and falls back to
      ``page_count * page_size`` (the same committed size SQLite reports).
      For recipe rows this is measured AFTER that combo's re-embed; for
      the integrity row it is the session baseline (D-009/T011 doctrine).
    * ``chunk_chars_max`` / ``chunk_chars_mean`` — character lengths of
      the ``embeddings.chunk`` column (SQLite ``LENGTH`` on TEXT counts
      characters, matching the ``max_tokens * 4`` truncate bound in
      ``chunk_for_symbol`` — 2048 chars at the default). Both are 0 / 0.0
      when the embeddings table is empty.
    """
    size_bytes: Optional[int] = None
    for _seq, db_name, db_file in conn.execute("PRAGMA database_list").fetchall():
        if db_name != "main":
            continue
        if db_file:  # file-backed: '' for :memory: and temp DBs
            try:
                size_bytes = Path(db_file).stat().st_size
            except OSError:
                size_bytes = None
        break
    if size_bytes is None:
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        size_bytes = int(page_size) * int(page_count)

    lengths = [row[0] or 0 for row in conn.execute("SELECT LENGTH(chunk) FROM embeddings")]
    return {
        "db_mb": round(size_bytes / (1024.0 * 1024.0), 4),
        "chunk_chars_max": max(lengths, default=0),
        "chunk_chars_mean": round(sum(lengths) / len(lengths), 4) if lengths else 0.0,
    }


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
    ``{"name": ..., "params": RetrievalParams | None, "variant": str (opt)}``
    the harness calls the guarded seam — ``evaluate_on(ids=...,
    purpose="selection", held_out_ids=validate)`` — so held-out enforcement
    is the seam's, not a copy of it: any requested id that intersects the
    validate half raises :class:`HeldOutError` *before any retrieval runs*
    (FR-006, TC-019).

    **Recipe combos (T014, FR-002)** — a combo carrying ``variant`` is a
    corpus-recipe measurement: the runner calls
    ``embeddings.embed_all(conn, variant=...)`` BEFORE evaluating that
    combo. The content-hash staleness flow inside ``embed_all`` handles the
    re-embedding (any recipe change flips every ``_chunk_hash`` → full
    re-embed; rowid-stable upsert); a repeated variant re-embeds nothing.
    The variant keyword is verified against the resolved ``embed_all`` at
    runtime — a loud ``RuntimeError`` when the T013 contract is missing.
    Variant combos run serially; each is machine time, not agent time.
    Every combo measures under its OWN declared embedding state: the
    variant's for recipe combos, the session baseline for non-variant
    combos, restored by :class:`_EmbeddingStateMachine` whenever a variant
    combo overwrote it — grid position never decides which state a row's
    numbers came from (a non-variant combo ordered after a variant one
    measures the session baseline, not the leftover recipe).

    Query-subset selection (the FR-005 gap: ``corpus_filter`` selects level
    only, ``load_ground_truth`` full-loads) lives in the seam's ``ids=``
    parameter: by default the harness selects the tune half of
    ``split_queries(queries, seed=split_seed)``; pass ``ids=`` explicitly for
    a narrower tune-side subset (still guarded — validate intersection
    raises). ``queries`` is the loaded ground truth (``load_ground_truth``
    output); a generator is fine, it is materialized once.

    Rows carry ``{combo, recall_at_10, mrr, p95_ms, n_queries, db_mb,
    chunk_chars_max, chunk_chars_mean}`` where ``p95_ms`` is the
    95th-percentile per-query retrieval wall time measured by the seam
    (``timer`` injectable for deterministic tests) and the three size
    figures come from :func:`_size_accounting` measured on the embedding
    state the row evaluated under — for the integrity row that is the
    CURRENT state (no re-embed: its figures are the session baseline,
    D-009/T011 doctrine); for a recipe row, the state right after that
    combo's re-embed. Recipe rows are additionally marked with a
    ``variant`` field (absent on non-recipe rows).

    The implicit **all-levers-off** row (``params=None`` AND no variant,
    named :data:`ALL_LEVERS_OFF`) is prepended FIRST whenever the caller's
    grid carries no such combo of its own — the integrity row T006 depends
    on; it never re-embeds, and its session-baseline state is RESTORED
    before it whenever a variant combo preceded it, so it measures — and
    size-accounts — the session baseline in every grid position alike. An
    explicit params-None variant-less combo suppresses the implicit one
    (never evaluated twice). A variant combo with ``params=None`` does NOT
    suppress it — it re-embeds by definition.

    Returns the D-007 document::

        {"schema": "cairn-quality-sweep/2",
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

    Read-only scope (TC-025): the ground-truth files behind ``queries``
    are only ever read and no file is created — recipe sweeps included.
    The one write path is the measurement DB's ``embeddings`` table: the
    ``embed_all`` re-embeds of variant combos plus the rowid-exact baseline
    restores; the DB ends the sweep in the LAST EVALUATED combo's declared
    state, so run recipe sweeps on a disposable copy when the starting
    state must survive.
    Serializing (and committing to ``benchmarks/quality/ablation.json``)
    is the caller's job — :func:`format_sweep_json` gives the canonical
    bytes.

    ``ValueError`` is raised for an unknown metric, malformed or
    duplicate-named combos, or a ``baseline`` naming no combo in the
    sweep; ``RuntimeError`` when a variant combo runs against an
    ``embed_all`` without the T013 ``variant`` contract.
    """
    if metric not in _METRICS:
        raise ValueError(
            f"unknown metric {metric!r}; expected one of {list(_METRICS)}"
        )

    # Materialize once: split_queries and every seam call iterate queries.
    query_list = list(queries)
    normalized = _normalize_combos(combos)

    # The integrity row: today's retrieval (params=None, no variant), first,
    # whenever the grid doesn't already carry such a combo. A variant combo
    # with params=None does NOT suppress it — it re-embeds by definition and
    # so cannot serve as the session-baseline row.
    if not any(
        params is None and variant is None for _name, params, variant in normalized
    ):
        normalized.insert(0, (ALL_LEVERS_OFF, None, None))

    names = {name for name, _params, _variant in normalized}
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
    embed_state = _EmbeddingStateMachine(conn)
    for name, params, variant in normalized:
        # Per-combo embedding state: a variant combo re-embeds FIRST
        # (embeddings.py's content-hash flow); a non-variant combo measures
        # under the session baseline, restored by the machine whenever a
        # variant combo overwrote it (the integrity row never re-embeds —
        # its figures are the session baseline, D-009/T011 doctrine).
        embed_state.install(variant)
        size = _size_accounting(conn)
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
        row: Dict[str, Any] = {
            "combo": name,
            "recall_at_10": report["recall_at_10"],
            "mrr": report["mrr"],
            "p95_ms": round(_percentile(durations, 95.0), 4),
            "n_queries": report["n_queries"],
            "db_mb": size["db_mb"],
            "chunk_chars_max": size["chunk_chars_max"],
            "chunk_chars_mean": size["chunk_chars_mean"],
        }
        if variant is not None:
            row["variant"] = variant
        rows.append(row)

    baseline_name = baseline if baseline is not None else next(
        name
        for name, params, variant in normalized
        if params is None and variant is None
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


def run_sweep_kfold(
    conn: sqlite3.Connection,
    queries: Iterable[Any],
    *,
    combos: Sequence[Mapping[str, Any]],
    k_folds: int = MIN_KFOLD_K,
    fold_seed: int = DEFAULT_SPLIT_SEED,
    ids: Optional[Iterable[str]] = None,
    baseline: Optional[str] = None,
    metric: str = "recall_at_10",
    dataset_name: str = "ground-truth",
    dataset_version: str = "1",
    bundle_root: Optional[str] = None,
    k: int = 10,
    timer: Callable[[], float] = time.perf_counter,
) -> Dict[str, Any]:
    """Run the lever sweep once per fold of the seeded k-fold rotation.

    The rotation counterpart of :func:`run_sweep`: instead of one seeded
    tune/validate split, :func:`kfold_partitions` cuts the id set into
    ``k_folds`` seeded folds and the sweep runs once per fold i through the
    SAME unchanged seam ``run_sweep`` uses — ``evaluate_on(ids=selection,
    purpose="selection", held_out_ids=fold_i)`` — with the selection ids
    being *all ids minus fold i's* and the fold's own ids handed as the flat
    ``held_out_ids`` iterable. Held-out enforcement is inherited from the
    seam per fold, never re-implemented: a selection-stage read touching
    ANY fold's held-out ids raises :class:`HeldOutError` at that fold's
    turn, before that fold's retrieval runs (TC-003 — the loud-failure
    contract holds per fold, not just per single split).

    Contract:

    * **Per-fold discipline (D-009)** — every query is held out exactly
      once (its own fold) and serves as selection material in all other
      folds; the significance basis is never fold means: the
      ``aggregate`` block pools each query exactly once across the
      rotation and runs the unchanged :func:`paired_bootstrap` over the
      pooled per-query arrays (see the ``aggregate`` paragraph below).
    * **Determinism** — the folds are a pure function of (id set,
      ``k_folds``, ``fold_seed``); with a pinned ``timer`` the whole
      document serializes to identical bytes run over run.
    * **Floor inheritance** — ``k_folds < 5`` and fewer distinct ids than
      folds are refused loudly by :func:`kfold_partitions` (``ValueError``
      naming the floor).
    * **``ids=`` is the tamper channel, not a narrowing knob** — ``None``
      (the default) selects all-ids-minus-fold-i per fold; an explicit
      ``ids`` request is handed to the seam VERBATIM each fold, exactly as
      ``run_sweep`` hands it. Because the rotation holds out every id in
      exactly one fold, any non-empty request eventually names a held-out
      id and aborts at that fold's turn with :class:`HeldOutError` — the
      provable per-fold guard. To narrow the rotation, filter ``queries``
      (the module doctrine: filtering is the caller's choice).

    Returns the ``cairn-quality-sweep-kfold/1`` document::

        {"schema": ..., "dataset": {"name", "version", "fold_seed",
         "k_folds", "split": "kfold", "metric", "n_queries"},
         "folds": [{"fold": i, "held_out_ids": [...], "rows": [...],
                    "reports": {combo: report}, "baseline": {...}}],
         "aggregate": {"metric", "baseline", "significance_basis",
                       "combos": {candidate: {"pooled", "bootstrap",
                                              "descriptive"}}}}

    Each fold entry carries: ``held_out_ids`` (that fold's ids, sorted);
    ``rows`` (the :data:`SWEEP_SCHEMA` row shape, evaluated on that fold's
    selection material, implicit all-levers-off row first, size accounting
    included, recipe variants re-embedded before their fold's evaluation
    exactly as in ``run_sweep``); ``reports`` (the full seam report per
    combo — per-query values a consumer needs to pair against without
    re-running the rotation); and ``baseline`` (the incumbent combo's
    per-query selection-metric map, same shape as ``run_sweep``'s).
    Per-combo embedding state holds across the WHOLE rotation: the
    :class:`_EmbeddingStateMachine` (and its baseline snapshot) outlives
    the fold loop, so a fold's first non-variant combo measures under the
    session baseline even when the previous fold's last variant combo
    re-embedded — the leftover recipe of fold i never leaks into fold
    i+1's integrity row (the fold-invariance premise the pooled aggregate
    below is written on).

    ``aggregate`` (T003, D-009) is the rotation verdict per
    candidate-vs-baseline combo. ``pooled`` is the per-query paired array:
    every query EXACTLY ONCE across the rotation, attributed to the fold
    that held it out (pool order = held-out fold, then id). Because the
    seam scores each query independently of the fold's selection set
    (retrieval sees the embedding state, the query, and the combo params —
    never the other ids) AND every combo measures under its own declared
    embedding state in every fold (the per-combo state guarantee above),
    a query's per-query values are identical in every fold whose selection
    material includes it, so the pool reconstructs one full-set paired
    array with ``n = all queries``; each query's candidate and baseline
    values are read from the earliest fold whose selection material
    includes it (the SAME fold for both, so the pair shares one embedding
    state). ``bootstrap`` is the unchanged
    :func:`paired_bootstrap` verdict over those pooled arrays — the
    significance basis (``significance_basis`` names it). ``descriptive``
    (the rotation-mean of the per-fold selection metrics, the per-fold
    figures, and the min/max delta spread) is DESCRIPTIVE ONLY and never
    feeds the significance test — Bengio–Grandvalet: no unbiased k-fold
    variance estimator exists from fold scores, so fold figures describe
    how much folds disagree and nothing more.

    ``ValueError`` for an unknown metric, malformed or duplicate-named
    combos, or a ``baseline`` naming no combo; ``RuntimeError`` when a
    variant combo runs against an ``embed_all`` without the ``variant``
    contract (both identical to ``run_sweep``); :class:`HeldOutError`
    propagates from the seam when a request touches any fold's held-out
    ids — nothing is returned in that case (no results table emitted).
    """
    if metric not in _METRICS:
        raise ValueError(
            f"unknown metric {metric!r}; expected one of {list(_METRICS)}"
        )

    # Materialize once: the partition and every seam call iterate queries.
    query_list = list(queries)
    normalized = _normalize_combos(combos)

    # The integrity row per fold, identical rule to run_sweep: prepended
    # whenever the grid carries no params-None variant-less combo of its
    # own (a variant combo with params=None re-embeds, so it cannot serve).
    if not any(
        params is None and variant is None for _name, params, variant in normalized
    ):
        normalized.insert(0, (ALL_LEVERS_OFF, None, None))

    names = {name for name, _params, _variant in normalized}
    if baseline is not None and baseline not in names:
        raise ValueError(
            f"baseline {baseline!r} names no combo in the sweep "
            f"(combos: {sorted(names)})"
        )

    all_ids = sorted(
        {item if isinstance(item, str) else item.query_id for item in query_list}
    )
    # The rotation and its loud refusals (k < MIN_KFOLD_K, fewer ids than
    # folds) come from kfold_partitions as-is.
    folds = kfold_partitions(all_ids, k=k_folds, seed=fold_seed)
    requested = None if ids is None else sorted(set(ids))

    # Loop-invariant incumbent (run_sweep's selection rule), computed once:
    # both the per-fold ``baseline`` maps and the ``aggregate`` pair against
    # it.
    baseline_name = baseline if baseline is not None else next(
        name
        for name, params, variant in normalized
        if params is None and variant is None
    )

    # Per-combo embedding state across the WHOLE rotation (the fold loop's
    # invariant): built once here, the machine's baseline snapshot and its
    # knowledge of the installed recipe survive the fold boundary, so fold
    # i+1's first non-variant combo is restored to the session baseline
    # instead of inheriting fold i's last variant state.
    embed_state = _EmbeddingStateMachine(conn)

    fold_entries: List[Dict[str, Any]] = []
    for fold_index, held_out in enumerate(folds):
        held_set = set(held_out)
        # Selection material: every id except this fold's. An explicit
        # request is handed to the seam verbatim — the guard's channel.
        selection_ids: List[str] = (
            [qid for qid in all_ids if qid not in held_set]
            if requested is None
            else requested
        )
        reports: Dict[str, Dict[str, Any]] = {}
        rows: List[Dict[str, Any]] = []
        for name, params, variant in normalized:
            # Same per-combo rule as run_sweep: variant combos re-embed
            # first; non-variant combos measure under the session baseline,
            # restored when a variant combo (this fold's or the previous
            # fold's) left another state installed.
            embed_state.install(variant)
            size = _size_accounting(conn)
            report = evaluate_on(
                conn,
                query_list,
                ids=selection_ids,
                purpose="selection",
                held_out_ids=held_out,
                metric=metric,
                bundle_root=bundle_root,
                k=k,
                params=params,
                timer=timer,
            )
            reports[name] = report
            durations = sorted(report["durations_ms"].values())
            row: Dict[str, Any] = {
                "combo": name,
                "recall_at_10": report["recall_at_10"],
                "mrr": report["mrr"],
                "p95_ms": round(_percentile(durations, 95.0), 4),
                "n_queries": report["n_queries"],
                "db_mb": size["db_mb"],
                "chunk_chars_max": size["chunk_chars_max"],
                "chunk_chars_mean": size["chunk_chars_mean"],
            }
            if variant is not None:
                row["variant"] = variant
            rows.append(row)

        base_report = reports[baseline_name]
        fold_entries.append(
            {
                "fold": fold_index,
                "held_out_ids": sorted(held_set),
                "rows": rows,
                "reports": reports,
                "baseline": {
                    "combo": baseline_name,
                    "metric": metric,
                    "per_query": {
                        qid: base_report["per_query"][qid][metric]
                        for qid in sorted(base_report["per_query"])
                    },
                },
            }
        )

    # --- The fold aggregate (T003, D-009): pooled per-query bootstrap. ----
    # Every query enters the pool EXACTLY ONCE, attributed to the fold that
    # held it out (pool order: held-out fold, then id). The seam scores each
    # query independently of the fold's selection set, so a query's values
    # are identical in every fold whose selection material includes it —
    # the pool reconstructs one full-set paired array (n = all queries), the
    # legitimate paired_bootstrap basis. Fold means never enter the
    # significance path (Bengio–Grandvalet: no unbiased k-fold variance
    # estimator exists from fold scores).
    owner_fold = {
        qid: fold_index for fold_index, held_out in enumerate(folds) for qid in held_out
    }
    first_held = set(folds[0])
    # The earliest fold whose selection material includes the query: fold 0,
    # or fold 1 for fold 0's own members (k >= 5 guarantees fold 1 exists and
    # does not hold them out). Candidate AND baseline values for a query are
    # read from that same fold, so the pair shares one embedding state.
    source_fold = {qid: (1 if qid in first_held else 0) for qid in all_ids}
    pool_order = sorted(all_ids, key=lambda qid: (owner_fold[qid], qid))
    rows_by_combo = [{row["combo"]: row for row in entry["rows"]} for entry in fold_entries]

    def _pooled(combo: str) -> List[float]:
        """Pooled per-query selection-metric array for ``combo``.

        Each query read exactly once, from its source fold's seam report —
        the per-query measurements themselves, never fold aggregates.
        """
        return [
            fold_entries[source_fold[qid]]["reports"][combo]["per_query"][qid][metric]
            for qid in pool_order
        ]

    combo_aggregates: Dict[str, Dict[str, Any]] = {}
    for cand, _params, _variant in normalized:
        if cand == baseline_name:
            continue  # the baseline is the pairing anchor, not a candidate
        pooled_candidate = _pooled(cand)
        pooled_baseline = _pooled(baseline_name)
        per_fold_candidate = [
            rows_by_combo[fold_index][cand][metric]
            for fold_index in range(len(fold_entries))
        ]
        per_fold_baseline = [
            rows_by_combo[fold_index][baseline_name][metric]
            for fold_index in range(len(fold_entries))
        ]
        per_fold_delta = [
            round(c - b, 4) for c, b in zip(per_fold_candidate, per_fold_baseline)
        ]
        combo_aggregates[cand] = {
            "pooled": {
                "n_queries": len(pool_order),
                "queries": pool_order,
                "candidate": pooled_candidate,
                "baseline": pooled_baseline,
            },
            # The significance basis (D-009): the unchanged single-split
            # bootstrap over the POOLED per-query arrays — never fold means.
            "bootstrap": paired_bootstrap(pooled_candidate, pooled_baseline),
            # DESCRIPTIVE ONLY (D-009): rotation statistics never feed the
            # significance test.
            "descriptive": {
                "rotation_mean": {
                    "candidate": round(
                        sum(per_fold_candidate) / len(per_fold_candidate), 4
                    ),
                    "baseline": round(
                        sum(per_fold_baseline) / len(per_fold_baseline), 4
                    ),
                    "delta": round(sum(per_fold_delta) / len(per_fold_delta), 4),
                },
                "per_fold": {
                    "candidate": per_fold_candidate,
                    "baseline": per_fold_baseline,
                    "delta": per_fold_delta,
                },
                "spread": {
                    "delta_min": min(per_fold_delta),
                    "delta_max": max(per_fold_delta),
                },
            },
        }

    return {
        "schema": KFOLD_SWEEP_SCHEMA,
        "dataset": {
            "name": dataset_name,
            "version": dataset_version,
            "fold_seed": fold_seed,
            "k_folds": len(folds),
            "split": "kfold",
            "metric": metric,
            "n_queries": len(all_ids),
        },
        "folds": fold_entries,
        "aggregate": {
            "metric": metric,
            "baseline": baseline_name,
            "significance_basis": "pooled_per_query_paired_bootstrap",
            "combos": combo_aggregates,
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
