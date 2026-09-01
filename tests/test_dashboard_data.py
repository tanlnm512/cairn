"""Dashboard view-data assembly: projects, graph scopes, health, memories,
the task queue, and the tool-use history."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
import time
import types

import pytest

from cairn.graph.schema import _apply_schema


def _seed(conn):
    """Three projects whose counts are known by construction.

    alpha: 2 files / 3 symbols / 2 edges, all 3 symbols embedded with
    'all-MiniLM-L6-v2'. beta: 1 file / 2 symbols / 1 edge, 1 of 2 embedded
    with 'hash-embed-v1'. gamma: 1 file / 1 symbol / 0 edges, file rows
    carry no indexed_at (exercises the repos.indexed_at fallback), no
    embeddings.
    """
    conn.executemany(
        "INSERT INTO repos (id, name, path, language, indexed_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("alpha", "alpha", "clients/alpha", "python", "2026-08-18T08:00:00"),
            ("beta", "beta", "clients/beta", "kotlin", "2026-08-19T09:00:00"),
            ("gamma", "gamma", "tools/gamma", "rust", "2026-08-20T07:30:00"),
        ],
    )
    conn.executemany(
        "INSERT INTO files (id, repo_id, path, language, indexed_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("f_a1", "alpha", "src/alpha/core.py", "python", "2026-08-20T10:00:00"),
            ("f_a2", "alpha", "src/alpha/util.py", "python", "2026-08-20T11:00:00"),
            ("f_b1", "beta", "beta/lib/b1.kt", "kotlin", "2026-08-19T09:30:00"),
            ("f_g1", "gamma", "gamma/src/g1.rs", "rust", None),
        ],
    )
    conn.executemany(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES (?, ?, ?, ?, ?)",
        [
            ("s_a1", "f_a1", "alpha_main", "alpha.core.alpha_main", "function"),
            ("s_a2", "f_a1", "alpha_helper", "alpha.core.alpha_helper", "function"),
            ("s_a3", "f_a2", "alpha_util", "alpha.util.alpha_util", "function"),
            ("s_b1", "f_b1", "beta_main", "beta.beta_main", "function"),
            ("s_b2", "f_b1", "beta_aux", "beta.beta_aux", "function"),
            ("s_g1", "f_g1", "gamma_main", "gamma.gamma_main", "function"),
        ],
    )
    conn.executemany(
        "INSERT INTO edges (id, source_id, target_id, kind) VALUES (?, ?, ?, ?)",
        [
            ("e1", "s_a1", "s_a2", "calls"),
            ("e2", "s_a2", "s_a3", "calls"),
            ("e3", "s_b1", "s_b2", "calls"),
        ],
    )
    conn.executemany(
        "INSERT INTO embeddings (symbol_id, model, dim, vec, chunk, embedded_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            ("s_a1", "all-MiniLM-L6-v2", 8, b"", "alpha_main", "2026-08-20T10:05:00"),
            ("s_a2", "all-MiniLM-L6-v2", 8, b"", "alpha_helper", "2026-08-20T10:05:00"),
            ("s_a3", "all-MiniLM-L6-v2", 8, b"", "alpha_util", "2026-08-20T10:06:00"),
            ("s_b1", "hash-embed-v1", 8, b"", "beta_main", "2026-08-19T09:35:00"),
        ],
    )
    conn.commit()


def test_list_projects_counts_and_freshness(fresh_db):
    from cairn.dashboard.data import list_projects

    _seed(fresh_db)
    projects = list_projects(fresh_db)

    assert [p["id"] for p in projects] == ["alpha", "beta", "gamma"]
    by_id = {p["id"]: p for p in projects}

    alpha = by_id["alpha"]
    assert (alpha["file_count"], alpha["symbol_count"], alpha["edge_count"]) == (2, 3, 2)
    # MAX over alpha's two file timestamps, not the earliest.
    assert alpha["last_indexed"] == "2026-08-20T11:00:00"
    assert alpha["path"] == "clients/alpha"  # workspace-relative, verbatim

    beta = by_id["beta"]
    assert (beta["file_count"], beta["symbol_count"], beta["edge_count"]) == (1, 2, 1)
    assert beta["last_indexed"] == "2026-08-19T09:30:00"

    gamma = by_id["gamma"]
    assert (gamma["file_count"], gamma["symbol_count"], gamma["edge_count"]) == (1, 1, 0)
    # No file timestamps recorded: falls back to repos.indexed_at.
    assert gamma["last_indexed"] == "2026-08-20T07:30:00"


def test_list_projects_embedding_status(fresh_db):
    from cairn.dashboard.data import list_projects

    _seed(fresh_db)
    by_id = {p["id"]: p for p in list_projects(fresh_db)}

    assert by_id["alpha"]["embedding_status"] == "embedded"
    assert by_id["alpha"]["embedding_models"] == ["all-MiniLM-L6-v2"]

    assert by_id["beta"]["embedding_status"] == "partial"
    assert by_id["beta"]["embedding_models"] == ["hash-embed-v1"]

    assert by_id["gamma"]["embedding_status"] == "not"
    assert by_id["gamma"]["embedding_models"] == []


def test_list_projects_empty_db_returns_empty_list(fresh_db):
    from cairn.dashboard.data import list_projects

    assert list_projects(fresh_db) == []


def test_get_graph_module_scope_is_the_default(fresh_db):
    from cairn.dashboard.data import get_graph

    _seed(fresh_db)
    graph = get_graph(fresh_db, focus="src/alpha")

    assert graph["metadata"]["scope"] == "module"
    assert graph["metadata"]["node_count"] == 3  # alpha's three symbols
    assert graph["metadata"]["edge_count"] == 2  # e1 + e2, both internal
    assert {n["id"] for n in graph["nodes"]} == {"alpha_main", "alpha_helper", "alpha_util"}
    assert {(e["source"], e["target"]) for e in graph["edges"]} == {
        ("alpha_main", "alpha_helper"),
        ("alpha_helper", "alpha_util"),
    }


def test_get_graph_repo_scope(fresh_db):
    from cairn.dashboard.data import get_graph

    _seed(fresh_db)
    graph = get_graph(fresh_db, scope="repo", repo="alpha")

    assert graph["metadata"]["scope"] == "repo"
    assert graph["metadata"]["repo"] == "alpha"
    assert graph["metadata"]["node_count"] == 2  # one bucket per top-level path
    assert graph["edges"] == []
    labels = {n["id"] for n in graph["nodes"]}
    assert labels == {"src/alpha/core.py (2)", "src/alpha/util.py (1)"}


def test_get_graph_symbol_scope(fresh_db):
    from cairn.dashboard.data import get_graph

    _seed(fresh_db)
    graph = get_graph(fresh_db, scope="symbol", focus="alpha_main", depth=1)

    assert graph["metadata"]["scope"] == "symbol"
    assert graph["metadata"]["node_count"] == 2  # focal + its one callee
    assert graph["metadata"]["edge_count"] == 1
    assert {n["id"] for n in graph["nodes"]} == {"alpha_main", "alpha_helper"}


def test_get_graph_rejects_unknown_scope(fresh_db):
    from cairn.dashboard.data import get_graph

    with pytest.raises(ValueError, match="unknown graph scope"):
        get_graph(fresh_db, scope="galaxy")


# ---------------------------------------------------------------------------
# Symbol-search candidates (graph-nav FR-001/FR-002 / US1): exact-name
# matches with disambiguating context, capped with an honest truncated flag.
# ---------------------------------------------------------------------------


def _seed_dup_name(conn):
    """TC-002's seed: one symbol name defined in two files (two repos, two
    kinds, so each match's context is distinguishable), inserted in the
    reverse of the deterministic result order -- only the query's ORDER BY
    can produce a stable list."""
    conn.executemany(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("s_dup_b", "f_b1", "dup_name", "beta.dup_name", "class"),
            ("s_dup_a", "f_a1", "dup_name", "alpha.dup_name", "function"),
        ],
    )
    conn.commit()


def test_symbol_candidates_exact_unique_name_single_match(fresh_db):
    """TC-001's one-interaction precondition (FR-001): a unique name
    resolves to exactly one candidate carrying its kind, file, and repo."""
    from cairn.dashboard.data import symbol_candidates

    _seed(fresh_db)

    assert symbol_candidates(fresh_db, "alpha_main") == {
        "matches": [
            {
                "name": "alpha_main",
                "kind": "function",
                "file": "src/alpha/core.py",
                "repo_id": "alpha",
            }
        ],
        "truncated": False,
    }


def test_symbol_candidates_ambiguous_name_lists_both_in_file_order(fresh_db):
    """TC-002 (FR-002): a name defined in two files lists both matches with
    their file/kind context instead of an arbitrary pick, in the
    deterministic file-ASC order."""
    from cairn.dashboard.data import symbol_candidates

    _seed(fresh_db)
    _seed_dup_name(fresh_db)

    result = symbol_candidates(fresh_db, "dup_name")

    assert result["truncated"] is False
    assert result["matches"] == [
        {
            "name": "dup_name",
            "kind": "class",
            "file": "beta/lib/b1.kt",
            "repo_id": "beta",
        },
        {
            "name": "dup_name",
            "kind": "function",
            "file": "src/alpha/core.py",
            "repo_id": "alpha",
        },
    ]


def test_symbol_candidates_caps_at_limit_with_honest_truncation(fresh_db):
    """The cap: more same-name symbols than CANDIDATES_LIMIT return exactly
    the cap with truncated True; exactly-at-the-cap is not truncation (the
    limit+1 over-fetch boundary)."""
    from cairn.dashboard.data import CANDIDATES_LIMIT, symbol_candidates

    _seed(fresh_db)
    over = CANDIDATES_LIMIT + 5
    # One file per same-name symbol: file m00..m04 < m05.. so the kept cap
    # is known by construction. Files 0..limit-1 also carry a second name
    # seeded at exactly the cap.
    fresh_db.executemany(
        "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, ?, ?)",
        [
            (f"c_f{i:02d}", "alpha", f"src/caps/m{i:02d}.py", "python")
            for i in range(over)
        ],
    )
    rows = [
        (f"c_s{i:02d}", f"c_f{i:02d}", "capped_name", f"caps.capped.{i}", "function")
        for i in range(over)
    ] + [
        (f"t_s{i:02d}", f"c_f{i:02d}", "at_cap_name", f"caps.at_cap.{i}", "class")
        for i in range(CANDIDATES_LIMIT)
    ]
    fresh_db.executemany(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    fresh_db.commit()

    capped = symbol_candidates(fresh_db, "capped_name")

    assert len(capped["matches"]) == CANDIDATES_LIMIT
    assert capped["truncated"] is True
    # The kept cap is the deterministic head, not an arbitrary slice.
    assert [m["file"] for m in capped["matches"]] == [
        f"src/caps/m{i:02d}.py" for i in range(CANDIDATES_LIMIT)
    ]

    at_cap = symbol_candidates(fresh_db, "at_cap_name")

    assert len(at_cap["matches"]) == CANDIDATES_LIMIT
    assert at_cap["truncated"] is False


def test_symbol_candidates_miss_and_blank_are_empty_never_errors(fresh_db):
    """A name with no matches -- and the blank or whitespace-only names --
    are well-formed empty results, never errors."""
    from cairn.dashboard.data import symbol_candidates

    _seed(fresh_db)
    empty = {"matches": [], "truncated": False}

    assert symbol_candidates(fresh_db, "no_such_symbol") == empty
    for blank in ("", "   "):
        assert symbol_candidates(fresh_db, blank) == empty


# ---------------------------------------------------------------------------
# Symbol-search typeahead suggestions: prefix matches (case-insensitive,
# LIKE wildcards escaped), shortest-name-first, capped with the honest
# truncated flag -- the dropdown's data contract.
# ---------------------------------------------------------------------------


def test_symbol_suggest_prefix_matches_shortest_first_with_context(fresh_db):
    from cairn.dashboard.data import symbol_suggest

    _seed(fresh_db)

    result = symbol_suggest(fresh_db, "alpha")

    assert result["truncated"] is False
    # shortest first: alpha_main/alpha_util (10 chars) before
    # alpha_helper (12), name ASC within a length tie
    assert [m["name"] for m in result["matches"]] == [
        "alpha_main",
        "alpha_util",
        "alpha_helper",
    ]
    first = result["matches"][0]
    assert (first["kind"], first["file"], first["repo_id"]) == (
        "function",
        "src/alpha/core.py",
        "alpha",
    )


def test_symbol_suggest_is_case_insensitive_and_prefix_only(fresh_db):
    from cairn.dashboard.data import symbol_suggest

    _seed(fresh_db)
    empty = {"matches": [], "truncated": False}

    assert [
        m["name"] for m in symbol_suggest(fresh_db, "ALPHA")["matches"]
    ] == ["alpha_main", "alpha_util", "alpha_helper"]
    # a mid-string fragment is not a prefix
    assert symbol_suggest(fresh_db, "lpha") == empty
    for blank in ("", "   "):
        assert symbol_suggest(fresh_db, blank) == empty


def test_symbol_suggest_escapes_like_wildcards_in_the_query(fresh_db):
    """% and _ in the typed text match literally -- the query is data,
    never a pattern (unescaped, 'pct_' would wildcard-match 'pctX')."""
    from cairn.dashboard.data import symbol_suggest

    fresh_db.execute(
        "INSERT INTO files (id, repo_id, path, language) "
        "VALUES ('f_w', 'alpha', 'w.py', 'python')"
    )
    fresh_db.executemany(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("s_w1", "f_w", "pct_100", "w.pct_100", "function"),
            ("s_w2", "f_w", "pctX100", "w.pctX100", "function"),
        ],
    )
    fresh_db.commit()

    # length tie (7 chars each): name ASC puts 'X' (0x58) before '_'
    assert [m["name"] for m in symbol_suggest(fresh_db, "pct")["matches"]] == [
        "pctX100",
        "pct_100",
    ]
    assert [m["name"] for m in symbol_suggest(fresh_db, "pct_1")["matches"]] == [
        "pct_100"
    ]
    assert symbol_suggest(fresh_db, "pct%") == {"matches": [], "truncated": False}


def test_symbol_suggest_caps_at_limit_with_honest_truncation(fresh_db):
    """The cap mirrors the candidates contract: over-cap returns exactly
    the cap with truncated True; exactly-at-cap is not truncation."""
    from cairn.dashboard.data import symbol_suggest

    _seed(fresh_db)

    over = symbol_suggest(fresh_db, "alpha", limit=2)
    assert [m["name"] for m in over["matches"]] == ["alpha_main", "alpha_util"]
    assert over["truncated"] is True

    at_cap = symbol_suggest(fresh_db, "alpha", limit=3)
    assert at_cap["truncated"] is False


# ---------------------------------------------------------------------------
# Node expansion (graph-nav FR-003/FR-005 / US2): the viz-layer neighbors
# query, called directly -- the node/edge shape the dashboard's DataSet
# merge consumes, duplicate-free, with honest counts at the per-direction
# caps.
# ---------------------------------------------------------------------------


def _seed_expand(conn):
    """TC-003's seed: close alpha's chain into a hub -- alpha_util now calls
    alpha_main, so alpha_main has one caller (alpha_util) and one callee
    (alpha_helper) by construction."""
    conn.execute(
        "INSERT INTO edges (id, source_id, target_id, kind) "
        "VALUES ('e_expand', 's_a3', 's_a1', 'calls')"
    )
    conn.commit()


def test_get_symbol_neighbors_returns_the_merge_shape(fresh_db):
    """TC-003's data half (FR-003): one requested name yields exactly that
    symbol plus its caller and callee, with both call edges connected to
    it -- the shape the client's DataSet merge consumes -- and metadata
    counts equal to the returned lists (FR-005)."""
    from cairn.viz.query import get_symbol_neighbors

    _seed(fresh_db)
    _seed_expand(fresh_db)

    result = get_symbol_neighbors(fresh_db, ["alpha_main"])

    assert result["metadata"]["scope"] == "neighbors"
    assert result["metadata"]["requested"] == ["alpha_main"]
    assert {n["id"] for n in result["nodes"]} == {
        "alpha_main",
        "alpha_helper",  # alpha_main's callee (e1)
        "alpha_util",  # alpha_main's caller (e_expand)
    }
    assert {(e["source"], e["target"], e["kind"]) for e in result["edges"]} == {
        ("alpha_main", "alpha_helper", "calls"),
        ("alpha_util", "alpha_main", "calls"),
    }
    assert result["metadata"]["truncated"] is False
    # FR-005: the counts are the returned lists, never a silent overdraw.
    assert result["metadata"]["node_count"] == len(result["nodes"]) == 3
    assert result["metadata"]["edge_count"] == len(result["edges"]) == 2


def _seed_neighbors_dups(conn):
    """TC-004's duplicate half: ``hub`` defined in two files, both rows
    called by the one ``feeder``; plus two distinct names ``pair_a`` /
    ``pair_b`` whose neighborhoods share the one callee ``shared_sink``."""
    conn.executemany(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            ("s_hub_a", "f_a1", "hub", "alpha.hub.a", "function"),
            ("s_hub_b", "f_a2", "hub", "alpha.hub.b", "class"),
            ("s_feeder", "f_b1", "feeder", "beta.feeder", "function"),
            ("s_pair_a", "f_a1", "pair_a", "alpha.pair_a", "function"),
            ("s_pair_b", "f_b1", "pair_b", "beta.pair_b", "function"),
            ("s_sink", "f_a2", "shared_sink", "alpha.shared_sink", "function"),
        ],
    )
    conn.executemany(
        "INSERT INTO edges (id, source_id, target_id, kind) VALUES (?, ?, ?, ?)",
        [
            ("dup_e1", "s_feeder", "s_hub_a", "calls"),
            ("dup_e2", "s_feeder", "s_hub_b", "calls"),
            ("dup_e3", "s_pair_a", "s_sink", "calls"),
            ("dup_e4", "s_pair_b", "s_sink", "calls"),
        ],
    )
    conn.commit()


def test_get_symbol_neighbors_yields_no_duplicates(fresh_db):
    """TC-004 (FR-005): expansion merges by node id -- a name defined in
    two files contributes each row's neighborhood without duplicating node
    ids, and two requested names with overlapping neighborhoods keep both
    node ids and edge triples unique."""
    from cairn.viz.query import get_symbol_neighbors

    _seed(fresh_db)
    _seed_neighbors_dups(fresh_db)

    # Same name, two symbol rows, one shared caller: each row contributes
    # its own caller edge (the client's id-keyed DataSet merge collapses
    # the visual duplicate), but every node id appears exactly once.
    hub = get_symbol_neighbors(fresh_db, ["hub"])

    ids = [n["id"] for n in hub["nodes"]]
    assert len(ids) == len(set(ids))
    assert set(ids) == {"hub", "feeder"}
    assert hub["metadata"]["node_count"] == len(hub["nodes"]) == 2
    assert hub["metadata"]["edge_count"] == len(hub["edges"]) == 2

    # Two names sharing one callee: unique node ids AND unique edge triples.
    pair = get_symbol_neighbors(fresh_db, ["pair_a", "pair_b"])

    ids = [n["id"] for n in pair["nodes"]]
    assert len(ids) == len(set(ids))
    assert set(ids) == {"pair_a", "pair_b", "shared_sink"}
    triples = {(e["source"], e["target"], e["kind"]) for e in pair["edges"]}
    assert len(triples) == len(pair["edges"])
    assert triples == {
        ("pair_a", "shared_sink", "calls"),
        ("pair_b", "shared_sink", "calls"),
    }
    assert pair["metadata"]["requested"] == ["pair_a", "pair_b"]
    assert pair["metadata"]["node_count"] == len(pair["nodes"]) == 3
    assert pair["metadata"]["edge_count"] == len(pair["edges"]) == 2


def test_get_symbol_neighbors_caps_per_direction_with_honest_counts(fresh_db):
    """TC-004's cap half (FR-005): past the per-direction cap exactly the
    cap renders with truncated True; exactly-at-the-cap is not truncation
    (the cap+1 over-fetch boundary); either way the counts equal the
    returned lists, never the uncapped totals still in the store."""
    from cairn.viz.query import _NEIGHBOR_CAP, get_symbol_neighbors

    _seed(fresh_db)
    over = _NEIGHBOR_CAP + 5
    # One file per caller, so every caller's node is known by construction:
    # popular gathers 35 callers, edge_popular exactly the cap of 30.
    fresh_db.executemany(
        "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, ?, ?)",
        [
            (f"cap_f{i:02d}", "alpha", f"src/caps/c{i:02d}.py", "python")
            for i in range(over)
        ],
    )
    fresh_db.executemany(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (
                f"cap_s{i:02d}",
                f"cap_f{i:02d}",
                f"cap_caller_{i:02d}",
                f"caps.caller.{i}",
                "function",
            )
            for i in range(over)
        ]
        + [
            ("cap_popular", "f_a1", "popular", "caps.popular", "function"),
            ("cap_edge", "f_a1", "edge_popular", "caps.edge_popular", "function"),
        ],
    )
    fresh_db.executemany(
        "INSERT INTO edges (id, source_id, target_id, kind) VALUES (?, ?, ?, ?)",
        [
            (f"cap_e{i:02d}", f"cap_s{i:02d}", "cap_popular", "calls")
            for i in range(over)
        ]
        + [
            (f"cape_e{i:02d}", f"cap_s{i:02d}", "cap_edge", "calls")
            for i in range(_NEIGHBOR_CAP)
        ],
    )
    fresh_db.commit()

    popular = get_symbol_neighbors(fresh_db, ["popular"])

    assert popular["metadata"]["truncated"] is True
    # The per-direction cap yields exactly 30 of the 35 callers, never all.
    callers = {n["id"] for n in popular["nodes"]} - {"popular"}
    assert len(callers) == _NEIGHBOR_CAP
    # Count honesty (FR-005): the metadata counts equal the returned lists
    # -- never the uncapped 36 nodes / 35 edges that exist in the store.
    assert (
        popular["metadata"]["node_count"]
        == len(popular["nodes"])
        == _NEIGHBOR_CAP + 1
    )
    assert popular["metadata"]["edge_count"] == len(popular["edges"]) == _NEIGHBOR_CAP

    edge = get_symbol_neighbors(fresh_db, ["edge_popular"])

    assert edge["metadata"]["truncated"] is False
    assert edge["metadata"]["node_count"] == len(edge["nodes"]) == _NEIGHBOR_CAP + 1
    assert edge["metadata"]["edge_count"] == len(edge["edges"]) == _NEIGHBOR_CAP


def test_get_symbol_neighbors_empty_blank_and_miss_never_error(fresh_db):
    """The blank-submit boundaries: empty or whitespace-only names are the
    well-formed empty neighbors shape with requested [], and a name with no
    symbol rows appears only in metadata.requested -- never an error."""
    from cairn.viz.query import get_symbol_neighbors

    _seed(fresh_db)
    empty = {
        "nodes": [],
        "edges": [],
        "metadata": {
            "scope": "neighbors",
            "requested": [],
            "node_count": 0,
            "edge_count": 0,
            "truncated": False,
        },
    }

    assert get_symbol_neighbors(fresh_db, []) == empty
    for blanks in ([""], ["", "   "]):
        assert get_symbol_neighbors(fresh_db, blanks) == empty

    miss = get_symbol_neighbors(fresh_db, ["no_such_symbol"])
    assert miss["nodes"] == []
    assert miss["edges"] == []
    assert miss["metadata"]["requested"] == ["no_such_symbol"]
    assert miss["metadata"]["truncated"] is False


def test_get_symbol_neighbors_depth_past_one_is_clamped(fresh_db):
    """D-002's boundary: the signature accepts depth, the behavior is
    1-hop per action -- depth=5 returns exactly the depth=1 (and default)
    result over a seeded neighborhood."""
    from cairn.viz.query import get_symbol_neighbors

    _seed(fresh_db)
    _seed_expand(fresh_db)

    one_hop = get_symbol_neighbors(fresh_db, ["alpha_main"], depth=1)

    assert get_symbol_neighbors(fresh_db, ["alpha_main"], depth=5) == one_hop
    assert get_symbol_neighbors(fresh_db, ["alpha_main"]) == one_hop


def test_projects_data_flows_through_the_read_only_factory(tmp_path):
    """The dashboard's own connection factory serves the view data, and that
    connection can never write (FR-010)."""
    from cairn.dashboard.data import get_read_only_db, list_projects

    db_path = str(tmp_path / "dash.db")
    seed = sqlite3.connect(db_path)
    seed.row_factory = sqlite3.Row
    _apply_schema(seed)
    _seed(seed)
    seed.close()

    conn = get_read_only_db(db_path)
    try:
        assert [p["id"] for p in list_projects(conn)] == ["alpha", "beta", "gamma"]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            conn.execute("INSERT INTO repos (id, name, path) VALUES ('x', 'x', 'x')")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Health panel (FR-008 / US6)
# ---------------------------------------------------------------------------


def _file_db(tmp_path, name="health.db"):
    db_path = str(tmp_path / name)
    seed = sqlite3.connect(db_path)
    seed.row_factory = sqlite3.Row
    _apply_schema(seed)
    return db_path, seed


def test_get_health_reports_size_freshness_and_backend_keys(tmp_path):
    from cairn.dashboard.data import get_health, get_read_only_db

    db_path, seed = _file_db(tmp_path)
    seed.executemany(
        "INSERT INTO build_runs (kind, started_at) VALUES ('full', ?)",
        [("2026-08-18T08:00:00Z",), ("2026-08-20T07:00:00Z",)],
    )
    seed.commit()
    seed.close()

    conn = get_read_only_db(db_path)
    try:
        health = get_health(conn, db_path)
    finally:
        conn.close()

    assert health["db_size_bytes"] > 0
    assert health["db_size_bytes"] == os.stat(db_path).st_size

    # The newest build_run wins regardless of insertion order.
    assert health["last_build_at"] == "2026-08-20T07:00:00Z"
    assert health["last_build_age"] == "just now" or re.fullmatch(
        r"\d+[smhd] old", health["last_build_age"]
    )

    for key in (
        "db_size_bytes",
        "last_build_age",
        "embed_backend",
        "hash_fallback",
        "ann_backend_enabled",
        "reranker_available",
    ):
        assert key in health
    for key in ("hash_fallback", "ann_backend_enabled", "reranker_available"):
        assert isinstance(health[key], bool)


def test_get_health_in_memory_conn_degrades_to_zero_and_none(fresh_db):
    from cairn.dashboard.data import get_health

    health = get_health(fresh_db)

    assert health["db_size_bytes"] == 0  # no file behind the connection
    assert health["last_build_at"] is None
    assert health["last_build_age"] is None
    assert health["ann_embedding_rows"] == 0
    # No embeddings -> the vec0 probes are moot (a fresh store legitimately
    # has no index), reported as None rather than a missing index.
    assert health["ann_index_exists"] is None
    assert health["ann_index_rows"] is None


def test_get_health_agrees_with_doctor_on_the_same_db(tmp_path):
    """TC-018: the panel's conclusions must match `cairn doctor`'s.

    doctor's own check functions are run in-process against the same
    connection (instead of parsing CLI output), plus the graph-layer probes
    they call -- the panel must agree with both.
    """
    from cairn.cli.system import _check_ann, _check_embeddings
    from cairn.graph.ann_index import (
        ann_backend_enabled,
        index_exists,
        index_row_count,
    )
    from cairn.graph.embeddings import current_model, is_hash_fallback
    from cairn.graph.reranker import reranker_available
    from cairn.dashboard.data import get_health, get_read_only_db

    db_path, seed = _file_db(tmp_path, name="agree.db")
    seed.execute(
        "INSERT INTO build_runs (kind, started_at) VALUES ('full', '2026-08-20T07:00:00Z')"
    )
    # One embedding under the current model, no vec0 table built: exercises
    # the index_exists / index_row_count probes exactly like doctor's _check_ann.
    seed.execute(
        "INSERT INTO embeddings (symbol_id, model, dim, vec, chunk, embedded_at) "
        "VALUES ('s1', ?, 8, x'', 'chunk', '2026-08-20T07:05:00')",
        (current_model(),),
    )
    seed.commit()
    seed.close()

    conn = get_read_only_db(db_path)
    try:
        health = get_health(conn, db_path)
        emb_row = _check_embeddings(conn)
        ann_row = _check_ann(conn)
        model = current_model()
        expected = {
            "hash_fallback": is_hash_fallback(),
            "ann_backend_enabled": ann_backend_enabled(),
            "ann_model": model,
            "ann_index_exists": index_exists(conn, model),
            "ann_index_rows": index_row_count(conn, model),
            "reranker_available": reranker_available(),
        }
    finally:
        conn.close()

    for key, value in expected.items():
        assert health[key] == value
    assert health["ann_embedding_rows"] == 1

    # doctor-level agreement: its checks WARN exactly where the panel sees
    # the corresponding degradation.
    assert (emb_row["status"] != "PASS") == health["hash_fallback"]
    assert ("sqlite-vec unavailable" in ann_row["detail"]) == (
        not health["ann_backend_enabled"]
    )
    if health["ann_backend_enabled"]:
        # Seeded DB has embeddings but no vec0 table: doctor's "no vec0
        # index" WARN fires exactly when the panel sees no index.
        assert ("no vec0 index" in ann_row["detail"]) == (
            health["ann_index_exists"] is False
        )
        assert health["ann_index_rows"] is None


# ---------------------------------------------------------------------------
# Memory + task-queue panels (FR-009 / US7)
# ---------------------------------------------------------------------------


def _seed_memories(knowledge_dir):
    from cairn.memory.store import create_memory, store_memory
    from cairn.okf.bundle import OKFBundle

    bundle = OKFBundle(str(knowledge_dir))
    for ts, mtype, title in [
        ("2026-08-18T10:00:00Z", "decision", "Use RRF fusion by default"),
        ("2026-08-19T11:00:00Z", "mistake", "Skipped the fuzzy retry"),
        ("2026-08-20T09:00:00Z", "pattern", "Seeded-DB test convention"),
    ]:
        concept = create_memory(type_=mtype, title=title, body=title)
        concept.timestamp = ts
        store_memory(concept, bundle, tier="tribal")


def test_get_recent_memories_newest_first_with_type_and_title(tmp_path):
    from cairn.dashboard.data import get_recent_memories

    kdir = tmp_path / "knowledge"
    _seed_memories(kdir)

    entries = get_recent_memories(str(kdir))

    assert [e["title"] for e in entries] == [
        "Seeded-DB test convention",
        "Skipped the fuzzy retry",
        "Use RRF fusion by default",
    ]
    assert [e["type"] for e in entries] == ["pattern", "mistake", "decision"]
    assert all(e["id"].startswith("memory/") for e in entries)
    assert all(e["tier"] == "tribal" for e in entries)


def test_get_recent_memories_limit_keeps_newest(tmp_path):
    from cairn.dashboard.data import get_recent_memories

    kdir = tmp_path / "knowledge"
    _seed_memories(kdir)

    entries = get_recent_memories(str(kdir), limit=2)

    assert [e["title"] for e in entries] == [
        "Seeded-DB test convention",
        "Skipped the fuzzy retry",
    ]


def test_get_recent_memories_missing_dir_returns_empty(tmp_path):
    from cairn.dashboard.data import get_recent_memories

    assert get_recent_memories(str(tmp_path / "nope")) == []


def _seed_tasks(knowledge_dir):
    from cairn.llm.tasks import claim_task, create_task
    from cairn.okf.bundle import OKFBundle

    bundle = OKFBundle(str(knowledge_dir))
    pending = create_task(bundle, "compass-synthesize", "src/cairn/viz")
    claimed = create_task(bundle, "wiki", "wiki/dashboard")
    assert claim_task(bundle, claimed.id) is not None
    done = create_task(bundle, "flow-synthesize", "trace_flow")
    concept = bundle.read_concept(done.concept_id)
    concept.status = "done"
    bundle.write_concept(concept)
    return pending, claimed, done


def test_get_task_queue_lists_and_filters_by_status(tmp_path):
    from cairn.dashboard.data import get_task_queue

    kdir = str(tmp_path / "knowledge")
    pending, claimed, done = _seed_tasks(tmp_path / "knowledge")

    entries = get_task_queue(kdir)
    by_status = {e["status"]: e for e in entries}
    assert set(by_status) == {"pending", "in-progress", "done"}
    assert by_status["pending"]["id"] == pending.id
    assert by_status["pending"]["kind"] == "compass-synthesize"
    assert by_status["pending"]["resource"] == "src/cairn/viz"
    assert by_status["in-progress"]["id"] == claimed.id
    assert by_status["in-progress"]["claimed_at"]
    assert by_status["done"]["id"] == done.id

    assert [e["id"] for e in get_task_queue(kdir, status="pending")] == [pending.id]
    assert [e["id"] for e in get_task_queue(kdir, status="in-progress")] == [
        claimed.id
    ]
    assert [e["id"] for e in get_task_queue(kdir, status="done")] == [done.id]
    assert get_task_queue(kdir, status="failed") == []


def test_get_task_queue_missing_dir_returns_empty(tmp_path):
    from cairn.dashboard.data import get_task_queue

    assert get_task_queue(str(tmp_path / "nope")) == []


# ---------------------------------------------------------------------------
# Tool-use history (FR-005 / US3)
# ---------------------------------------------------------------------------

# Known-by-construction calls across 2 tools / 2 sessions. Ids deliberately
# do not sort with time: the view's ORDER BY invoked_at DESC must yield
# [3, 1, 4, 2], which is neither id-ascending nor id-descending (id is the
# rowid, so rowid order cannot be what produces the result).
_METRIC_ROWS = [
    # (id, tool, session, invoked_at, duration_ms, status, req, resp)
    (2, "explore", "sess-a", 1755500000.0, 12.5, "ok", 400, 1600),  # oldest
    (4, "get_callers", "sess-b", 1755500060.5, 40.0, "ok", 80, 3200),
    (1, "explore", "sess-b", 1755500120.25, 55.5, "ok", 200, 800),
    (3, "get_callers", "sess-a", 1755500180.0, 7.0, "error", 80, 0),  # newest
]


def _seed_metrics(conn, rows=None):
    rows = rows if rows is not None else _METRIC_ROWS
    conn.executemany(
        "INSERT INTO tool_metrics (id, tool_name, session_id, invoked_at, "
        "duration_ms, status, error_message, req_chars, resp_chars, args_summary) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                rid,
                tool,
                sess,
                ts,
                dur,
                status,
                "boom" if status == "error" else None,
                req,
                resp,
                '{"query": "alpha"}',
            )
            for rid, tool, sess, ts, dur, status, req, resp in rows
        ],
    )
    conn.commit()
    return [row[0] for row in rows]


def _history_rows(
    count, base_ts=1755500000.25, tool="explore", session="sess-a", id_start=1
):
    """``count`` bulk rows, one per second: id ``id_start + i`` invoked at
    ``base_ts + i``, so id order and time order agree and either key's
    newest-first walk is the reverse id sequence. The ``.25`` base keeps
    every timestamp non-integral: like production ``time.time()`` values,
    they stay REAL through sqlite's NUMERIC-affinity ``TIMESTAMP`` column,
    so cursor strings carry the float verbatim."""
    return [
        (id_start + i, tool, session, base_ts + i, 5.0, "ok", 10, 10)
        for i in range(count)
    ]


def _walk_forward(conn, cursor=None, **kwargs):
    """Walk pages via ``next`` from ``cursor`` (None = first page) until a
    page comes back without one.

    Returns ``(rows in walk order, per-page sizes)``. The 64-page hard bound
    is far beyond anything seeded here and turns a cursor bug into a fast
    failure instead of a hang."""
    from cairn.dashboard.data import list_history

    rows, sizes = [], []
    for _ in range(64):
        page = list_history(conn, before=cursor, **kwargs)
        rows.extend(page["rows"])
        sizes.append(len(page["rows"]))
        cursor = page["next"]
        if cursor is None:
            return rows, sizes
    raise AssertionError("history walk never reached a nextless page")


def test_list_history_newest_first_with_full_columns(fresh_db):
    from cairn.dashboard.data import list_history

    _seed_metrics(fresh_db)

    history = list_history(fresh_db)["rows"]

    # invoked_at descending; not id order ([1, 2, 3, 4]) nor its reverse.
    assert [h["id"] for h in history] == [3, 1, 4, 2]
    newest = history[0]
    assert newest["tool_name"] == "get_callers"
    assert newest["session_id"] == "sess-a"
    assert newest["invoked_at"] == 1755500180.0  # epoch float, verbatim
    assert newest["duration_ms"] == 7.0
    assert newest["status"] == "error"
    assert newest["error_message"] == "boom"
    # Per-row estimated tokens (US4-AC2): chars // CHARS_PER_TOKEN.
    oldest = history[-1]
    assert (oldest["est_req_tokens"], oldest["est_resp_tokens"]) == (100, 400)
    assert all("args_summary" in h for h in history)


def test_list_history_filters_tool_session_combined_and_nonsense(fresh_db):
    from cairn.dashboard.data import list_history

    _seed_metrics(fresh_db)

    by_tool = list_history(fresh_db, tool_name="explore")["rows"]
    assert [h["id"] for h in by_tool] == [1, 2]

    by_session = list_history(fresh_db, session_id="sess-b")["rows"]
    assert [h["id"] for h in by_session] == [1, 4]

    combined = list_history(fresh_db, tool_name="explore", session_id="sess-b")["rows"]
    assert [h["id"] for h in combined] == [1]

    # Nonsense filters are empty pages, never errors.
    empty_page = {"rows": [], "next": None, "prev": None}
    assert list_history(fresh_db, tool_name="no_such_tool") == empty_page
    assert list_history(fresh_db, session_id="no-such-session") == empty_page
    assert list_history(fresh_db, tool_name="no_such_tool", session_id="x") == empty_page


def test_list_history_pre_migration_null_sizes_stay_unknown(fresh_db):
    from cairn.dashboard.data import list_history

    # A row recorded before the payload-size migrations: no size columns set.
    fresh_db.execute(
        "INSERT INTO tool_metrics (id, tool_name, session_id, invoked_at, "
        "duration_ms, status) VALUES (1, 'explore', 'sess-a', 1755500000.0, 5.0, 'ok')"
    )
    fresh_db.commit()

    (row,) = list_history(fresh_db)["rows"]

    assert row["req_chars"] is None
    assert row["resp_chars"] is None
    # Unknown, not zero-vs-value confusion: None, never 0.
    assert row["est_req_tokens"] is None
    assert row["est_resp_tokens"] is None


def test_list_history_fresh_db_returns_empty_list(fresh_db):
    from cairn.dashboard.data import list_history

    # An empty store is a well-formed empty page, never an error.
    empty_page = {"rows": [], "next": None, "prev": None}
    assert list_history(fresh_db) == empty_page
    assert list_history(fresh_db, tool_name="explore") == empty_page


def test_list_history_args_summary_truncated_never_expanded(fresh_db):
    from cairn.mcp_server.metric_buffering import MAX_ARGS_SUMMARY_CHARS

    from cairn.dashboard.data import list_history

    tail_marker = "DISTINCTIVE_TAIL_" + "x" * 300
    payload = '{"query": "' + "y" * 300 + tail_marker + '"}'
    # Stored exactly as the write chokepoint (T004) leaves it: redacted and
    # sliced to MAX_ARGS_SUMMARY_CHARS, with req_chars the FULL payload size.
    _seed_metrics(
        fresh_db,
        rows=[(1, "explore", "sess-a", 1755500000.0, 5.0, "ok", len(payload), 0)],
    )
    fresh_db.execute(
        "UPDATE tool_metrics SET args_summary = ? WHERE id = 1",
        (payload[:MAX_ARGS_SUMMARY_CHARS],),
    )
    fresh_db.commit()

    (row,) = list_history(fresh_db)["rows"]

    assert row["args_summary"] == payload[:MAX_ARGS_SUMMARY_CHARS]
    assert len(row["args_summary"]) <= 200
    assert "DISTINCTIVE_TAIL_" not in row["args_summary"]  # TC-024
    # The full-payload size still drives the token estimate.
    assert row["est_req_tokens"] == len(payload) // 4


# ---------------------------------------------------------------------------
# History pagination (traffic-scale FR-001/FR-006, US1-AC1/AC2): keyset
# cursors on (invoked_at DESC, id DESC) per tech-spec D-001. These call the
# data layer directly over a seeded connection.
# ---------------------------------------------------------------------------


def test_list_history_first_page_is_bounded_with_older_page_cursor(fresh_db):
    """TC-001's data half: however large the store, the default fetch is one
    bounded page plus the cursor of what lies older."""
    from cairn.dashboard.data import HISTORY_PAGE_SIZE, list_history

    total = HISTORY_PAGE_SIZE + 70  # deliberately past one full page
    _seed_metrics(fresh_db, rows=_history_rows(total))

    page = list_history(fresh_db)

    assert len(page["rows"]) == HISTORY_PAGE_SIZE
    assert [h["id"] for h in page["rows"]] == list(
        range(total, total - HISTORY_PAGE_SIZE, -1)
    )
    # The older-page cursor is the page's oldest row: "<invoked_at>,<id>"
    # (row id N was seeded at base_ts + N - 1).
    oldest_id = total - HISTORY_PAGE_SIZE + 1
    assert page["next"] == f"{1755500000.25 + (oldest_id - 1)},{oldest_id}"
    assert page["prev"] is None  # a cursorless fetch starts at the newest


def test_list_history_cursor_stable_under_mid_paging_insert(fresh_db):
    """TC-002: pages stay stable and repeat-free while rows land mid-walk."""
    from cairn.dashboard.data import list_history

    _seed_metrics(fresh_db, rows=_history_rows(120))

    first = list_history(fresh_db)  # ids 120..71, fetched pre-insert

    # A new call lands between page fetches, newer than every walked cursor.
    _seed_metrics(
        fresh_db,
        rows=[(121, "explore", "sess-late", 1755500500.25, 5.0, "ok", 10, 10)],
    )

    rows, _sizes = _walk_forward(fresh_db, cursor=first["next"])

    walked = [h["id"] for h in first["rows"]] + [r["id"] for r in rows]
    # The walk stays strictly descending with no row on two pages and covers
    # exactly the pre-insert history -- the late row is never injected past
    # its (newest) position into the older pages being walked.
    assert walked == list(range(120, 0, -1))
    assert 121 not in {r["id"] for r in rows}
    # It appears only where its position puts it: the refreshed first page.
    refreshed = list_history(fresh_db)
    assert refreshed["rows"][0]["id"] == 121


def test_list_history_full_walk_covers_every_row_exactly_once(fresh_db):
    from cairn.dashboard.data import HISTORY_PAGE_SIZE

    total = 3 * HISTORY_PAGE_SIZE + 37  # final page is partial
    _seed_metrics(fresh_db, rows=_history_rows(total))

    rows, sizes = _walk_forward(fresh_db)

    assert sizes == [HISTORY_PAGE_SIZE] * 3 + [37]
    ids = [r["id"] for r in rows]
    assert len(ids) == len(set(ids))  # no row appears on two pages
    assert ids == list(range(total, 0, -1))  # every seeded row, exactly once


def test_list_history_backward_walk_retraces_to_first_page(fresh_db):
    """TC-002's other half: paging forward and back lands on the same pages."""
    from cairn.dashboard.data import list_history

    _seed_metrics(fresh_db, rows=_history_rows(120))

    first = list_history(fresh_db)
    second = list_history(fresh_db, before=first["next"])

    assert [h["id"] for h in second["rows"]] == list(range(70, 20, -1))
    # prev points at the page's newest row; following it flips the keyset
    # comparison without flipping the newest-first presentation.
    assert second["prev"] == f"{1755500000.25 + 69},70"

    back = list_history(fresh_db, after=second["prev"])

    assert [h["id"] for h in back["rows"]] == [h["id"] for h in first["rows"]]
    assert back["next"] == first["next"]
    assert back["prev"] is None  # retraced all the way to the first page


