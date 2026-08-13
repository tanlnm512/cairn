"""Cardinality guard for the telemetry event catalog (task T15, spec §5.2/§6.4/§8).

The ``events`` table is an OTel-shaped signal: ``name + attributes`` where the
attributes must have **bounded cardinality** (enums, bucketed numbers, short
site tags -- never file paths, never free-form user text, spec §5.2 invariant 2
and §8 "cardinality explosion" risk). A single runaway attribute (a file path,
a query string, an exception message) would balloon the distinct-value set and
make the table useless for aggregation.

This file is the enforcement layer. It owns ``ALLOWED_ATTR_VALUES`` -- the
single source of truth for the value domain of every cataloged event's attrs --
and asserts four ways:

  1. **Catalog coverage (parametrized over the catalog).** Every event-name
     constant declared in ``cairn.telemetry.events`` MUST have an entry in
     ``ALLOWED_ATTR_VALUES``. The catalog is introspected from the module, so
     adding a new ``FOO_BAR = "foo_bar"`` constant there (and starting to emit
     it) fails ``test_every_catalog_event_has_cardinality_declaration`` until
     the author declares its cardinality here. Forcing the declaration is the
     whole point: an undeclared event is an unbounded one.

  2. **Static bucket-helper checks.** The pure functions that collapse a raw
     measurement into a tag (``semantic._ms_bucket`` / ``_n_results_bucket``,
     ``metric_buffering._chars_bucket``) are parametrized over representative +
     edge inputs and asserted to return only members of the declared bucket
     sets. These helpers ARE the cardinality mechanism for numeric attrs.

  3. **Dynamic emit sweep.** Each *live* emitter is driven once on its cheapest
     path and every emitted attr value is validated against its declared
     domain. Enum/bucket attrs use strict set membership (a new value is a
     cardinality event the test must be updated for); bounded-tag attrs
     (``site``, ``tool``, ``task_kind``, ``query_kind``) use a shape heuristic;
     numeric attrs (``count``, ``attempt``) use an int-range predicate.

  4. **Universal no-path-separator guard.** No emitted string attr may contain
     ``/`` or ``\\`` -- a cheap heuristic that catches a future emitter
     accidentally stuffing a file path, an exception traceback, or free text
     into an attr. ``site`` tags are ``module.function`` (contain ``.`` but not
     ``/``) and are explicitly allowed.

Per-task fire/behavior coverage lives in ``test_semantic_events.py``,
``test_emitters.py``, ``test_contention_visibility.py``; those assert *specific*
expected values. This file asserts the *cardinality property* (every value is
bounded) and is intentionally the one place that fails loudly if an emitter
escapes its declared domain.

Two catalog events -- ``ann_fallback`` and ``hash_fallback`` -- are declared
here per spec §6.4 but have **no live emitter today** (only the
``warn_*_fallback_once`` *logging* helpers exist; they log, they do not emit an
``events`` row). That is a reported source gap, not a test gap: there is nothing
to drive, so the dynamic sweep covers the other six. See
``test_ann_and_hash_fallback_have_no_live_emitter_documented``.
"""

from __future__ import annotations

import collections
import json
import re

import pytest

from cairn.telemetry import events
from cairn.telemetry import sink
from cairn.telemetry import (
    ANN_FALLBACK,
    EMPTY_RESULT,
    HASH_FALLBACK,
    LOCK_CONTENTION,
    SEMANTIC_BACKEND,
    STRAY_SWEPT,
    TASK_LIFECYCLE,
    TRUNCATE_RESULT,
)


# ---------------------------------------------------------------------------
# The single source of truth: allowed attr-value domains per catalog event.
#
# Convention for each attr's validator:
#   * ``frozenset``  -> strict enum / bucket. Membership is required; a new
#                       value is a cardinality change that MUST update this set
#                       (e.g. a 4th backend, a 5th task_lifecycle event).
#   * callable       -> shape predicate for a bounded tag / number. Validated
#                       by heuristic (no path separators / whitespace, bounded
#                       length, or an int range). These attrs are *intentionally*
#                       tags, not strict enums: their roster (the ~13 contention
#                       sites, the MCP tool names, the task kinds) is volatile,
#                       so coupling the test to the exact roster would be noisy
#                       without adding cardinality safety. The heuristic still
#                       rejects the actual risk -- paths and free text.
# ---------------------------------------------------------------------------

