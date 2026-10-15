"""Centrality ranking for skill generation (FR-002).

Each test builds a real workspace and ranks candidates against its live
graph, once with the transitive closure materialised (closure tier) and
once without (degree tier).

Contract: ``rank_candidates(conn, candidates) -> RankedSymbols`` returns
``symbols`` — the candidate qualified names whose definitions resolve in
the graph, ordered by score desc then qualified name asc (total order) —
and ``tier``: ``closure`` when scores aggregate transitive impact from the
``transitive_edges`` store, ``degree`` when it is absent and direct
in/out degree is used instead. Read-only: no closure or graph rows change.
"""
from __future__ import annotations

from cairn.graph.builder import build_graph
from cairn.graph.schema import get_db
from cairn.skillgen.ranking import RankedSymbols, rank_candidates

HUB_WORKSPACE = (
    "def zeta_hub():\n"
    "    return 1\n"
    "\n"
    "def mid_one():\n"
    "    return zeta_hub()\n"
    "\n"
    "def mid_two():\n"
    "    return zeta_hub()\n"
    "\n"
    "def alpha_leaf():\n"
    "    return 2\n"
)


def _write_hub_workspace(ws):
    (ws / ".git").mkdir(parents=True)
    (ws / "pkg_a").mkdir()
    (ws / "pkg_a" / "core.py").write_text(HUB_WORKSPACE)


def _hub_conn(tmp_path, *, with_closure: bool):
    """Graph for the hub workspace; closure materialised only when asked."""
    _write_hub_workspace(tmp_path / "demo")
    db_path = tmp_path / "graph.db"
    build_graph(workspace=str(tmp_path), db_path=str(db_path), verbose=False)
    conn = get_db(str(db_path))
    if with_closure:
        from cairn.graph.dataflow import build_transitive_closure

        build_transitive_closure(conn)
    return conn


def _multi_hop_conn(tmp_path):
    """Hub workspace plus top_caller -> mid_one -> zeta_hub (distance 2)."""
    _write_hub_workspace(tmp_path / "demo")
    (tmp_path / "demo" / "pkg_a" / "core.py").write_text(
        HUB_WORKSPACE
        + "\n"
        + "def top_caller():\n"
        + "    return mid_one()\n"
    )
    db_path = tmp_path / "graph.db"
    build_graph(workspace=str(tmp_path), db_path=str(db_path), verbose=False)
    conn = get_db(str(db_path))
    from cairn.graph.dataflow import build_transitive_closure

    build_transitive_closure(conn)
    return conn


def test_closure_tier_ranks_hub_above_alphabetically_first_leaf(tmp_path):
    conn = _hub_conn(tmp_path, with_closure=True)
    res = rank_candidates(conn, ["alpha_leaf", "zeta_hub"])
    assert isinstance(res, RankedSymbols)
    assert res.tier == "closure"
    assert res.symbols[0] == "zeta_hub"
    assert res.symbols == ["zeta_hub", "alpha_leaf"]


def test_closure_tier_aggregates_transitive_ancestors_with_name_tiebreak(tmp_path):
    conn = _multi_hop_conn(tmp_path)
    res = rank_candidates(
        conn, ["alpha_leaf", "zeta_hub", "mid_one", "mid_two", "top_caller"]
    )
    assert res.tier == "closure"
    assert res.symbols == [
        "zeta_hub",
        "mid_one",
        "alpha_leaf",
        "mid_two",
        "top_caller",
    ]


def test_degree_tier_used_when_closure_absent(tmp_path):
    conn = _hub_conn(tmp_path, with_closure=False)
    assert conn.execute("SELECT COUNT(*) FROM transitive_edges").fetchone()[0] == 0
    res = rank_candidates(conn, ["alpha_leaf", "zeta_hub", "mid_one", "mid_two"])
    assert res.tier == "degree"
    assert res.symbols == ["zeta_hub", "mid_one", "mid_two", "alpha_leaf"]


def test_candidates_without_graph_definition_are_dropped(tmp_path):
    conn = _hub_conn(tmp_path, with_closure=True)
    res = rank_candidates(conn, ["zeta_hub", "no_such_sym_qq"])
    assert res.symbols == ["zeta_hub"]


def test_empty_candidates_yield_empty_symbols(tmp_path):
    conn = _hub_conn(tmp_path, with_closure=True)
    res = rank_candidates(conn, [])
    assert res.symbols == []


def test_ranking_is_deterministic(tmp_path):
    conn = _hub_conn(tmp_path, with_closure=True)
    candidates = ["alpha_leaf", "zeta_hub", "mid_one", "mid_two"]
    assert rank_candidates(conn, candidates) == rank_candidates(conn, candidates)


def test_ranking_is_read_only(tmp_path):
    conn = _hub_conn(tmp_path, with_closure=True)
    before = conn.total_changes
    rank_candidates(conn, ["alpha_leaf", "zeta_hub"])
    rank_candidates(conn, ["alpha_leaf", "zeta_hub", "mid_one", "mid_two"])
    assert conn.total_changes == before


def _oversized_name_conn(tmp_path):
    """A closure-backed graph where one candidate name owns more symbol rows
    than SQLite's doubled host-parameter limit can hold in one IN () query."""
    from cairn.graph.schema import get_db

    conn = get_db(str(tmp_path / "chunk.db"))
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES ('r1', 'r1', 'ws/r1')"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES ('f1', 'r1', 'm.py', 'python')"
    )
    n = 16_500
    conn.executemany(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind) "
        "VALUES (?, 'f1', 'dup_name', ?, 'function')",
        [(f"s{i}", f"m.dup_name.{i}") for i in range(n)],
    )
    conn.executemany(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind) "
        "VALUES (?, 'f1', ?, ?, 'function')",
        [(f"src{i}", f"caller_{i}", f"m.caller_{i}") for i in range(3)],
    )
    # Three distinct ancestors reach a slice of the same-name seed rows.
    conn.executemany(
        "INSERT INTO transitive_edges (source_id, target_name, target_id, distance) "
        "VALUES (?, 'dup_name', ?, 1)",
        [(f"src{src}", f"s{(src * 7919) % n}") for src in range(3)],
    )
    conn.commit()
    return conn


def test_closure_tier_chunks_same_name_seeds_under_host_parameter_limit(tmp_path):
    """A very common candidate name (thousands of same-name symbol rows) must
    still rank: the seed-id IN () lists are chunked, never one doubled
    unbounded query."""
    conn = _oversized_name_conn(tmp_path)
    res = rank_candidates(conn, ["dup_name"])
    assert res.tier == "closure"
    assert res.symbols == ["dup_name"]