def test_list_history_equal_invoked_at_tie_breaks_on_higher_id(fresh_db):
    from cairn.dashboard.data import list_history

    ts = 1755500000.25
    _seed_metrics(
        fresh_db,
        rows=[
            (1, "explore", "sess-a", ts, 5.0, "ok", 10, 10),
            (2, "get_callers", "sess-a", ts, 5.0, "ok", 10, 10),
            (3, "ask_compass", "sess-a", ts, 5.0, "ok", 10, 10),
        ],
    )

    # Same instant: id DESC decides, deterministically highest-first.
    first = list_history(fresh_db, limit=2)
    assert [h["id"] for h in first["rows"]] == [3, 2]

    # The cursor is (invoked_at, id): the remaining same-instant row comes
    # next without the tie re-serving row 2.
    second = list_history(fresh_db, before=first["next"], limit=2)
    assert [h["id"] for h in second["rows"]] == [1]
    assert second["next"] is None


def test_list_history_unparseable_cursor_is_no_filter_never_an_error(fresh_db):
    from cairn.dashboard.data import list_history

    _seed_metrics(fresh_db)

    plain = list_history(fresh_db)

    # A cursor is a hint, never an error: every unparseable shape (no comma,
    # non-numeric fields) degrades to the plain first page.
    for bad in ("garbage", "abc,def", "1755500000.0,late"):
        assert list_history(fresh_db, before=bad) == plain
        assert list_history(fresh_db, after=bad) == plain


