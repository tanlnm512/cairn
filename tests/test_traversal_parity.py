"""Golden parity tests for graph traversal queries (perf phase P1.1).

Pins the exact user-visible outputs of ``impact_analysis``, ``trace_flow`` and
``get_dataflow`` on a seeded synthetic corpus, so query-path optimizations
(closure-index impact, memoized per-name lookups) can prove they did not
change results. The DFS walk's output order depends on SQL row order, so any
change that reorders rows or visit sequences shows up here as a golden diff.

Determinism: the corpus uses <= 10 files on purpose. Above 10 files
``builder._parse_all`` collects worker futures with ``as_completed``, making
insert order -- hence row order and DFS visit order -- nondeterministic
across processes. At or below 10 files parsing is inline and sequential.

``get_dataflow``'s ``within_repo``/``cross_repo`` lists are built from Python
sets in ``build_dataflow_index``, whose iteration order is hash-randomized
per process; they are sorted at capture time, so the golden pins contents,
not set order. The ``updated`` timestamp is stripped for the same reason.

Regenerate goldens with::

    UPDATE_GOLDENS=1 uv run pytest tests/test_traversal_parity.py
"""
from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from cairn.graph.dataflow import (
    build_dataflow_index,
    build_transitive_closure,
    get_dataflow,
)
from cairn.graph.queries import impact_analysis, trace_flow
from cairn.graph.schema import get_db

GOLDEN_PATH = Path(__file__).parent / "goldens" / "traversal_parity.json"

# (name, max_depth, fuzzy, limit) — wide fan-in, a linear chain, a tight limit
# (truncation), an ambiguous name collision, a cycle, fuzzy mode, and a miss.
IMPACT_QUERIES = [
    ("leaf_util", 3, False, 500),
    ("leaf_util", 2, False, 4),
    ("chain_h1", 3, False, 500),
    ("duplicated", 5, False, 100),
    ("cyc_b", 5, False, 100),
    ("leaf_util", 3, True, 100),
    ("no_such_symbol_xyz", 5, False, 100),
]

# (entry, max_depth, limit, fuzzy)
TRACE_QUERIES = [
    ("entry_north", 6, 500, False),
    ("chain_h3", 4, 100, False),
]

DATAFLOW_QUERIES = ["leaf_util", "mid_a"]


# A handcrafted corpus rather than bench/corpus.py: parity pinning needs
# *resolved* (target_id) edges — precise impact only walks those — and the
# synthetic generator's attribute-chain calls (`sib.Cls().m()`) never resolve.
# Bare unique names + explicit imports do. Shapes covered: fan-in, a 3-hop
# chain, a 2-cycle, a same-name collision (`duplicated` in two files), class
# methods, and an isolated symbol.
PARITY_SOURCES: dict[str, str] = {
    "f01.py": (
        "def leaf_util():\n"
        "    return 1\n"
        "\n"
        "\n"
        "def chain_h1():\n"
        "    return leaf_util()\n"
    ),
    "f02.py": (
        "from f01 import chain_h1, leaf_util\n"
        "\n"
        "\n"
        "def chain_h2():\n"
        "    leaf_util()\n"
        "    return chain_h1()\n"
    ),
    "f03.py": (
        "from f02 import chain_h2\n"
        "\n"
        "\n"
        "def chain_h3():\n"
        "    return chain_h2()\n"
    ),
    "f04.py": (
        "from f01 import leaf_util\n"
        "\n"
        "\n"
        "def mid_a():\n"
        "    return leaf_util()\n"
        "\n"
        "\n"
        "def mid_b():\n"
        "    return leaf_util() * 2\n"
    ),
    "f05.py": (
        "from f01 import leaf_util\n"
        "\n"
        "\n"
        "def mid_c():\n"
        "    return leaf_util() - 1\n"
        "\n"
        "\n"
        "class Widget:\n"
        "    def crunch(self):\n"
        "        return leaf_util()\n"
    ),
    "f06.py": (
        "from f04 import mid_a, mid_b\n"
        "from f05 import mid_c\n"
        "from f07 import duplicated\n"
        "\n"
        "\n"
        "def entry_north():\n"
        "    mid_a()\n"
        "    mid_b()\n"
        "    duplicated()\n"
        "\n"
        "\n"
        "def entry_south():\n"
        "    return mid_c()\n"
    ),
    "f07.py": (
        "from f01 import leaf_util\n"
        "\n"
        "\n"
        "def duplicated():\n"
        "    return leaf_util()\n"
        "\n"
        "\n"
        "def cyc_a():\n"
        "    leaf_util()\n"
        "    return cyc_b()\n"
        "\n"
        "\n"
        "def cyc_b():\n"
        "    return cyc_a()\n"
    ),
    "f08.py": (
        "def duplicated():\n"
        "    return 0\n"
        "\n"
        "\n"
        "def lonely():\n"
        "    pass\n"
    ),
}