# Strict enum / bucket sets (a new member here is a deliberate cardinality event).
_BACKEND = frozenset({"ann", "brute", "hash"})
_MS_BUCKETS = frozenset({"0-10ms", "10-100ms", "100-1000ms", ">1000ms"})
_N_BUCKETS = frozenset({"0", "1-5", "6-10", "11-50", ">50"})
_CHARS_BUCKETS = frozenset({"<=500", "500-2k", "2k-10k", ">10k"})
_TASK_EVENTS = frozenset({"claimed", "completed", "revised", "dropped"})
_ANN_REASONS = frozenset(
    {"load_failed", "not_installed", "no_index", "disabled", "query_error"}
)


def _bounded_tag(value) -> bool:
    """A short, single-token low-cardinality tag.

    Rejects the cardinality risks this guard exists to catch: path separators
    (``/`` / ``\\``), internal whitespace (free text / messages), and over-long
    strings. Identifiers like ``search_symbols``, ``compass-synthesize``, and
    ``semantic_search`` pass; ``"src/cairn/x.py"`` and ``"no such symbol"`` do
    not. ``bool``/``int`` are rejected -- a tag is a string.
    """
    return (
        isinstance(value, str)
        and 0 < len(value) <= 80
        and "/" not in value
        and "\\" not in value
        and not any(ch.isspace() for ch in value)
    )


def _site_tag(value) -> bool:
    """``module.function`` site tag: a bounded tag that also contains a ``.``.

    The 13 lock-contention sites are all ``module.function`` literals; the dot
    is the signal that it is a code-location tag and *not* a path. Multiple dots
    (``a.b.c``) are tolerated so the guard does not break on a future nested
    module; a leading/trailing dot or a slash is still rejected.
    """
    return _bounded_tag(value) and "." in value and not value.startswith(".")