def test_list_history_filters_compose_with_paging_cursors(fresh_db):
    from cairn.dashboard.data import HISTORY_PAGE_SIZE

    _seed_metrics(
        fresh_db,
        rows=(
            _history_rows(60, session="sess-a")  # ids 1-60
            + _history_rows(40, session="sess-b", id_start=61)  # ids 61-100
            + _history_rows(30, tool="get_callers", id_start=101)  # ids 101-130
        ),
    )

    # sess-b and get_callers timestamps interleave sess-a's, so only the
    # composed WHERE (filter AND keyset comparison) keeps every walked page
    # inside the filter.
    rows, sizes = _walk_forward(fresh_db, tool_name="explore", session_id="sess-a")

    assert sizes == [HISTORY_PAGE_SIZE, 10]
    assert all(
        r["tool_name"] == "explore" and r["session_id"] == "sess-a" for r in rows
    )
    assert [r["id"] for r in rows] == list(range(60, 0, -1))


# ---------------------------------------------------------------------------
# Token aggregates (FR-006 / US4) + call chains (FR-007 / US5)
# ---------------------------------------------------------------------------


def test_session_gap_s_constant():
    from cairn.dashboard import data

    assert data.SESSION_GAP_S == 1800


def test_get_tool_tokens_aggregates_ranked_by_total_desc(fresh_db):
    from cairn.dashboard.data import get_tool_tokens

    _seed_metrics(fresh_db)

    tokens = get_tool_tokens(fresh_db)

    # get_callers: (80+3200) + (80+0) chars -> 40 + 800 = 840 tokens;
    # explore: (400+1600) + (200+800) chars -> 150 + 600 = 750 tokens.
    assert [t["tool_name"] for t in tokens] == ["get_callers", "explore"]

    by_tool = {t["tool_name"]: t for t in tokens}
    assert by_tool["get_callers"]["calls"] == 2
    assert by_tool["get_callers"]["total_tokens"] == 840
    assert by_tool["explore"]["calls"] == 2
    assert by_tool["explore"]["total_tokens"] == 750
    # Every row internally consistent: req + resp == total, mean * calls
    # == total within rounding (TC-014).
    for t in tokens:
        assert t["est_req_tokens"] + t["est_resp_tokens"] == t["total_tokens"]
        assert t["mean_tokens"] * t["calls"] == pytest.approx(t["total_tokens"])