@pytest.fixture(scope="module")
def corpus_db(tmp_path_factory):
    """Build the parity corpus graph with derived indexes; return the db path."""
    from cairn.graph.builder import build_graph

    root = tmp_path_factory.mktemp("parity")
    repo = root / "parityrepo"
    repo.mkdir()
    (repo / ".git").mkdir()  # scanner repo marker (see bench/corpus.py)
    for fname, src in PARITY_SOURCES.items():
        (repo / fname).write_text(src, encoding="utf-8")
    db = root / "parity.kg"
    build_graph(workspace=str(repo), db_path=str(db))
    conn = get_db(str(db))
    try:
        build_dataflow_index(conn)
        build_transitive_closure(conn)
    finally:
        conn.close()
    return str(db)


def _canon_impact(res: dict) -> dict:
    """Order-insensitive canonical form of an impact_analysis result.

    DFS visit order follows SQL row order, which follows file-enumeration
    order during the build; directory iteration on APFS is not stable across
    tmp dirs, so order is not golden-pinned. Contents, depths, totals and
    truncation are.
    """
    out = dict(res)
    out["impacted"] = sorted(
        res["impacted"], key=lambda r: (r["depth"], r["symbol"], r["file"], r["repo"])
    )
    out["cycles"] = sorted(res["cycles"], key=lambda c: (c["symbol"], c["depth"]))
    return out


def _canon_trace(res: dict) -> dict:
    """Order-insensitive canonical form of a trace_flow result.

    ``chain``/``leaves``/``modules`` are already sorted by the product code;
    ``branches`` follows walk order, so sort it here.
    """
    out = dict(res)
    out["branches"] = sorted(res["branches"], key=lambda b: b["symbol"])
    for b in out["branches"]:
        b["callees"] = sorted(b["callees"])
    return out


def _capture(conn: sqlite3.Connection) -> dict:
    """Collect the pinned query outputs. Must be deterministic for the corpus."""
    out: dict = {"impact": {}, "trace": {}, "dataflow": {}}

    for name, depth, fuzzy, limit in IMPACT_QUERIES:
        res = impact_analysis(
            conn, name, max_depth=depth, fuzzy=fuzzy, limit=limit,
            use_index=False,
        )
        out["impact"][f"{name}|d{depth}|{'fuzzy' if fuzzy else 'precise'}|l{limit}"] = _canon_impact(res)

    for entry, depth, limit, fuzzy in TRACE_QUERIES:
        res = trace_flow(conn, entry, max_depth=depth, limit=limit, fuzzy=fuzzy)
        out["trace"][f"{entry}|d{depth}|l{limit}"] = _canon_trace(res)

    for sym in DATAFLOW_QUERIES:
        res = get_dataflow(conn, sym)
        if res is not None:
            res = dict(res)
            res.pop("updated", None)  # build timestamp -- not pinned
            res["within_repo"] = sorted(res["within_repo"])
            res["cross_repo"] = sorted(res["cross_repo"])
        out["dataflow"][sym] = res

    return out


def test_traversal_goldens(corpus_db):
    conn = get_db(corpus_db)
    try:
        captured = _capture(conn)
    finally:
        conn.close()

    if os.environ.get("UPDATE_GOLDENS"):
        GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN_PATH.write_text(json.dumps(captured, indent=1, sort_keys=True) + "\n")
        pytest.fail("goldens regenerated -- re-run without UPDATE_GOLDENS")

    assert GOLDEN_PATH.exists(), (
        f"golden file missing: {GOLDEN_PATH} -- regenerate with "
        "UPDATE_GOLDENS=1 uv run pytest tests/test_traversal_parity.py"
    )
    expected = json.loads(GOLDEN_PATH.read_text())
    assert json.loads(json.dumps(captured)) == expected


# --- Index mode (perf phase P1.2/P1.3) --------------------------------------


def _as_tuples(res: dict) -> set:
    return {(r["symbol"], r["file"], r["repo"]) for r in res["impacted"]}


def test_index_mode_covers_dfs_results(corpus_db):
    """Index mode must never lose a DFS-impacted symbol (coverage superset).

    Depths may differ (shortest-path) and extra rows are allowed (unique-name
    hops DFS prunes) -- see dataflow.impact_from_closure's docstring.
    """
    conn = get_db(corpus_db)
    try:
        for name, depth in [("leaf_util", 3), ("chain_h1", 3), ("leaf_util", 2)]:
            dfs = impact_analysis(conn, name, max_depth=depth, use_index=False)
            idx = impact_analysis(conn, name, max_depth=depth, use_index=True)
            assert _as_tuples(dfs) <= _as_tuples(idx), (name, depth)
            assert idx["cycles"] == []
            for r in idx["impacted"]:
                assert 0 <= r["depth"] <= depth
            assert idx["total"] == len(idx["impacted"])
    finally:
        conn.close()