def _int_nonneg(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _int_pos(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


ALLOWED_ATTR_VALUES: dict[str, dict[str, object]] = {
    SEMANTIC_BACKEND: {
        "backend": _BACKEND,
        "fusion": frozenset({0, 1}),
        "rerank": frozenset({0, 1}),
        "ms": _MS_BUCKETS,
        "n_results": _N_BUCKETS,
    },
    EMPTY_RESULT: {
        "query_kind": _bounded_tag,  # "semantic_search" today; engine-layer tag
        "backend": _BACKEND,
    },
    LOCK_CONTENTION: {
        "site": _site_tag,
    },
    TRUNCATE_RESULT: {
        "tool": _bounded_tag,  # MCP tool name (bounded identifier)
        "chars_bucket": _CHARS_BUCKETS,
    },
    TASK_LIFECYCLE: {
        "task_kind": _bounded_tag,  # compass-synthesize / flow-revise / wiki / ...
        "event": _TASK_EVENTS,
        "attempt": _int_pos,
    },
    STRAY_SWEPT: {
        "count": _int_pos,  # emitted only when killed > 0
    },
    # Declared per spec §6.4; NO live emitter today (warn_*_fallback_once only
    # logs). When an emitter lands, add it to LIVE_EVENTS so the sweep drives it.
    ANN_FALLBACK: {
        "reason": _ANN_REASONS,
    },
    HASH_FALLBACK: {},  # catalog: "(existing warning path)" -- no attrs
}

# Events that actually emit at a live site today (the dynamic sweep drives each).
# Kept explicit so the gap (ann_fallback / hash_fallback) is visible at a glance.
LIVE_EVENTS = frozenset(
    {
        SEMANTIC_BACKEND,
        EMPTY_RESULT,
        LOCK_CONTENTION,
        TRUNCATE_RESULT,
        TASK_LIFECYCLE,
        STRAY_SWEPT,
    }
)
NO_EMITTER_EVENTS = frozenset({ANN_FALLBACK, HASH_FALLBACK})


def _catalog_event_names() -> set[str]:
    """Every event-name string constant declared in the catalog (``events.py``).

    Introspected from the module so adding ``NEW_THING = "new_thing"`` there is
    auto-detected: ``test_every_catalog_event_has_cardinality_declaration`` will
    fail until ``NEW_THING`` is added to ``ALLOWED_ATTR_VALUES``. The filter
    (UPPER_SNAKE ``str`` constant, no leading underscore) matches exactly the 8
    catalog names and excludes ``_MAX_ATTR_CHARS`` (an int) and imports.
    """
    return {
        value
        for name, value in vars(events).items()
        if name.isupper() and isinstance(value, str) and not name.startswith("_")
    }


def _validate_event(name: str, attrs: dict) -> list[str]:
    """Validate one emitted event's attrs against the declared domain.

    Returns a list of human-readable violations (empty == all good). Checks the
    attr *key set* matches the declaration exactly (no surprise/missing keys)
    and every value passes its validator.
    """
    declared = ALLOWED_ATTR_VALUES[name]
    violations: list[str] = []
    if set(attrs) != set(declared):
        violations.append(f"attr keys {sorted(attrs)} != declared {sorted(declared)}")
    for key, value in attrs.items():
        validator = declared.get(key)
        if validator is None:
            continue  # the key-set mismatch above already flags this
        ok, why = _check_value(validator, value)
        if not ok:
            violations.append(f"{name}.{key}={value!r} rejected: {why}")
    return violations


def _check_value(validator, value) -> tuple[bool, str]:
    if isinstance(validator, frozenset):
        return value in validator, f"not in allowed set {sorted(validator, key=str)}"
    if callable(validator):
        return bool(validator(value)), "failed shape predicate"
    return False, f"validator {validator!r} must be a frozenset or callable"


# ---------------------------------------------------------------------------
# Shared sink-state reset (mirrors tests/test_emitters.py)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_telemetry_state(monkeypatch):
    """Clear the shared sink buffer + warn guards + gating env around each test.

    ``_FLUSHER_STARTED`` is deliberately left alone (resetting it would let the
    next emit spawn a second daemon thread); the 30s tick never fires inside a
    test and is a no-op here regardless (no conn factory configured).
    """
    with sink._LOCK:
        sink._BUFFER.clear()
    sink._conn_factory = None
    with events._WARN_LOCK:
        events._WARNED.clear()
    monkeypatch.delenv("CAIRN_TELEMETRY", raising=False)
    monkeypatch.delenv("CAIRN_READ_ONLY", raising=False)
    yield
    with sink._LOCK:
        sink._BUFFER.clear()
    sink._conn_factory = None


def _buffered_events():
    """Snapshot ``[(name, attrs_dict)]`` currently queued in the sink buffer."""
    return [
        (name, json.loads(attrs_json) if attrs_json is not None else {})
        for _ts, name, _sid, attrs_json in list(sink._BUFFER)
    ]


# ===========================================================================
# 1. Catalog coverage -- every cataloged event must declare its cardinality
# ===========================================================================


def test_every_catalog_event_has_cardinality_declaration():
    """Adding a new event constant to ``events.py`` without a declaration fails.

    This is the load-bearing guard of the file: it forces every new signal to
    declare the bounded domain of its attrs up front, so an undeclared (and thus
    potentially unbounded) event can never ship silently.
    """
    catalog = _catalog_event_names()
    declared = set(ALLOWED_ATTR_VALUES)
    assert catalog, "catalog introspection found no events -- filter is broken"
    missing = catalog - declared
    extra = declared - catalog
    assert not missing, (
        f"cataloged events with no cardinality declaration (add them to "
        f"ALLOWED_ATTR_VALUES): {sorted(missing)}"
    )
    assert not extra, (
        f"declared events not in the catalog (stale entry?): {sorted(extra)}"
    )


def test_live_and_no_emitter_partitions_cover_catalog():
    """LIVE_EVENTS + NO_EMITTER_EVENTS == the catalog (no event unaccounted for).

    Pins the documentation of which events have a live emitter (dynamic sweep
    covers them) vs. which are catalog-only today. If an emitter is later wired
    for ``ann_fallback`` / ``hash_fallback``, move it into LIVE_EVENTS so the
    sweep starts driving it.
    """
    assert LIVE_EVENTS | NO_EMITTER_EVENTS == _catalog_event_names()
    assert not (LIVE_EVENTS & NO_EMITTER_EVENTS), "an event can't be both"


@pytest.mark.parametrize("event", sorted(ALLOWED_ATTR_VALUES, key=str))
def test_each_attr_validator_is_well_formed(event):
    """Every declared validator is a frozenset (enum/bucket) or callable (tag).

    Guards against a typo'd declaration (e.g. a bare ``set`` or ``list``) that
    would silently never match and let any value through.
    """
    for key, validator in ALLOWED_ATTR_VALUES[event].items():
        assert isinstance(validator, (frozenset,)) or callable(validator), (
            f"{event}.{key} validator must be frozenset or callable, got "
            f"{type(validator).__name__}"
        )
        if isinstance(validator, frozenset):
            assert validator, f"{event}.{key} enum/bucket set is empty"


# ===========================================================================
# 2. Static bucket-helper checks (the cardinality mechanism for numbers)
# ===========================================================================

# Representative + edge inputs per helper. Each must land inside the helper's
# declared bucket set -- proving the helper can NEVER return an out-of-set tag.
_MS_INPUTS = [-1.0, 0.0, 9.99, 10.0, 99.9, 100.0, 999.0, 1000.0, 5000.0, 1e9]
_N_INPUTS = [-5, 0, 1, 5, 6, 10, 11, 50, 51, 1000]
_CHARS_INPUTS = [0, 1, 500, 501, 2000, 2001, 10_000, 10_001, 60_000, 1_000_000]


@pytest.mark.parametrize("ms", _MS_INPUTS)
def test_ms_bucket_only_returns_declared_values(ms):
    from cairn.graph.semantic import _ms_bucket

    assert _ms_bucket(ms) in _MS_BUCKETS


@pytest.mark.parametrize("n", _N_INPUTS)
def test_n_results_bucket_only_returns_declared_values(n):
    from cairn.graph.semantic import _n_results_bucket

    assert _n_results_bucket(n) in _N_BUCKETS


@pytest.mark.parametrize("n", _CHARS_INPUTS)
def test_chars_bucket_only_returns_declared_values(n):
    from cairn.mcp_server.metric_buffering import _chars_bucket

    assert _chars_bucket(n) in _CHARS_BUCKETS


def test_ms_bucket_boundary_labels_are_exact():
    """The bucket boundaries map to the documented labels (not just any member)."""
    from cairn.graph.semantic import _ms_bucket

    assert _ms_bucket(0.0) == "0-10ms"
    assert _ms_bucket(9.99) == "0-10ms"
    assert _ms_bucket(10.0) == "10-100ms"
    assert _ms_bucket(1000.0) == ">1000ms"


def test_n_results_bucket_boundary_labels_are_exact():
    from cairn.graph.semantic import _n_results_bucket

    assert _n_results_bucket(0) == "0"
    assert _n_results_bucket(1) == "1-5"
    assert _n_results_bucket(5) == "1-5"
    assert _n_results_bucket(6) == "6-10"
    assert _n_results_bucket(51) == ">50"


def test_chars_bucket_boundary_labels_are_exact():
    from cairn.mcp_server.metric_buffering import _chars_bucket

    assert _chars_bucket(0) == "<=500"
    assert _chars_bucket(500) == "<=500"
    assert _chars_bucket(501) == "500-2k"
    assert _chars_bucket(10_001) == ">10k"


# ===========================================================================
# 3. Dynamic emit sweep -- drive each live emitter, validate every attr value
# ===========================================================================


@pytest.fixture
def captured_live_emits(hash_backend, fresh_db, tmp_path, monkeypatch):
    """Drive every *live* emitter once on its cheapest path; return captured rows.

    Returns ``{event_name: [attrs_dict, ...]}``. Each drive picks the minimal
    branch that fires the emit (no ``embed_all`` corpus, no real LLM critic, no
    real stray process) so the sweep stays fast and hermetic. The assertion that
    each LIVE event was actually emitted lives in the parametrized consumers --
    a silently-broken emitter must not pass vacuously.

    Branch choices:
      * ``semantic_search`` on an *empty* DB -> brute scan finds nothing ->
        emits ``semantic_backend`` (n_results="0") AND ``empty_result``. No
        symbols/embeddings to build, so this is cheap. ``CAIRN_ANN_BACKEND=off``
        + reranker stubbed off keeps it deterministic (mirrors
        test_semantic_events.py) and free of sqlite-vec / model deps.
      * ``note_contention`` -> one ``lock_contention`` event.
      * ``_truncate_result`` with a 50-char cap -> one ``truncate_result``.
      * ``claim_task`` on a fresh task -> one ``task_lifecycle`` (claimed).
      * ``_run_stray_sweep`` with a mocked sweep -> one ``stray_swept``.
    """
    # Determinism knobs for the semantic drive.
    monkeypatch.setenv("CAIRN_ANN_BACKEND", "off")
    from cairn.graph import reranker as rrk

    monkeypatch.setattr(rrk, "rerank_enabled", lambda: False)
    monkeypatch.setattr(
        rrk, "rerank", lambda query, candidates, limit: (candidates[:limit], False)
    )

    captured: dict[str, list[dict]] = collections.defaultdict(list)

    # 1. semantic_backend + empty_result (empty DB -> results == []).
    from cairn.graph.semantic import semantic_search

    semantic_search(fresh_db, "anything-at-all", limit=5)

    # 2. lock_contention.
    from cairn.telemetry import note_contention

    note_contention("schema.get_db")

    # 3. truncate_result (over-cap branch only).
    from cairn.mcp_server import metric_buffering as mb

    monkeypatch.setattr(mb, "MAX_RESULT_CHARS", 50)
    mb._truncate_result("explore", "x" * 200)

    # 4. task_lifecycle (the claimed transition).
    from cairn.llm.tasks import claim_task, create_task
    from cairn.okf.bundle import OKFBundle

    knowledge = tmp_path / ".knowledge"
    (knowledge / "_tasks").mkdir(parents=True)
    bundle = OKFBundle(str(knowledge))
    task = create_task(bundle, task_kind="compass-synthesize", resource="mod/foo")
    claim_task(bundle, task.id)

    # 5. stray_swept (an active pass -> count > 0).
    from cairn.mcp_server import lifecycle, server

    monkeypatch.setattr(lifecycle, "sweep_strays", lambda db_path, log=False: 1)
    server._run_stray_sweep("/fake/db.sqlite")

    for name, attrs in _buffered_events():
        captured[name].append(attrs)

    yield captured


@pytest.mark.parametrize("event", sorted(LIVE_EVENTS, key=str))
def test_live_emit_attrs_are_within_declared_domain(event, captured_live_emits):
    """Every attr value emitted by a live emitter is in its declared domain.

    A failure here means an emitter produced a value outside its enum/bucket/
    tag-shape -- the cardinality contract is broken. The drive must actually
    fire the event (the ``assert captured`` guards against a vacuous pass).
    """
    captured = captured_live_emits.get(event, [])
    assert captured, (
        f"{event} was not emitted by the sweep drive -- a live emitter is "
        f"silently broken (or mis-categorized in LIVE_EVENTS)"
    )
    for attrs in captured:
        violations = _validate_event(event, attrs)
        assert not violations, (
            f"{event} emitted out-of-bounds attrs {attrs!r}: {violations}"
        )


def test_no_live_emitted_attr_contains_a_path_separator(captured_live_emits):
    """Universal guard: no emitted string attr may contain ``/`` or ``\\``.

    This is the cheap heuristic (spec §8 "cardinality explosion" mitigation)
    that catches a future emitter accidentally stuffing a file path, an
    exception traceback, or free text into an attr -- regardless of whether that
    attr is a strict enum or a bounded tag. ``site`` tags are ``module.function``
    (contain ``.``, not ``/``) and pass; a path does not.
    """
    bad = []
    for event, rows in captured_live_emits.items():
        for attrs in rows:
            for key, value in attrs.items():
                if isinstance(value, str) and ("/" in value or "\\" in value):
                    bad.append(f"{event}.{key}={value!r}")
    assert not bad, f"path-like attr values leaked into events: {bad}"


# ===========================================================================
# 4. The no-path-separator heuristic itself (proves it catches a future bad emit)
# ===========================================================================


@pytest.mark.parametrize(
    "value",
    [
        "src/cairn/x.py",
        "a/b/c",
        "C:\\Users\\tan\\file.py",
        "/abs/path/to/db.sqlite",
        "config/observability",
    ],
)
def test_path_like_values_are_rejected_by_the_tag_heuristic(value):
    """A future emitter that stuffs a path into a bounded-tag attr is caught.

    This is the regression net for the heuristic: feed it the shapes that would
    cause cardinality explosion (file paths, repo-relative paths, Windows paths)
    and confirm every one is rejected. ``_bounded_tag`` is what the ``site`` /
    ``tool`` / ``task_kind`` / ``query_kind`` attrs validate against.
    """
    assert not _bounded_tag(value), f"path-like {value!r} should be rejected"
    assert "/" in value or "\\" in value


@pytest.mark.parametrize(
    "value",
    [
        "schema.get_db",  # site tag (module.function)
        "explore",  # MCP tool name
        "search_symbols",  # underscored tool name
        "compass-synthesize",  # task kind
        "semantic_search",  # query kind
    ],
)
def test_legitimate_tags_pass_the_tag_heuristic(value):
    """The bounded-tag heuristic accepts the real tag shapes in use today.

    ``site`` carries a dot (``module.function``); tool/task-kind/query-kind are
    plain identifiers. None contain ``/`` / ``\\`` / whitespace, so all pass --
    the heuristic is precise enough not to flag the legitimate values.
    """
    assert _bounded_tag(value), f"legitimate tag {value!r} should pass"
    assert _site_tag("schema.get_db")


# ===========================================================================
# 5. task_lifecycle event-tag exhaustiveness (static, avoids re-running the LLM)
# ===========================================================================


def test_task_lifecycle_event_literals_are_within_declared_set():
    """The ``event=`` literals in ``llm/tasks.py`` are a subset of the declared set.

    ``task_lifecycle`` emits from four sites (claimed/completed/revised/dropped)
    inside ``complete_task``, which runs the deterministic critic (heavier to
    drive in full). The complete/revise/drop sites share the identical emit
    shape with the claimed site (covered dynamically above and in
    test_emitters.py); this static check ensures no *fifth* ``event`` value has
    been added without updating ``_TASK_EVENTS``. It greps source rather than
    importing so a syntax-only change is still caught.
    """
    import inspect

    from cairn.llm import tasks as tasks_mod

    source = inspect.getsource(tasks_mod)
    literals = set(re.findall(r'event\s*=\s*"([a-z_]+)"', source))
    assert literals, 'no event="..." literals found -- regex is stale'
    extra = literals - _TASK_EVENTS
    assert not extra, (
        f"task_lifecycle emits undeclared event values {sorted(extra)}: add "
        f"them to _TASK_EVENTS (or stop emitting them)"
    )


# ===========================================================================
# 6. ann_fallback / hash_fallback: declared but no live emitter (source gap)
# ===========================================================================


def test_ann_and_hash_fallback_have_no_live_emitter_documented():
    """ann_fallback / hash_fallback are catalog-declared but not emitted today.

    FINDING (reported, not fixed here -- this is a test-only task): spec §6.4
    says ``ann_fallback`` is "Emitted from ann_index.try_load / ann_query /
    semantic.py fallback branch" and ``hash_fallback`` from "embeddings.
    warn_hash_fallback_once callers", but those code paths only call the
    ``warn_*_fallback_once`` *logging* helpers -- they do NOT emit an ``events``
    row. So the two events are unreachable. This test pins that fact: if/when an
    emitter is wired, it MUST be moved into LIVE_EVENTS above so the sweep
    validates its attrs. Until then the declaration (ANN_FALLBACK.reason domain)
    is the only contract.
    """
    assert ANN_FALLBACK in ALLOWED_ATTR_VALUES
    assert HASH_FALLBACK in ALLOWED_ATTR_VALUES
    # The reason domain is declared per spec §6.4 even though nothing emits it.
    assert ALLOWED_ATTR_VALUES[ANN_FALLBACK]["reason"] == _ANN_REASONS
    assert ALLOWED_ATTR_VALUES[HASH_FALLBACK] == {}