def test_get_tool_tokens_seeded_400_800_is_100_plus_200(fresh_db):
    from cairn.dashboard.data import get_tool_tokens

    _seed_metrics(
        fresh_db,
        rows=[(1, "explore", "sess-a", 1755500000.0, 5.0, "ok", 400, 800)],
    )

    (row,) = get_tool_tokens(fresh_db)

    assert (row["est_req_tokens"], row["est_resp_tokens"]) == (100, 200)
    assert row["total_tokens"] == 300
    assert row["calls"] == 1
    assert row["mean_tokens"] == 300.0


def test_get_tool_tokens_null_sizes_count_as_calls_zero_tokens(fresh_db):
    from cairn.dashboard.data import get_tool_tokens

    # One pre-migration row (no size columns) plus one sized row.
    fresh_db.execute(
        "INSERT INTO tool_metrics (id, tool_name, session_id, invoked_at, "
        "duration_ms, status) VALUES (1, 'explore', 'sess-a', 1755500000.0, 5.0, 'ok')"
    )
    _seed_metrics(
        fresh_db,
        rows=[(2, "explore", "sess-a", 1755500060.0, 5.0, "ok", 400, 800)],
    )

    (row,) = get_tool_tokens(fresh_db)

    assert row["calls"] == 2  # the NULL-size row still counts as a call
    assert row["total_tokens"] == 300  # only the sized row's 100 + 200
    assert row["mean_tokens"] == 150.0


def test_get_tool_tokens_empty_db_returns_empty_list(fresh_db):
    from cairn.dashboard.data import get_tool_tokens

    assert get_tool_tokens(fresh_db) == []


def test_get_session_chains_gap_splits_bursts_keeps_order(fresh_db):
    from cairn.dashboard.data import get_session_chains

    base = 1755500000.0
    later = base + 6 * 3600  # six hours on: far beyond SESSION_GAP_S
    _seed_metrics(
        fresh_db,
        rows=[
            # sess-a burst 1: three calls a minute apart (TC-017).
            (1, "explore", "sess-a", base, 5.0, "ok", 10, 10),
            (2, "get_callers", "sess-a", base + 60, 5.0, "ok", 10, 10),
            (3, "ask_compass", "sess-a", base + 120, 5.0, "ok", 10, 10),
            # sess-a burst 2: two more calls six hours later, same session.
            (4, "explore", "sess-a", later, 5.0, "ok", 10, 10),
            (5, "impact_analysis", "sess-a", later + 60, 5.0, "ok", 10, 10),
            # sess-b: a single call is still a chain (TC-016).
            (6, "explore", "sess-b", base + 30, 5.0, "ok", 10, 10),
        ],
    )

    result = get_session_chains(fresh_db)
    chains = result["chains"]

    # Three chains, under both render bounds: the wrapper is the flat list
    # plus honest totals (nothing hidden, FR-004's no-op half).
    assert (result["total_chains"], result["truncated"]) == (3, False)
    # Sessions newest-activity-first (sess-a ends at later+60, sess-b at
    # base+30); chains within a session chronological.
    assert [(c["session_id"], c["call_count"]) for c in chains] == [
        ("sess-a", 3),
        ("sess-a", 2),
        ("sess-b", 1),
    ]

    burst1, burst2, single = chains
    assert [c["id"] for c in burst1["calls"]] == [1, 2, 3]  # chronological
    assert [c["tool_name"] for c in burst1["calls"]] == [
        "explore",
        "get_callers",
        "ask_compass",
    ]
    assert [c["id"] for c in burst2["calls"]] == [4, 5]
    assert (burst1["started_at"], burst1["ended_at"]) == (base, base + 120)
    assert (burst2["started_at"], burst2["ended_at"]) == (later, later + 60)
    call = burst2["calls"][0]
    assert (call["invoked_at"], call["duration_ms"], call["status"]) == (
        later,
        5.0,
        "ok",
    )
    assert single["calls"][0]["id"] == 6


def test_get_session_chains_splits_only_beyond_the_gap(fresh_db):
    from cairn.dashboard.data import SESSION_GAP_S, get_session_chains

    base = 1755500000.0
    _seed_metrics(
        fresh_db,
        rows=[
            (1, "explore", "sess-a", base, 5.0, "ok", 10, 10),
            # Exactly SESSION_GAP_S apart: still the same chain.
            (2, "explore", "sess-a", base + SESSION_GAP_S, 5.0, "ok", 10, 10),
            # One second past the gap: a new chain starts.
            (3, "explore", "sess-a", base + 2 * SESSION_GAP_S + 1, 5.0, "ok", 10, 10),
        ],
    )

    chains = get_session_chains(fresh_db)["chains"]

    assert [c["call_count"] for c in chains] == [2, 1]


def test_get_session_chains_equal_timestamps_stay_one_chain(fresh_db):
    from cairn.dashboard.data import get_session_chains

    # invoked_at is NOT NULL in the schema, so NULL rows cannot occur; the
    # nearest legal edge is several calls at the same instant (zero gap).
    _seed_metrics(
        fresh_db,
        rows=[
            (1, "explore", "sess-a", 1755500000.0, 5.0, "ok", 10, 10),
            (2, "get_callers", "sess-a", 1755500000.0, 5.0, "ok", 10, 10),
            (3, "ask_compass", "sess-a", 1755500000.0, 5.0, "ok", 10, 10),
        ],
    )

    (chain,) = get_session_chains(fresh_db)["chains"]

    assert chain["call_count"] == 3
    assert [c["id"] for c in chain["calls"]] == [1, 2, 3]
    assert (chain["started_at"], chain["ended_at"]) == (1755500000.0, 1755500000.0)


def test_get_session_chains_empty_db_returns_empty_list(fresh_db):
    from cairn.dashboard.data import get_session_chains

    # An empty store is the well-formed empty wrapper, never an error.
    assert get_session_chains(fresh_db) == {
        "chains": [],
        "total_chains": 0,
        "truncated": False,
    }


# ---------------------------------------------------------------------------
# Time windows (FR-002/FR-003 / US2): the shared ``since`` predicate across
# history, tokens, and chains. Seeded timestamps anchor to ``time.time()``
# at seeding time with fixed offsets, so every cutoff is deterministic —
# never a sleep, never wall-clock dependence.
# ---------------------------------------------------------------------------


def test_list_history_since_excludes_outside_rows_keeps_cursors_in_window(fresh_db):
    """TC-003's history half: only in-window rows render, and the paging
    cursors stay in-window too (FR-002 + FR-006)."""
    from cairn.dashboard.data import list_history

    cutoff = time.time() - 86400  # a 24h-style window edge
    _seed_metrics(
        fresh_db,
        rows=[
            # Outside the window, ids interleaved with the inside rows:
            # exclusion must key on invoked_at, never on id order.
            (1, "explore", "sess-a", cutoff - 7200, 5.0, "ok", 40, 40),
            (2, "explore", "sess-a", cutoff - 60, 5.0, "ok", 80, 80),
            # Inside, spanning the edge: exactly-at-cutoff counts (>=).
            (3, "explore", "sess-a", cutoff, 5.0, "ok", 100, 100),
            (4, "get_callers", "sess-a", cutoff + 60, 5.0, "ok", 200, 200),
            (5, "ask_compass", "sess-a", cutoff + 120, 5.0, "ok", 400, 400),
        ],
    )

    # All time (since=None): everything, newest first.
    assert [h["id"] for h in list_history(fresh_db)["rows"]] == [5, 4, 3, 2, 1]

    first = list_history(fresh_db, since=cutoff, limit=2)
    assert [h["id"] for h in first["rows"]] == [5, 4]
    second = list_history(fresh_db, since=cutoff, limit=2, before=first["next"])
    assert [h["id"] for h in second["rows"]] == [3]  # boundary row is inside
    # Two older rows exist in the store, but both are outside the window:
    # no next cursor may point at them.
    assert second["next"] is None
    # prev stays in-window as well: retracing returns only in-window rows.
    back = list_history(fresh_db, since=cutoff, limit=2, after=second["prev"])
    assert [h["id"] for h in back["rows"]] == [5, 4]
    assert back["next"] == first["next"]


def test_list_history_since_excludes_null_invoked_at_rows():
    """The shipped schema declares invoked_at NOT NULL, so NULL rows cannot
    occur in production; this pins the window predicate's SQL contract on a
    legacy-shape table without the constraint — NULL never satisfies
    ``invoked_at >= ?`` (FR-002), so such rows surface only on all-time
    pages."""
    from cairn.dashboard.data import list_history

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE tool_metrics ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, tool_name TEXT NOT NULL, "
        "session_id TEXT NOT NULL DEFAULT 'unknown', invoked_at TIMESTAMP, "
        "duration_ms REAL, status TEXT NOT NULL DEFAULT 'ok', "
        "error_message TEXT, req_chars INTEGER, resp_chars INTEGER, "
        "args_summary TEXT, source TEXT NOT NULL DEFAULT 'mcp')"
    )
    cutoff = time.time() - 86400
    _seed_metrics(
        conn,
        rows=[
            (1, "explore", "sess-a", None, 5.0, "ok", 40, 40),
            (2, "explore", "sess-a", cutoff + 60, 5.0, "ok", 80, 80),
        ],
    )

    # Windowed: only the dated row; the NULL row never matches.
    assert [h["id"] for h in list_history(conn, since=cutoff)["rows"]] == [2]
    # All time: the NULL row is still there, not silently dropped.
    assert {h["id"] for h in list_history(conn)["rows"]} == {1, 2}


def test_get_session_chains_since_drops_old_sessions_and_old_calls(fresh_db):
    """TC-003's chains half: a session with no in-window calls vanishes
    entirely; a mixed session keeps only its in-window calls with chain
    bounds recomputed from them; an empty window is an empty list."""
    from cairn.dashboard.data import get_session_chains

    cutoff = time.time() - 86400
    _seed_metrics(
        fresh_db,
        rows=[
            # sess-old: every call predates the window — the session vanishes.
            (1, "explore", "sess-old", cutoff - 7200, 5.0, "ok", 10, 10),
            (2, "get_callers", "sess-old", cutoff - 7140, 5.0, "ok", 10, 10),
            # sess-mixed: one call before the edge, two after — only the two
            # in-window calls survive, still one chain (gaps stay small).
            (3, "explore", "sess-mixed", cutoff - 60, 5.0, "ok", 10, 10),
            (4, "explore", "sess-mixed", cutoff + 60, 5.0, "ok", 10, 10),
            (5, "get_callers", "sess-mixed", cutoff + 120, 5.0, "ok", 10, 10),
            # sess-recent: born inside the window.
            (6, "ask_compass", "sess-recent", cutoff + 180, 5.0, "ok", 10, 10),
        ],
    )

    # Sessions newest-activity-first: sess-recent ends at cutoff+180,
    # sess-mixed (windowed) at cutoff+120. One tuple per chain: sess-mixed
    # keeps exactly its two in-window calls, sess-old is gone entirely.
    windowed = get_session_chains(fresh_db, since=cutoff)
    chains = windowed["chains"]
    assert [(c["session_id"], c["call_count"]) for c in chains] == [
        ("sess-recent", 1),
        ("sess-mixed", 2),
    ]
    mixed = chains[1]
    # The pre-edge call 3 is gone; chain bounds recompute from what remains.
    assert [call["id"] for call in mixed["calls"]] == [4, 5]
    assert (mixed["started_at"], mixed["ended_at"]) == (cutoff + 60, cutoff + 120)

    # A window no call falls in is the empty wrapper, never an error.
    assert get_session_chains(fresh_db, since=time.time() + 3600) == {
        "chains": [],
        "total_chains": 0,
        "truncated": False,
    }

    # All time: all three sessions, sess-mixed whole again, totals honest.
    all_time = get_session_chains(fresh_db)
    assert [
        (c["session_id"], c["call_count"]) for c in all_time["chains"]
    ] == [
        ("sess-recent", 1),
        ("sess-mixed", 3),
        ("sess-old", 2),
    ]
    assert (all_time["total_chains"], all_time["truncated"]) == (3, False)