def test_index_mode_depths_are_shortest_paths(corpus_db):
    """chain_h2 -> chain_h1 is the direct caller: depth 0, like DFS."""
    conn = get_db(corpus_db)
    try:
        idx = impact_analysis(conn, "chain_h1", max_depth=3, use_index=True)
        depths = {(r["symbol"], r["file"]): r["depth"] for r in idx["impacted"]}
        assert depths[("chain_h2", "f02.py")] == 0
        assert depths[("chain_h3", "f03.py")] == 1
    finally:
        conn.close()


def test_index_mode_falls_back_on_seed_cycles(corpus_db):
    """A cycle through the seed keeps DFS so ``cycles`` reporting survives."""
    conn = get_db(corpus_db)
    try:
        auto = impact_analysis(conn, "cyc_b", max_depth=5)
        dfs = impact_analysis(conn, "cyc_b", max_depth=5, use_index=False)
        assert _canon_impact(auto) == _canon_impact(dfs)
        assert auto["cycles"]  # cycle actually reported
    finally:
        conn.close()


def test_index_mode_forced_reports_no_cycles(corpus_db):
    """use_index=True forces past the cycle gate: cycles=[] even when DFS
    finds one (the benchmarking/debug escape hatch)."""
    conn = get_db(corpus_db)
    try:
        idx = impact_analysis(conn, "cyc_b", max_depth=3, use_index=True)
        assert idx["cycles"] == []
        assert idx["total"] > 0
    finally:
        conn.close()


def test_index_mode_truncation_is_exact(corpus_db):
    conn = get_db(corpus_db)
    try:
        idx = impact_analysis(conn, "leaf_util", max_depth=3, limit=4, use_index=True)
        assert idx["total"] == 4
        assert idx["truncated"] is True
    finally:
        conn.close()


def test_index_mode_skipped_for_fuzzy_and_deep(corpus_db):
    """Fuzzy and max_depth beyond the closure always take the DFS path."""
    conn = get_db(corpus_db)
    try:
        fuzzy = impact_analysis(conn, "leaf_util", max_depth=3, fuzzy=True)
        assert fuzzy["cycles"] or fuzzy["total"] > 0  # served, but by DFS
        deep = impact_analysis(conn, "leaf_util", max_depth=10)
        assert deep["total"] > 0
    finally:
        conn.close()


# --- DFS query-count memoization (perf phase P2) -----------------------------


class _QueryCountingCursor:
    """Delegating cursor that counts execute() calls (tests only)."""

    def __init__(self, cur, parent):
        self._cur = cur
        self._parent = parent

    def execute(self, sql, params=()):
        self._parent.queries += 1
        return self._cur.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._cur, name)


class _QueryCountingConn:
    """Delegating connection that counts execute() calls (tests only)."""

    def __init__(self, conn):
        self._conn = conn
        self.queries = 0

    def cursor(self):
        return _QueryCountingCursor(self._conn.cursor(), self)

    def execute(self, sql, params=()):
        self.queries += 1
        return self._conn.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_dfs_memoizes_same_name_caller_queries(fresh_db):
    """Per-name memoization: N same-named callers cost one get_callers query.

    Without the memo, the DFS issues one get_callers query per visited symbol
    (20 here); with it, distinct names only -- 3 queries total (seed
    find_definition + get_callers("hub") + get_callers("node")).
    """
    fresh_db.execute(
        "INSERT INTO repos (id, name, path, language) VALUES ('r1', 'r1', '/tmp/r1', 'python')"
    )
    fresh_db.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES ('f1', 'r1', 'a.py', 'python')"
    )
    sym_rows = []
    for i in range(20):
        sym_rows.append((f"n{i}", "f1", "node", f"node_{i}", "function"))
        sym_rows.append((f"h{i}", "f1", "hub", f"hub_{i}", "function"))
    fresh_db.executemany(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES (?,?,?,?,?)",
        sym_rows,
    )
    fresh_db.executemany(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind) VALUES (?,?,?,?,?)",
        [(f"e{i}", f"n{i}", f"h{i}", "hub", "calls") for i in range(20)],
    )
    fresh_db.commit()

    counting = _QueryCountingConn(fresh_db)
    res = impact_analysis(counting, "hub", max_depth=5, use_index=False)
    assert res["total"] == 20
    assert counting.queries == 3