def test_get_session_chains_session_id_filters_and_composes_with_window(fresh_db):
    """FR-002's session filter: ``session_id`` reads only that session's
    rows, composes with the ``since`` window, and a no-match session is
    the empty wrapper, never an error."""
    from cairn.dashboard.data import get_session_chains

    cutoff = time.time() - 86400
    _seed_metrics(
        fresh_db,
        rows=[
            # sess-a straddles the window edge; sess-b is fully inside.
            (1, "explore", "sess-a", cutoff - 60, 5.0, "ok", 10, 10),
            (2, "get_callers", "sess-a", cutoff + 60, 5.0, "ok", 10, 10),
            (3, "explore", "sess-b", cutoff + 120, 5.0, "ok", 10, 10),
            (4, "ask_compass", "sess-b", cutoff + 180, 5.0, "ok", 10, 10),
        ],
    )

    # Session-only: exactly sess-a's calls, still one chain (small gaps).
    only_a = get_session_chains(fresh_db, session_id="sess-a")
    assert [(c["session_id"], c["call_count"]) for c in only_a["chains"]] == [
        ("sess-a", 2)
    ]
    assert (only_a["total_chains"], only_a["truncated"]) == (1, False)

    # Composed with the window: sess-a's pre-edge call drops out and the
    # chain bounds recompute from what remains.
    windowed = get_session_chains(fresh_db, since=cutoff, session_id="sess-a")
    (chain,) = windowed["chains"]
    assert [call["id"] for call in chain["calls"]] == [2]
    assert (chain["started_at"], chain["ended_at"]) == (cutoff + 60, cutoff + 60)

    # A session with no recorded calls is the empty wrapper, never an error.
    assert get_session_chains(fresh_db, session_id="no-such-session") == {
        "chains": [],
        "total_chains": 0,
        "truncated": False,
    }

    # None keeps the unfiltered behavior: every session,
    # newest-activity-first.
    all_sessions = get_session_chains(fresh_db)
    assert [
        (c["session_id"], c["call_count"]) for c in all_sessions["chains"]
    ] == [("sess-b", 2), ("sess-a", 2)]


def test_get_tool_tokens_since_recomputes_aggregates_and_ranking(fresh_db):
    """TC-004: a tool with heavy old traffic and light recent traffic —
    windowed totals are the window's sums only, and the ranking flips;
    NULL-size rows inside the window still count as calls, zero tokens."""
    from cairn.dashboard.data import get_tool_tokens

    cutoff = time.time() - 86400
    _seed_metrics(
        fresh_db,
        rows=[
            # tool_old_heavy: three big calls days ago...
            (1, "tool_old_heavy", "sess-a", cutoff - 30 * 86400, 5.0, "ok", 1600, 3200),
            (2, "tool_old_heavy", "sess-a", cutoff - 29 * 86400, 5.0, "ok", 1600, 3200),
            (3, "tool_old_heavy", "sess-a", cutoff - 28 * 86400, 5.0, "ok", 1600, 3200),
            # ...and one small call inside the window.
            (4, "tool_old_heavy", "sess-a", cutoff + 60, 5.0, "ok", 400, 800),
            # tool_steady: one large call inside — the window's leader.
            (5, "tool_steady", "sess-b", cutoff + 120, 5.0, "ok", 2400, 4800),
        ],
    )
    # A pre-migration (NULL-size) row inside the window, for a third tool.
    fresh_db.execute(
        "INSERT INTO tool_metrics (id, tool_name, session_id, invoked_at, "
        "duration_ms, status) VALUES (6, 'tool_nulls', 'sess-c', ?, 5.0, 'ok')",
        (cutoff + 180,),
    )
    fresh_db.commit()

    # All time: tool_old_heavy leads, 3x(400+800) = 3600 vs steady's 1800.
    assert [t["tool_name"] for t in get_tool_tokens(fresh_db)] == [
        "tool_old_heavy",
        "tool_steady",
        "tool_nulls",
    ]

    # Windowed: only in-window calls count and the ranking flips (AC2).
    windowed = get_tool_tokens(fresh_db, since=cutoff)
    assert [t["tool_name"] for t in windowed] == [
        "tool_steady",
        "tool_old_heavy",
        "tool_nulls",
    ]
    by_tool = {t["tool_name"]: t for t in windowed}
    heavy = by_tool["tool_old_heavy"]
    assert heavy["calls"] == 1  # only the light recent call
    assert heavy["total_tokens"] == 300  # 400//4 + 800//4, not 3600+300
    assert heavy["mean_tokens"] == 300.0
    steady = by_tool["tool_steady"]
    assert (steady["calls"], steady["total_tokens"]) == (1, 1800)
    # The NULL-size in-window row is still a call, contributing zero tokens.
    nulls = by_tool["tool_nulls"]
    assert nulls["calls"] == 1
    assert (nulls["est_req_tokens"], nulls["est_resp_tokens"]) == (0, 0)
    assert nulls["total_tokens"] == 0


def test_list_history_window_composes_with_tool_and_session_filters(fresh_db):
    """FR-006: the window is one more WHERE term — tool and session filters
    each narrow it, and it narrows them."""
    from cairn.dashboard.data import list_history

    cutoff = time.time() - 86400
    _seed_metrics(
        fresh_db,
        rows=[
            (1, "explore", "sess-a", cutoff - 60, 5.0, "ok", 10, 10),  # old, matches
            (2, "explore", "sess-a", cutoff + 60, 5.0, "ok", 10, 10),  # the one hit
            (3, "explore", "sess-b", cutoff + 120, 5.0, "ok", 10, 10),  # wrong session
            (4, "get_callers", "sess-a", cutoff + 180, 5.0, "ok", 10, 10),  # wrong tool
            (5, "get_callers", "sess-b", cutoff - 60, 5.0, "ok", 10, 10),  # matches nothing
        ],
    )

    page = list_history(fresh_db, tool_name="explore", session_id="sess-a", since=cutoff)
    assert [h["id"] for h in page["rows"]] == [2]
    assert (page["next"], page["prev"]) == (None, None)
    # Without the window the same filter also finds the old call: the two
    # filter kinds compose, neither replacing the other.
    unwindowed = list_history(fresh_db, tool_name="explore", session_id="sess-a")
    assert [h["id"] for h in unwindowed["rows"]] == [2, 1]


# ---------------------------------------------------------------------------
# Chains bounds (traffic-scale FR-004 / US3-AC1, TC-005): the render caps —
# chains at once (CHAINS_MAX_CHAINS) and calls kept per chain
# (CHAINS_CALLS_PER_CHAIN, overridable via expand) — over the legacy
# all-'unknown'-session store shape, composing with the ``since`` window.
# ---------------------------------------------------------------------------


def test_get_session_chains_legacy_unknown_session_capped_per_chain(fresh_db):
    """TC-005, the spec's first case: a legacy store where every call sits
    under one session id 'unknown' — hundreds of calls, one chain. The
    rendered chain keeps only its newest CHAINS_CALLS_PER_CHAIN calls with
    honest shown/total accounting and recomputed bounds; expand exempts
    that session from the per-chain cap."""
    from cairn.dashboard.data import CHAINS_CALLS_PER_CHAIN, get_session_chains

    # 200 calls a second apart: far inside SESSION_GAP_S, so one chain.
    base = 1755500000.25  # _history_rows' default anchor
    _seed_metrics(fresh_db, rows=_history_rows(200, session="unknown"))

    result = get_session_chains(fresh_db)

    assert (result["total_chains"], result["truncated"]) == (1, False)
    (chain,) = result["chains"]
    assert chain["session_id"] == "unknown"
    # The rendered chain is exactly the cap of calls: the newest tail.
    assert len(chain["calls"]) == CHAINS_CALLS_PER_CHAIN
    first_shown = 200 - CHAINS_CALLS_PER_CHAIN + 1
    assert [c["id"] for c in chain["calls"]] == list(range(first_shown, 201))
    # Honest accounting: what renders vs what exists.
    assert chain["shown_calls"] == CHAINS_CALLS_PER_CHAIN
    assert chain["call_count"] == 200
    assert chain["truncated_calls"] is True
    # started_at recomputes to the first INCLUDED call; ended_at keeps the
    # full chain's truth.
    assert chain["started_at"] == base + (first_shown - 1)
    assert chain["ended_at"] == base + 199

    # expand='unknown' lifts the per-chain cap for that session only:
    # every call returns, untruncated, bounds whole again.
    (full,) = get_session_chains(fresh_db, expand="unknown")["chains"]
    assert full["shown_calls"] == full["call_count"] == 200
    assert [c["id"] for c in full["calls"]] == list(range(1, 201))
    assert full["truncated_calls"] is False
    assert (full["started_at"], full["ended_at"]) == (base, base + 199)


def test_get_session_chains_chain_list_cap_keeps_newest_activity(fresh_db):
    """FR-004's list bound: more chains than CHAINS_MAX_CHAINS render —
    exactly the cap is kept, the kept ones are the newest-activity chains,
    and total_chains/truncated tell the truth about the rest."""
    from cairn.dashboard.data import CHAINS_MAX_CHAINS, get_session_chains

    total = CHAINS_MAX_CHAINS + 4  # deliberately past the cap
    base = 1755500000.0
    # One call per session: session sess-00..sess-23, sess-i's call at
    # base + i, so newest activity is strictly the highest id.
    _seed_metrics(
        fresh_db,
        rows=[
            (i + 1, "explore", f"sess-{i:02d}", base + i, 5.0, "ok", 10, 10)
            for i in range(total)
        ],
    )

    result = get_session_chains(fresh_db)

    assert len(result["chains"]) == CHAINS_MAX_CHAINS
    assert result["total_chains"] == total
    assert result["truncated"] is True
    # The cap applies after the newest-activity-first sort: the newest
    # sessions survive, the oldest four vanish from the rendered list.
    assert [c["session_id"] for c in result["chains"]] == [
        f"sess-{i:02d}" for i in range(total - 1, total - 1 - CHAINS_MAX_CHAINS, -1)
    ]
    # Each kept chain is whole: the list cap never truncates calls.
    for chain in result["chains"]:
        assert (chain["call_count"], chain["shown_calls"]) == (1, 1)
        assert chain["truncated_calls"] is False


def test_get_session_chains_below_caps_results_unchanged_with_new_keys(fresh_db):
    """FR-004's no-op half: below both bounds the wrapper changes nothing
    about which chains render, their order, or their calls — it only adds
    the honest keys."""
    from cairn.dashboard.data import (
        CHAINS_CALLS_PER_CHAIN,
        CHAINS_MAX_CHAINS,
        get_session_chains,
    )

    base = 1755500000.0
    _seed_metrics(
        fresh_db,
        rows=[
            (1, "explore", "sess-a", base, 5.0, "ok", 10, 10),
            (2, "get_callers", "sess-a", base + 60, 5.0, "ok", 10, 10),
            (3, "ask_compass", "sess-b", base + 120, 5.0, "ok", 10, 10),
            (4, "explore", "sess-c", base + 6 * 3600, 5.0, "ok", 10, 10),
            (5, "impact_analysis", "sess-c", base + 6 * 3600 + 60, 5.0, "ok", 10, 10),
        ],
    )

    result = get_session_chains(fresh_db)
    chains = result["chains"]

    # Well under both bounds by construction.
    assert len(chains) < CHAINS_MAX_CHAINS
    assert all(c["call_count"] < CHAINS_CALLS_PER_CHAIN for c in chains)
    # Same order and content semantics the gap tests assert: sessions
    # newest-activity-first (sess-c ends at base+6h+60, sess-b at base+120,
    # sess-a at base+60), calls chronological within each chain.
    assert [(c["session_id"], c["call_count"]) for c in chains] == [
        ("sess-c", 2),
        ("sess-b", 1),
        ("sess-a", 2),
    ]
    assert [c["id"] for c in chains[2]["calls"]] == [1, 2]
    for chain in chains:
        # Nothing truncated: shown == all, bounds the full chain's truth.
        assert chain["shown_calls"] == chain["call_count"] == len(chain["calls"])
        assert chain["truncated_calls"] is False
        timestamps = [call["invoked_at"] for call in chain["calls"]]
        assert timestamps == sorted(timestamps)
        assert (chain["started_at"], chain["ended_at"]) == (
            timestamps[0],
            timestamps[-1],
        )
    assert (result["total_chains"], result["truncated"]) == (3, False)


def test_get_session_chains_since_windows_before_the_caps(fresh_db):
    """FR-002 + FR-004: the window filters rows before grouping and before
    either cap — an old giant session that would flood the capped list
    vanishes under a recent window, and the windowed totals count only
    in-window chains."""
    from cairn.dashboard.data import CHAINS_MAX_CHAINS, SESSION_GAP_S, get_session_chains

    cutoff = time.time() - 86400
    # A legacy-shape giant: 22 calls each > SESSION_GAP_S apart — 22 chains,
    # more than the chain-list cap — every one recorded before the edge.
    _seed_metrics(
        fresh_db,
        rows=[
            (
                i + 1,
                "explore",
                "unknown",
                cutoff - 60 - (22 - i) * (SESSION_GAP_S + 100),
                5.0,
                "ok",
                10,
                10,
            )
            for i in range(22)
        ],
    )
    # A small recent session, born inside the window.
    _seed_metrics(
        fresh_db,
        rows=[
            (101, "explore", "sess-now", cutoff + 10, 5.0, "ok", 10, 10),
            (102, "get_callers", "sess-now", cutoff + 20, 5.0, "ok", 10, 10),
        ],
    )

    # All time: the giant floods the list — sess-now's chain plus 19 of the
    # giant's 22 fit the cap, newest-activity-first, and the wrapper says so.
    all_time = get_session_chains(fresh_db)
    assert all_time["total_chains"] == 23
    assert all_time["truncated"] is True
    assert len(all_time["chains"]) == CHAINS_MAX_CHAINS
    assert all_time["chains"][0]["session_id"] == "sess-now"

    # Windowed: the giant has no in-window calls and vanishes before any
    # cap applies — the recent session is the entire result, untruncated.
    windowed = get_session_chains(fresh_db, since=cutoff)
    assert (windowed["total_chains"], windowed["truncated"]) == (1, False)
    (chain,) = windowed["chains"]
    assert chain["session_id"] == "sess-now"
    assert [c["id"] for c in chain["calls"]] == [101, 102]
    assert (chain["started_at"], chain["ended_at"]) == (cutoff + 10, cutoff + 20)


# ---------------------------------------------------------------------------
# Workspaces probe degradation (workspace-launcher FR-005 / T009, tech-spec
# D-003): probe_stores bounds the SQL opens; rows past the cap must surface
# a VISIBLE counts-unavailable state (call_count None + counts_capped True,
# the row the route renders as the em-dash under the cap line) while
# keeping the free os.stat fields — never a silent zero, never a hang.
# Data-layer calls over a synthesized CAIRN_HOME; the rendering of the same
# degradation is the app/workspaces suites' half.
# ---------------------------------------------------------------------------


def _ws_home(tmp_path):
    """An empty CAIRN_HOME the test populates store by store."""
    home = tmp_path / "cairn-home"
    home.mkdir()
    return home


def _ws_store(home, key, calls):
    """A real schema store at ``<home>/<key>/.kg`` with ``calls``
    tool_metrics rows — the app suite's _seed_store_db convention,
    duplicated because test modules are separately owned."""
    from cairn.graph.schema import get_db

    kg = home / key / ".kg"
    kg.parent.mkdir(parents=True, exist_ok=True)
    conn = get_db(str(kg))
    try:
        conn.executemany(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
            "duration_ms, status) VALUES (?, ?, ?, ?, ?)",
            [
                ("explore", "ws-degrade", 1755648000.0 + i, 50.0, "ok")
                for i in range(calls)
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return kg


def test_probe_stores_past_cap_degrades_counts_visibly_keeps_stats(tmp_path):
    """T009 / FR-005: more populated stores than the open budget → exactly
    max_opens rows carry a call count and they are the FIRST stores in
    list order; every past-cap row degrades visibly (call_count None +
    counts_capped True, state still populated) while size/freshness —
    D-003's free stat-first half — survive for every row."""
    from cairn.dashboard.workspaces import enumerate_stores, probe_stores

    home = _ws_home(tmp_path)
    # Four stores, keys sorting by construction, call counts identifying
    # each store (i calls for key i) so the probed pair is checkable
    # per-key, not just by count of non-None values.
    for i in range(4):
        _ws_store(home, f"{i:016x}", calls=i + 1)

    entries = enumerate_stores(home)
    assert [e["key"] for e in entries] == [f"{i:016x}" for i in range(4)]

    rows = probe_stores(home, entries, max_opens=2)

    # One row per entry, list order preserved (the list-order contract).
    assert [r["key"] for r in rows] == [e["key"] for e in entries]
    # Exactly the budget's worth of counts, on the FIRST two stores.
    assert [(r["key"], r["call_count"]) for r in rows[:2]] == [
        (f"{i:016x}", i + 1) for i in range(2)
    ]
    assert all(r["counts_capped"] is False for r in rows[:2])
    # Past the cap: counts unknown — None, never a silent 0 — flagged.
    for row in rows[2:]:
        assert row["state"] == "populated"  # budgeted, not broken
        assert row["call_count"] is None
        assert row["counts_capped"] is True
    # The free half never degrades: stats present on every row, capped too.
    for row in rows:
        assert row["size_bytes"] > 0
        assert row["last_modified"] is not None


def test_probe_stores_zero_opens_degrades_populated_only(tmp_path):
    """T009 / FR-005's floor: max_opens=0 degrades EVERY populated row
    (counts None + flagged, stats kept) while empty and missing rows are
    exactly what they were — those states never open a DB, so they are
    not capped, just count-less by nature (no .kg to stat either)."""
    from cairn.dashboard.workspaces import enumerate_stores, probe_stores

    home = _ws_home(tmp_path)
    _ws_store(home, "a000000000000001", calls=3)
    _ws_store(home, "b000000000000002", calls=1)
    (home / "c000000000000003").mkdir()  # empty: key dir, no .kg
    (home / "workspaces.json").write_text(
        json.dumps({str(tmp_path / "gone"): "d000000000000004"}),
        encoding="utf-8",
    )  # registered key, dir gone: missing

    rows = probe_stores(home, enumerate_stores(home), max_opens=0)

    by_key = {r["key"]: r for r in rows}
    for key in ("a000000000000001", "b000000000000002"):
        row = by_key[key]
        assert row["state"] == "populated"
        assert row["call_count"] is None
        assert row["counts_capped"] is True
        assert row["size_bytes"] > 0  # degradation costs counts, not stats
        assert row["last_modified"] is not None

    empty = by_key["c000000000000003"]
    assert empty["state"] == "empty"
    assert empty["call_count"] is None
    assert empty["counts_capped"] is False  # never opened, not capped
    assert empty["size_bytes"] is None  # no .kg to stat

    missing = by_key["d000000000000004"]
    assert missing["state"] == "missing"
    assert missing["call_count"] is None
    assert missing["counts_capped"] is False
    assert missing["size_bytes"] is None


def test_probe_stores_corrupt_first_store_fails_fast_batch_completes(tmp_path):
    """T009's no-hang half: a corrupt .kg at list position 1 fails its
    open fast — reclassified unreadable, stats kept — and CONSUMES one
    budget slot, yet the batch continues: the next store still gets its
    real count, the last degrades past the cap, and the call returns one
    row per entry in order. The corrupt open's sqlite3.Error is absorbed,
    never propagated; completing under the suite's own timeout is the
    bounded-work proof (no flaky timing assertion, per the scale suite's
    convention)."""
    from cairn.dashboard.workspaces import enumerate_stores, probe_stores

    home = _ws_home(tmp_path)
    # The junk store's key sorts first, so enumerate puts it at position 1.
    junk = home / "0000000000000001" / ".kg"
    junk.parent.mkdir(parents=True)
    junk.write_bytes(b"definitely not a sqlite database\n" * 8)
    _ws_store(home, "0000000000000002", calls=7)
    _ws_store(home, "0000000000000003", calls=5)

    entries = enumerate_stores(home)
    # The enumerator is filesystem-only: junk .kg is a file → populated.
    assert [e["state"] for e in entries] == ["populated"] * 3

    rows = probe_stores(home, entries, max_opens=2)

    # The whole batch returned, one row per entry, order preserved.
    assert [r["key"] for r in rows] == [e["key"] for e in entries]

    corrupt, probed, capped = rows
    assert corrupt["state"] == "unreadable"  # the probe's refinement
    assert corrupt["call_count"] is None
    assert corrupt["counts_capped"] is False  # it WAS allowed; the open failed
    assert corrupt["size_bytes"] == junk.stat().st_size  # stats survive
    assert corrupt["last_modified"] is not None
    # Budget accounting: corrupt (1) + this store (2) == max_opens, and
    # the middle store still got its real count — failing the first open
    # aborted nothing.
    assert probed["state"] == "populated"
    assert probed["call_count"] == 7
    assert probed["counts_capped"] is False
    # ...so the third store lands past the cap, visibly degraded.
    assert capped["state"] == "populated"
    assert capped["call_count"] is None
    assert capped["counts_capped"] is True


def test_probe_stores_empty_and_missing_consume_no_open_budget(tmp_path):
    """T009's accounting half: only populated rows draw from the open
    budget. Three empty dirs and a registered-missing key walk the list
    FIRST (entries reordered — probe_stores probes in the list order it is
    handed), and both populated stores behind them still get their counts
    under max_opens=2. Had the no-open states consumed slots, both
    populated rows would have come back capped."""
    from cairn.dashboard.workspaces import enumerate_stores, probe_stores

    home = _ws_home(tmp_path)
    _ws_store(home, "a000000000000001", calls=2)
    _ws_store(home, "b000000000000002", calls=4)
    for i in range(3):
        (home / f"c{i:015x}").mkdir()
    (home / "workspaces.json").write_text(
        json.dumps({str(tmp_path / "gone"): "d000000000000004"}),
        encoding="utf-8",
    )

    # enumerate's own order is populated-first (the budget would never be
    # contested); reorder so the four no-open states precede the populated
    # pair and the accounting is actually exercised.
    entries = enumerate_stores(home)
    entries = [e for e in entries if e["state"] != "populated"] + [
        e for e in entries if e["state"] == "populated"
    ]
    assert len(entries) == 6
    assert [e["state"] for e in entries[:4]] == ["empty"] * 3 + ["missing"]

    rows = probe_stores(home, entries, max_opens=2)

    assert [(r["key"], r["call_count"], r["counts_capped"]) for r in rows[-2:]] == [
        ("a000000000000001", 2, False),
        ("b000000000000002", 4, False),
    ]


# ---------------------------------------------------------------------------
# Tokenizer mode selection (ui-dashboard-polish FR-002 / TC-002, TC-003) and
# per-tool truncation surfacing (FR-003 / TC-005). The active mode is a
# process-wide singleton, so every mode test pins sys.modules["transformers"]
# itself (a deterministic stub for present, None for absent) and resets the
# singleton on entry AND exit -- a leaked exact-mode resolution would
# re-tokenize later tests' calibration samples on semantic-extra machines.
# ---------------------------------------------------------------------------

_STUB_MODEL = "stub/deterministic-tokenizer"
_STUB_CHARS_PER_TOKEN = 2


class _StubAutoTokenizer:
    """The exact-mode probe's tokenizer contract, deterministic on every
    machine: ``from_pretrained`` succeeds for any locally-available model
    and ``encode`` counts one token per 2 characters."""

    @staticmethod
    def from_pretrained(model, local_files_only=False):
        return _StubAutoTokenizer

    @staticmethod
    def encode(text, add_special_tokens=False):
        return [0] * (len(text) // _STUB_CHARS_PER_TOKEN)


@pytest.fixture
def exact_tokenizer_present(monkeypatch):
    """TC-002's precondition: the exact tokenizer importable and its model
    cached. sys.modules carries the deterministic stub, so the probe
    resolves exact mode without the semantic extra ever being installed."""
    from cairn.dashboard.tokenizer import reset_tokenizer_mode

    stub = types.ModuleType("transformers")
    stub.AutoTokenizer = _StubAutoTokenizer
    monkeypatch.setitem(sys.modules, "transformers", stub)
    monkeypatch.setenv("CAIRN_EMBED_LOCAL_MODEL", _STUB_MODEL)
    reset_tokenizer_mode()
    yield
    reset_tokenizer_mode()


@pytest.fixture
def tokenizer_import_absent(monkeypatch):
    """TC-003's precondition: the import absent. A None in sys.modules makes
    ``from transformers import AutoTokenizer`` raise ImportError -- the
    probe's absent-import path, not a failing tokenizer."""
    from cairn.dashboard.tokenizer import reset_tokenizer_mode

    monkeypatch.setitem(sys.modules, "transformers", None)
    reset_tokenizer_mode()
    yield
    reset_tokenizer_mode()


def _seed_calibratable_rows(conn, rows=6, summary_chars=200):
    """Uniform tool_metrics rows whose stored summaries total past the
    calibration floor (each length a multiple of the stub's chars/token, so
    the calibrated divisor lands exactly on 2), with equal sizes per row so
    every row's estimates agree under either mode's divisor."""
    conn.executemany(
        "INSERT INTO tool_metrics (id, tool_name, session_id, invoked_at, "
        "duration_ms, status, req_chars, resp_chars, args_summary) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                i + 1,
                "explore",
                "sess-a",
                1755500000.0 + i,
                5.0,
                "ok",
                400,
                800,
                "s" * summary_chars,
            )
            for i in range(rows)
        ],
    )
    conn.commit()


def _render_tokens_html(tools, window="all"):
    """Render the real tokens.html with the /tokens route's own context
    shape and template machinery (same directory, same mean filter); the
    request is a stub exposing only ``url.path``, which the window control
    reads."""
    from starlette.templating import Jinja2Templates

    from cairn.dashboard.app import _PACKAGE_DIR, _fmt_mean

    templates = Jinja2Templates(directory=str(_PACKAGE_DIR / "templates"))
    templates.env.filters["mean"] = _fmt_mean
    request = types.SimpleNamespace(url=types.SimpleNamespace(path="/tokens"))
    return templates.env.get_template("tokens.html").render(
        {
            "request": request,
            "tools": tools,
            "window": window,
            "store_key": "",
            "url_for": lambda name, path="": "#",
        }
    )


def _rendered_tool_row(html, tool):
    """The rendered ``<tr>`` chunk of one tool's tokens row, identified by
    the tool cell's link text."""
    for chunk in html.split("<tr>"):
        if f">{tool}</a>" in chunk:
            return chunk
    raise AssertionError(f"no rendered tokens row for {tool!r}")


def test_tokenizer_exact_mode_used_and_labeled_when_import_present(
    fresh_db, exact_tokenizer_present
):
    """TC-002 (FR-002): with an exact tokenizer available, the resolved mode
    names it, direct estimates count through it, and the window's estimates
    divide by its calibrated ratio -- in get_tool_tokens, in list_history,
    and in the rendered /tokens label."""
    from cairn.dashboard.data import get_tool_tokens, list_history
    from cairn.dashboard.tokenizer import active_tokenizer_mode, estimate_tokens

    assert active_tokenizer_mode() == f"exact ({_STUB_MODEL})"

    # The estimate path runs through the tokenizer, not chars // 4: 10 chars
    # are 5 stub tokens where the heuristic would say 2.
    assert estimate_tokens("x" * 10) == 5

    _seed_calibratable_rows(fresh_db)

    tokens = get_tool_tokens(fresh_db)
    assert tokens.token_mode == f"exact ({_STUB_MODEL})"
    assert (tokens.calibrated, tokens.chars_per_token) == (True, 2)
    (entry,) = tokens
    # 6 x (400 + 800) chars through the calibrated ~2 chars/token ratio.
    assert (entry["est_req_tokens"], entry["est_resp_tokens"]) == (1200, 2400)
    assert entry["total_tokens"] == 3600

    history = list_history(fresh_db)["rows"]
    assert len(history) == 6
    for row in history:
        assert (row["est_req_tokens"], row["est_resp_tokens"]) == (200, 400)

    html = _render_tokens_html(tokens)
    assert f"Estimation mode: exact ({_STUB_MODEL})" in html
    assert "calibrated at ~2 chars/token" in html


def test_tokenizer_heuristic_fallback_used_and_labeled_when_import_absent(
    fresh_db, tokenizer_import_absent
):
    """TC-003 (FR-002): import absent -> the heuristic mode resolves,
    estimates are the documented chars // 4 (the same corpus as the exact
    test, different numbers -- the estimates follow the mode), and the
    rendered label says heuristic with no calibration claim."""
    from cairn.dashboard.data import get_tool_tokens, list_history
    from cairn.dashboard.tokenizer import HEURISTIC_MODE, active_tokenizer_mode, estimate_tokens

    assert active_tokenizer_mode() == HEURISTIC_MODE == "heuristic (chars/4)"
    assert estimate_tokens("x" * 10) == 2

    _seed_calibratable_rows(fresh_db)

    tokens = get_tool_tokens(fresh_db)
    assert tokens.token_mode == "heuristic (chars/4)"
    assert (tokens.calibrated, tokens.chars_per_token) == (False, 4)
    (entry,) = tokens
    assert (entry["est_req_tokens"], entry["est_resp_tokens"]) == (600, 1200)
    assert entry["total_tokens"] == 1800

    history = list_history(fresh_db)["rows"]
    assert len(history) == 6
    for row in history:
        assert (row["est_req_tokens"], row["est_resp_tokens"]) == (100, 200)

    html = _render_tokens_html(tokens)
    # The trailing period closes the label with no calibration suffix.
    assert "Estimation mode: heuristic (chars/4)." in html


def test_exact_mode_below_calibration_floor_stays_uncalibrated(
    fresh_db, exact_tokenizer_present
):
    """The exact mode's honesty marker: no usable summary sample (under
    CALIBRATION_MIN_CHARS) keeps the heuristic divisor, and the rendered
    label says uncalibrated -- also why small corpora stay deterministic
    on tokenizer-equipped machines."""
    from cairn.dashboard.data import CALIBRATION_MIN_CHARS, get_tool_tokens

    _seed_calibratable_rows(fresh_db, rows=3, summary_chars=40)
    assert 3 * 40 < CALIBRATION_MIN_CHARS

    tokens = get_tool_tokens(fresh_db)
    assert tokens.token_mode == f"exact ({_STUB_MODEL})"
    assert (tokens.calibrated, tokens.chars_per_token) == (False, 4)
    (entry,) = tokens
    assert (entry["est_req_tokens"], entry["est_resp_tokens"]) == (300, 600)

    html = _render_tokens_html(tokens)
    assert "uncalibrated for this window" in html
    assert re.search(r"estimates use the\s+4 chars/token heuristic divisor", html)


# TC-005's mixed store: (id, tool, req_chars, resp_chars,
# truncated_from_chars, truncated_to_chars) -- every truncation-evidence
# shape at once.
_TRUNCATION_ROWS = [
    # search: two truncated calls carrying magnitudes, one untruncated.
    (1, "search", 400, 800, 2000, 500),
    (2, "search", 400, 800, 1000, 400),
    (3, "search", 400, 800, None, None),
    # summarize: truncated but nothing cut -- evidence with zero magnitude.
    (4, "summarize", 100, 600, 600, 600),
    # read_file: recorded by the current writer, never truncated.
    (5, "read_file", 200, 50, None, None),
    (6, "read_file", 200, 50, None, None),
]


def _seed_truncation_mixed(conn):
    """The mixed rows plus one legacy row recorded before the truncation
    columns existed. Summaries stay far under the calibration floor so the
    divisor is 4 in either mode -- the assertions hold with and without
    the semantic extra."""
    conn.executemany(
        "INSERT INTO tool_metrics (id, tool_name, session_id, invoked_at, "
        "duration_ms, status, req_chars, resp_chars, args_summary, "
        "truncated_from_chars, truncated_to_chars) "
        "VALUES (?, ?, 'sess-a', ?, 5.0, 'ok', ?, ?, '{\"q\": \"x\"}', ?, ?)",
        [
            (rid, tool, 1755500000.0 + rid, req, resp, frm, to)
            for rid, tool, req, resp, frm, to in _TRUNCATION_ROWS
        ],
    )
    conn.execute(
        "INSERT INTO tool_metrics (id, tool_name, session_id, invoked_at, "
        "duration_ms, status, req_chars, resp_chars, args_summary) "
        "VALUES (7, 'legacy_probe', 'sess-a', 1755500007.0, 5.0, 'ok', "
        "40, 80, '{\"q\": \"y\"}')"
    )
    conn.commit()


def test_get_tool_tokens_surfaces_per_tool_truncation_counts(fresh_db):
    """TC-005's data half (FR-003): truncated_calls/truncated_chars ride the
    per-tool entries from the durable columns -- magnitudes aggregate across
    the truncated calls only, absent evidence reads unknown (None, never
    0), and a zero-magnitude truncation reads 0, never unknown."""
    from cairn.dashboard.data import get_tool_tokens

    _seed_truncation_mixed(fresh_db)
    by_tool = {t["tool_name"]: t for t in get_tool_tokens(fresh_db)}

    search = by_tool["search"]
    assert (search["calls"], search["truncated_calls"], search["truncated_chars"]) == (
        3,
        2,
        2100,  # (2000 - 500) + (1000 - 400), the untruncated call adds nothing
    )
    # Usage renders alongside: 3 x (400 + 800) chars // 4.
    assert search["total_tokens"] == 900

    summarize = by_tool["summarize"]
    assert (summarize["truncated_calls"], summarize["truncated_chars"]) == (1, 0)

    for no_evidence in ("read_file", "legacy_probe"):
        tool = by_tool[no_evidence]
        assert tool["truncated_calls"] is None
        assert tool["truncated_chars"] is None


def test_tokens_render_distinguishes_truncation_unknown_from_zero(fresh_db):
    """TC-005's view half (FR-003): the rendered surface shows per-tool
    truncation counts alongside usage, renders no-evidence rows as
    unknown/-- and zero-magnitude evidence as 0, and the mode label names a
    real mode regardless of the machine's semantic extra."""
    from cairn.dashboard.data import get_tool_tokens

    _seed_truncation_mixed(fresh_db)
    html = _render_tokens_html(get_tool_tokens(fresh_db))

    assert re.search(r"Estimation mode: (exact \(|heuristic \(chars/4\))", html)

    search = _rendered_tool_row(html, "search")
    assert ">~900</td>" in search  # usage alongside the truncation counts
    assert ">2</td>" in search
    assert ">2100</td>" in search

    summarize = _rendered_tool_row(html, "summarize")
    assert ">1</td>" in summarize
    assert ">0</td>" in summarize  # zero magnitude is a number, not unknown
    assert "unknown" not in summarize

    for no_evidence in ("read_file", "legacy_probe"):
        row = _rendered_tool_row(html, no_evidence)
        assert ">unknown</td>" in row
        assert ">—</td>" in row


# ---------------------------------------------------------------------------
# Module-scope curation (graph-tab info panel): the empty-focus default shows
# connectivity-ranked production hubs instead of an arbitrary first-50 sample,
# and tests are excluded by default with an explicit opt-in.
# ---------------------------------------------------------------------------


def _seed_hubs(conn):
    """55 test symbols inserted first (rowid order), then 6 production
    symbols whose edges make hub_main the top hub. Under today's
    LIMIT-50-rowid sample the canvas is entirely test nodes."""
    conn.executemany(
        "INSERT INTO repos (id, name, path, language, indexed_at) VALUES (?, ?, ?, ?, ?)",
        [("hub", "hub", ".", "python", "2026-08-27T00:00:00")],
    )
    conn.executemany(
        "INSERT INTO files (id, repo_id, path, language, indexed_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("f_t1", "hub", "tests/test_pack.py", "python", "2026-08-27T00:00:00"),
            ("f_p1", "hub", "src/pack/core.py", "python", "2026-08-27T00:00:00"),
            ("f_p2", "hub", "src/pack/util.py", "python", "2026-08-27T00:00:00"),
        ],
    )
    rows = [
        (f"s_t{i}", "f_t1", f"test_case_{i:02d}",
         f"tests.test_pack.test_case_{i:02d}", "function")
        for i in range(55)
    ]
    rows += [
        ("s_p0", "f_p1", "hub_main", "pack.core.hub_main", "function"),
        ("s_p1", "f_p1", "in_a", "pack.core.in_a", "function"),
        ("s_p2", "f_p1", "in_b", "pack.core.in_b", "function"),
        ("s_p3", "f_p2", "out_a", "pack.util.out_a", "function"),
        ("s_p4", "f_p2", "out_b", "pack.util.out_b", "function"),
        ("s_p5", "f_p2", "pack_leaf", "pack.util.pack_leaf", "function"),
    ]
    conn.executemany(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, docstring) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [r + (None,) for r in rows[:-6]]
        + [r + ("the hub.",) for r in rows[-6:]],
    )
    conn.executemany(
        "INSERT INTO edges (id, source_id, target_id, kind) VALUES (?, ?, ?, ?)",
        [
            ("he1", "s_p0", "s_p3", "calls"),
            ("he2", "s_p0", "s_p4", "calls"),
            ("he3", "s_p1", "s_p0", "calls"),
            ("he4", "s_p2", "s_p0", "calls"),
            ("he5", "s_t0", "s_p0", "calls"),
        ],
    )
    conn.commit()


def test_module_default_excludes_tests_and_ranks_hubs(fresh_db):
    from cairn.dashboard.data import get_graph

    _seed_hubs(fresh_db)
    graph = get_graph(fresh_db)  # scope defaults to module, no focus

    ids = {n["id"] for n in graph["nodes"]}
    assert ids == {"hub_main", "in_a", "in_b", "out_a", "out_b", "pack_leaf"}
    assert graph["metadata"]["tests_included"] is False
    assert graph["metadata"]["node_count"] == 6
    assert graph["metadata"]["edge_count"] == 4  # he1-he4; he5's test source is dropped
    # Ranked by degree: the hub first, the unconnected leaf last.
    assert graph["nodes"][0]["id"] == "hub_main"
    assert graph["nodes"][-1]["id"] == "pack_leaf"


def test_module_scope_include_tests_opt_in(fresh_db):
    from cairn.dashboard.data import get_graph

    _seed_hubs(fresh_db)
    graph = get_graph(fresh_db, include_tests=True)

    ids = {n["id"] for n in graph["nodes"]}
    assert "test_case_00" in ids
    assert "hub_main" in ids
    assert graph["metadata"]["tests_included"] is True
    assert graph["metadata"]["truncated"] is True  # 61 candidates > the 50 cap


def test_module_scope_focus_filters_tests_too(fresh_db):
    from cairn.dashboard.data import get_graph

    _seed_hubs(fresh_db)
    # "pack" matches production AND test paths (tests/test_pack.py).
    default = get_graph(fresh_db, focus="pack")
    assert not any(n["id"].startswith("test_") for n in default["nodes"])
    assert "hub_main" in {n["id"] for n in default["nodes"]}

    opted_in = get_graph(fresh_db, focus="pack", include_tests=True)
    assert "test_case_00" in {n["id"] for n in opted_in["nodes"]}


def _seed_vendored(conn):
    """One minified asset whose internal calls give it the workspace's
    highest-degree symbols, one vendored dir, and one real hub."""
    conn.executemany(
        "INSERT INTO repos (id, name, path, language, indexed_at) VALUES (?, ?, ?, ?, ?)",
        [("v", "v", ".", "javascript", "2026-08-28T00:00:00")],
    )
    conn.executemany(
        "INSERT INTO files (id, repo_id, path, language, indexed_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("f_min", "v", "src/web/static/app.min.js", "javascript", "2026-08-28T00:00:00"),
            ("f_dist", "v", "dist/bundle/helpers.js", "javascript", "2026-08-28T00:00:00"),
            ("f_real", "v", "src/web/app.py", "python", "2026-08-28T00:00:00"),
        ],
    )
    rows = [
        ("s_min_a", "f_min", "append", "minified.append", "method"),
        ("s_min_b", "f_min", "emit", "minified.emit", "function"),
        ("s_dist", "f_dist", "dist_helper", "bundled.dist_helper", "function"),
        ("s_real_a", "f_real", "real_main", "web.app.real_main", "function"),
        ("s_real_b", "f_real", "real_helper", "web.app.real_helper", "function"),
    ]
    conn.executemany(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, docstring) "
        "VALUES (?, ?, ?, ?, ?, NULL)",
        rows,
    )
    # Degree 6+2 for the minified pair (workspace top), degree 1 for the hub.
    conn.executemany(
        "INSERT INTO edges (id, source_id, target_id, kind) VALUES (?, ?, ?, ?)",
        [
            ("ve1", "s_min_a", "s_min_b", "calls"),
            ("ve2", "s_min_a", "s_min_b", "calls"),
            ("ve3", "s_min_a", "s_min_b", "calls"),
            ("ve4", "s_min_b", "s_min_a", "calls"),
            ("ve5", "s_min_b", "s_min_a", "calls"),
            ("ve6", "s_min_b", "s_min_a", "calls"),
            ("ve7", "s_real_a", "s_real_b", "calls"),
            ("ve8", "s_dist", "s_real_a", "calls"),
        ],
    )
    conn.commit()


def test_module_scope_excludes_vendored_and_minified_symbols(fresh_db):
    from cairn.dashboard.data import get_graph

    _seed_vendored(fresh_db)
    graph = get_graph(fresh_db)

    ids = {n["id"] for n in graph["nodes"]}
    # The minified pair has the workspace's top degree but is never a
    # candidate; the dist/ dir is excluded by the same rule.
    assert "append" not in ids and "emit" not in ids and "dist_helper" not in ids
    assert ids == {"real_main", "real_helper"}
    assert graph["metadata"]["vendored_excluded"] == 3
    # The dist->real edge dies with its vendored source node.
    assert graph["metadata"]["edge_count"] == 1


def test_module_scope_dedupes_cross_repo_parallel_edges(fresh_db):
    from cairn.dashboard.data import get_graph

    # Same bare names in two repos: the name-based edge join sees three
    # rows for ONE logical edge, plus a distinct kind that must survive.
    fresh_db.executemany(
        "INSERT INTO repos (id, name, path, language, indexed_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("r1", "r1", ".", "python", "2026-08-28T00:00:00"),
            ("r2", "r2", ".", "python", "2026-08-28T00:00:00"),
        ],
    )
    fresh_db.executemany(
        "INSERT INTO files (id, repo_id, path, language, indexed_at) VALUES (?, ?, ?, ?, ?)",
        [
            ("f_r1", "r1", "src/one.py", "python", "2026-08-28T00:00:00"),
            ("f_r2", "r2", "src/two.py", "python", "2026-08-28T00:00:00"),
        ],
    )
    fresh_db.executemany(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind, docstring) "
        "VALUES (?, ?, ?, ?, ?, NULL)",
        [
            ("s_a1", "f_r1", "caller", "one.caller", "function"),
            ("s_b1", "f_r1", "helper", "one.helper", "function"),
            ("s_a2", "f_r2", "caller", "two.caller", "function"),
            ("s_b2", "f_r2", "helper", "two.helper", "function"),
        ],
    )
    fresh_db.executemany(
        "INSERT INTO edges (id, source_id, target_id, kind) VALUES (?, ?, ?, ?)",
        [
            ("pe1", "s_a1", "s_b1", "calls"),
            ("pe2", "s_a2", "s_b2", "calls"),
            ("pe3", "s_a1", "s_b2", "calls"),
            ("pe4", "s_a2", "s_b1", "imports"),
        ],
    )
    fresh_db.commit()

    graph = get_graph(fresh_db)
    # One calls edge caller->helper; the distinct-kind imports edge stays.
    assert graph["metadata"]["edge_count"] == 2
    kinds = sorted((e["source"], e["target"], e["kind"]) for e in graph["edges"])
    assert kinds == [("caller", "helper", "calls"), ("caller", "helper", "imports")]


# ---------------------------------------------------------------------------
# Symbol inspect payload (graph-tab side panel): one call answering
# "what is this, what feeds it, what does it touch, what breaks".
# ---------------------------------------------------------------------------


def test_inspect_symbol_returns_panel_payload(fresh_db):
    from cairn.dashboard.data import inspect_symbol

    _seed_hubs(fresh_db)
    data = inspect_symbol(fresh_db, "hub_main")

    assert data["found"] is True
    assert data["symbol"]["name"] == "hub_main"
    assert data["symbol"]["kind"] == "function"
    assert data["symbol"]["file"] == "src/pack/core.py"
    assert data["symbol"]["docstring"] == "the hub."
    assert {c["name"] for c in data["callers"]} >= {"in_a", "in_b", "test_case_00"}
    assert {c["name"] for c in data["callees"]} >= {"out_a", "out_b"}
    assert data["impact"]["total"] >= 2
    tests = {t["symbol"] for t in data["impact"]["affected_tests"]}
    assert "test_case_00" in tests
    # The ambiguous-name escape hatch: same-name rows listed for disambiguation.
    assert data["same_name_count"] == 1


def test_inspect_symbol_unknown_name_reports_not_found(fresh_db):
    from cairn.dashboard.data import inspect_symbol

    _seed_hubs(fresh_db)
    assert inspect_symbol(fresh_db, "no_such_symbol") == {
        "found": False,
        "name": "no_such_symbol",
    }


# ---------------------------------------------------------------------------
# Dashboard wiki panel (FR-009 / US6): manifest rows joined with the
# promoted wiki/pages/ concepts, rendered bodies, and sources.
# ---------------------------------------------------------------------------

_WIKI_BODY = (
    "## How it works\n"
    "\n"
    "The pipeline runs in <two> phases & one pass.\n"
    "\n"
    "- first the catalog\n"
    "- then the pages\n"
    "\n"
    "### Diagram\n"
    "\n"
    "```mermaid\n"
    "graph LR\n"
    "  A --> B\n"
    "```\n"
)
_WIKI_SOURCES = [{"path": "src/demo/core.py"}, {"symbol": "demo_main"}]


def _plan_entry(page_id, title):
    """One deterministic plan entry, the shape manifest rows carry."""
    return {
        "page_id": page_id,
        "title": title,
        "description": f"Wiki page for {page_id}.",
        "module": "src/demo",
        "seeds": {"files": ["src/demo/core.py"], "symbols": ["demo_main"]},
        "input_hash": f"hash-{page_id}",
    }


def _manifest_row(entry, task_id, state, attempts=0):
    return {**entry, "task_id": task_id, "state": state, "attempts": attempts}


def _write_manifest(knowledge_dir, pages):
    from pathlib import Path

    wiki_dir = Path(knowledge_dir) / "_wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    doc = {"schema": "cairn-wiki-manifest-2", "pages": pages}
    (wiki_dir / "manifest.json").write_text(
        json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _promote_concept(bundle, repo, page_id):
    from cairn.okf.concept import OKFConcept

    bundle.write_concept(
        OKFConcept(
            type="Wiki-Article",
            title=f"Wiki: {page_id}",
            description=f"Wiki article for {repo}/{page_id}",
            resource=page_id,
            tags=[repo, "wiki"],
            timestamp="2026-08-30T10:00:00Z",
            concept_id=f"wiki/pages/{repo}/{page_id}",
            sources=_WIKI_SOURCES,
            body=_WIKI_BODY,
            extensions={"page_id": page_id, "input_hash": f"hash-{page_id}"},
        )
    )


def _seed_wiki(knowledge_dir):
    """Three manifest pages: two with promoted concepts, one queued (no
    concept on disk)."""
    from cairn.okf.bundle import OKFBundle

    bundle = OKFBundle(str(knowledge_dir))
    _promote_concept(bundle, "demo", "overview")
    _promote_concept(bundle, "demo", "viz-module")
    _write_manifest(
        knowledge_dir,
        {
            "demo/overview": _manifest_row(
                _plan_entry("overview", "demo architecture overview"),
                task_id="task-overview",
                state="promoted",
            ),
            "demo/viz-module": _manifest_row(
                _plan_entry("viz-module", "viz module"),
                task_id="task-viz",
                state="promoted",
            ),
            "demo/tasks-module": _manifest_row(
                _plan_entry("tasks-module", "tasks module"),
                task_id="task-tasks",
                state="queued",
            ),
        },
    )


def test_get_wiki_pages_joins_manifest_with_promoted_concepts(tmp_path):
    from cairn.dashboard.data import get_wiki_pages

    kdir = tmp_path / "knowledge"
    _seed_wiki(kdir)

    pages = get_wiki_pages(str(kdir))

    by_id = {p["page_id"]: p for p in pages}
    assert set(by_id) == {"overview", "viz-module", "tasks-module"}
    assert by_id["overview"]["title"] == "demo architecture overview"
    assert by_id["overview"]["state"] == "promoted"
    assert by_id["overview"]["promoted"] is True
    assert by_id["viz-module"]["promoted"] is True
    assert by_id["tasks-module"]["state"] == "queued"
    assert by_id["tasks-module"]["promoted"] is False
    # An explicit repo selects pages under wiki/pages/{repo}/.
    assert {
        p["page_id"] for p in get_wiki_pages(str(kdir), repo="demo")
    } == {"overview", "viz-module", "tasks-module"}


def test_get_wiki_pages_promoted_derived_from_concept_not_stored_state(tmp_path):
    from cairn.dashboard.data import get_wiki_pages

    kdir = tmp_path / "knowledge"
    _write_manifest(
        kdir,
        {
            "demo/ghost": _manifest_row(
                _plan_entry("ghost", "ghost page"),
                task_id="task-ghost",
                state="promoted",
            ),
        },
    )

    pages = get_wiki_pages(str(kdir))

    assert len(pages) == 1
    assert pages[0]["page_id"] == "ghost"
    assert pages[0]["promoted"] is False


def test_get_wiki_pages_skips_unreadable_concept_files(tmp_path):
    from cairn.dashboard.data import get_wiki_pages

    kdir = tmp_path / "knowledge"
    _seed_wiki(kdir)
    (kdir / "wiki" / "pages" / "demo" / "viz-module.md").write_bytes(b"\xff\xfe\x00")

    pages = get_wiki_pages(str(kdir))

    by_id = {p["page_id"]: p for p in pages}
    assert by_id["overview"]["promoted"] is True
    assert by_id["viz-module"]["promoted"] is False


def test_get_wiki_pages_missing_dir_returns_empty(tmp_path):
    from cairn.dashboard.data import get_wiki_pages

    assert get_wiki_pages(str(tmp_path / "nope")) == []


def test_get_wiki_page_renders_body_and_carries_sources(tmp_path):
    from cairn.dashboard.data import get_wiki_page

    kdir = tmp_path / "knowledge"
    _seed_wiki(kdir)

    page = get_wiki_page(str(kdir), "overview")

    assert page is not None
    assert page["title"] == "demo architecture overview"
    assert page["state"] == "promoted"
    assert page["sources"] == _WIKI_SOURCES
    html = page["html"]
    assert re.search(r"<h2[^>]*>How it works</h2>", html)
    assert re.search(r"<h3[^>]*>Diagram</h3>", html)
    assert "<li>first the catalog</li>" in html
    assert "##" not in html  # rendered, never the raw markdown source
    assert "&lt;two&gt;" in html  # escape-first: inline markup never passes


def test_get_wiki_page_missing_page_or_dir_returns_none(tmp_path):
    from cairn.dashboard.data import get_wiki_page

    kdir = tmp_path / "knowledge"
    _seed_wiki(kdir)

    assert get_wiki_page(str(kdir), "no-such-page") is None
    assert get_wiki_page(str(tmp_path / "nope"), "overview") is None


# ---------------------------------------------------------------------------
# Wiki staleness (FR-007 / D-020): recorded sha (concept extensions, manifest
# row fallback) vs the workspace HEAD, as fresh/stale/unknown.
# ---------------------------------------------------------------------------

_SHA_A = "abc1234a"
_SHA_B = "def5678b"


def _promote_concept_with_sha(bundle, repo, page_id, sha=None):
    """The FR-009 promoted concept, plus the recorded-sha extension."""
    from cairn.okf.concept import OKFConcept

    extensions = {"page_id": page_id, "input_hash": f"hash-{page_id}"}
    if sha:
        extensions["commit_sha"] = sha
    bundle.write_concept(
        OKFConcept(
            type="Wiki-Article",
            title=f"Wiki: {page_id}",
            description=f"Wiki article for {repo}/{page_id}",
            resource=page_id,
            tags=[repo, "wiki"],
            timestamp="2026-08-30T10:00:00Z",
            concept_id=f"wiki/pages/{repo}/{page_id}",
            sources=_WIKI_SOURCES,
            body=_WIKI_BODY,
            extensions=extensions,
        )
    )


def _fake_head(monkeypatch, head):
    """HEAD resolution is read through the ``cairn.dashboard.data``
    namespace seam (the module-level re-export of
    ``utils.git.get_repo_head``)."""
    monkeypatch.setattr(
        "cairn.dashboard.data.get_repo_head",
        lambda repo, workspace=None: head,
        raising=False,
    )


def test_get_wiki_pages_gain_staleness_stale_and_unknown(tmp_path, monkeypatch):
    """Every page dict carries ``staleness``: a concept sha behind the HEAD
    reads stale; a page with no recorded sha anywhere reads unknown."""
    from cairn.dashboard.data import get_wiki_pages
    from cairn.okf.bundle import OKFBundle

    kdir = tmp_path / "knowledge"
    bundle = OKFBundle(str(kdir))
    _promote_concept_with_sha(bundle, "demo", "overview", sha=_SHA_A)
    _promote_concept_with_sha(bundle, "demo", "viz-module", sha=_SHA_A)
    _write_manifest(
        kdir,
        {
            "demo/overview": _manifest_row(
                _plan_entry("overview", "overview"),
                task_id="t-overview",
                state="promoted",
            ),
            "demo/viz-module": _manifest_row(
                _plan_entry("viz-module", "viz"),
                task_id="t-viz",
                state="promoted",
            ),
            "demo/tasks-module": _manifest_row(
                _plan_entry("tasks-module", "tasks"),
                task_id="t-tasks",
                state="queued",
            ),
        },
    )
    _fake_head(monkeypatch, _SHA_B)

    pages = get_wiki_pages(str(kdir))

    staleness = {p["page_id"]: p["staleness"] for p in pages}
    assert staleness["overview"] == "stale"
    assert staleness["viz-module"] == "stale"
    assert staleness["tasks-module"] == "unknown"


def test_get_wiki_pages_staleness_fresh_when_head_matches(tmp_path, monkeypatch):
    from cairn.dashboard.data import get_wiki_pages
    from cairn.okf.bundle import OKFBundle

    kdir = tmp_path / "knowledge"
    bundle = OKFBundle(str(kdir))
    _promote_concept_with_sha(bundle, "demo", "overview", sha=_SHA_A)
    _write_manifest(
        kdir,
        {
            "demo/overview": _manifest_row(
                _plan_entry("overview", "overview"),
                task_id="t-overview",
                state="promoted",
            ),
        },
    )
    _fake_head(monkeypatch, _SHA_A)

    pages = get_wiki_pages(str(kdir))

    assert [p["staleness"] for p in pages] == ["fresh"]


def test_get_wiki_pages_staleness_unknown_when_head_unavailable(
    tmp_path, monkeypatch
):
    """A recorded sha with an unresolvable HEAD is unknown, never
    fresh/stale."""
    from cairn.dashboard.data import get_wiki_pages
    from cairn.okf.bundle import OKFBundle

    kdir = tmp_path / "knowledge"
    bundle = OKFBundle(str(kdir))
    _promote_concept_with_sha(bundle, "demo", "overview", sha=_SHA_A)
    _write_manifest(
        kdir,
        {
            "demo/overview": _manifest_row(
                _plan_entry("overview", "overview"),
                task_id="t-overview",
                state="promoted",
            ),
        },
    )
    _fake_head(monkeypatch, None)

    pages = get_wiki_pages(str(kdir))

    assert pages[0]["staleness"] == "unknown"


def test_get_wiki_pages_staleness_falls_back_to_manifest_row_sha(
    tmp_path, monkeypatch
):
    """A not-yet-promoted page has no concept, so its recorded sha comes
    from the manifest row's ``commit_sha``."""
    from cairn.dashboard.data import get_wiki_pages

    kdir = tmp_path / "knowledge"
    _write_manifest(
        kdir,
        {
            "demo/tasks-module": {
                **_manifest_row(
                    _plan_entry("tasks-module", "tasks"),
                    task_id="t-tasks",
                    state="queued",
                ),
                "commit_sha": _SHA_A,
            },
        },
    )
    _fake_head(monkeypatch, _SHA_A)

    pages = get_wiki_pages(str(kdir))

    assert pages[0]["staleness"] == "fresh"

    _fake_head(monkeypatch, _SHA_B)
    pages = get_wiki_pages(str(kdir))

    assert pages[0]["staleness"] == "stale"


def test_get_wiki_page_gain_staleness(tmp_path, monkeypatch):
    from cairn.dashboard.data import get_wiki_page
    from cairn.okf.bundle import OKFBundle

    kdir = tmp_path / "knowledge"
    bundle = OKFBundle(str(kdir))
    _promote_concept_with_sha(bundle, "demo", "overview", sha=_SHA_A)
    _write_manifest(
        kdir,
        {
            "demo/overview": _manifest_row(
                _plan_entry("overview", "overview"),
                task_id="t-overview",
                state="promoted",
            ),
        },
    )
    _fake_head(monkeypatch, _SHA_A)

    page = get_wiki_page(str(kdir), "overview")

    assert page is not None
    assert page["staleness"] == "fresh"

    _fake_head(monkeypatch, _SHA_B)
    page = get_wiki_page(str(kdir), "overview")

    assert page is not None
    assert page["staleness"] == "stale"
