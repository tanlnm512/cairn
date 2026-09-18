"""Tests for the pack pipeline: item model, cost hook, seed stage, expansion,
rank stage, fit stage, emitter, entry.

Imports only ``cairn.pack`` and its collaborators at module level; never
``cairn.cli`` or ``cairn.mcp_server`` (test-isolation rule).
"""
from __future__ import annotations

import sqlite3

import pytest


def _seed_pack_symbols(conn: sqlite3.Connection) -> None:
    """Insert a small symbol set and rebuild the FTS index."""
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES ('test', 'test', '/tmp/test')"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) "
        "VALUES ('f1', 'test', '/tmp/test/backoff.py', 'python')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES ('s1', 'f1', 'retry_backoff_policy', 'function', 'core.retry_backoff_policy', 1, 40)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES ('s2', 'f1', 'compute_backoff_delay', 'function', 'core.compute_backoff_delay', 42, 60)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES ('s3', 'f1', 'unrelated_widget', 'class', 'gui.unrelated_widget', 62, 90)"
    )
    try:
        conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    except sqlite3.OperationalError:
        pass  # FTS5 not available in this build
    conn.commit()


def test_build_pack_rejects_blank_task(fresh_db):
    from cairn.pack import build_pack

    for blank in ("", "   ", "\t\n"):
        with pytest.raises(ValueError, match="--task"):
            build_pack(fresh_db, None, blank, 1000)


def test_build_pack_strips_surrounding_whitespace(fresh_db):
    from cairn.pack import build_pack

    result = build_pack(fresh_db, None, "  retry_backoff_policy  ", 1000)
    assert result.task == "retry_backoff_policy"


def test_seed_lexical_fallback_without_embeddings(fresh_db):
    from cairn.pack import build_pack

    _seed_pack_symbols(fresh_db)
    result = build_pack(fresh_db, None, "retry_backoff_policy", 1500)
    assert [s.symbol_id for s in result.symbols] == ["s1"]
    assert result.symbols[0].is_seed is True
    assert result.symbols[0].name == "retry_backoff_policy"
    assert result.symbols[0].qualified_name == "core.retry_backoff_policy"
    assert result.symbols[0].file_path == "/tmp/test/backoff.py"
    assert [(i.kind, i.rank) for i in result.items] == [("source", 1)]


def test_seed_semantic_path_pins_rerank_off(fresh_db, monkeypatch):
    import cairn.pack as pack

    hits = [
        {
            "id": "s7",
            "name": "semantic_hit",
            "kind": "function",
            "qualified_name": "mod.semantic_hit",
            "file_path": "p.py",
            "score": 0.9,
        }
    ]
    seen = {}

    def _fake_semantic(conn, query, limit=20, rerank=None):
        seen["args"] = {"limit": limit, "rerank": rerank}
        return hits

    monkeypatch.setattr(pack, "_embeddings_present", lambda conn: True)
    monkeypatch.setattr(pack, "semantic_search", _fake_semantic)
    result = pack.build_pack(fresh_db, None, "semantic hit", 1000)
    assert seen["args"] == {"limit": pack.SEMANTIC_SEED_LIMIT, "rerank": False}
    assert [s.symbol_id for s in result.symbols] == ["s7"]
    assert result.symbols[0].name == "semantic_hit"


def test_seed_dedups_preserving_first_order(fresh_db, monkeypatch):
    import cairn.pack as pack

    def _hit(symbol_id, name):
        return {
            "id": symbol_id,
            "name": name,
            "kind": "function",
            "qualified_name": f"m.{name}",
            "file_path": f"{name}.py",
        }

    hits = [_hit("s2", "b"), _hit("s1", "a"), _hit("s2", "b")]
    monkeypatch.setattr(pack, "_embeddings_present", lambda conn: True)
    monkeypatch.setattr(
        pack, "semantic_search", lambda conn, q, limit=20, rerank=None: hits
    )
    seeds = pack.seed_symbols(fresh_db, "anything")
    assert [s.symbol_id for s in seeds] == ["s2", "s1"]


def test_seed_union_adds_lexical_anchors_after_semantic_hits(fresh_db, monkeypatch):
    import cairn.pack as pack

    _seed_pack_symbols(fresh_db)
    hits = [_fake_hit("x1", "semantic_noise")]
    monkeypatch.setattr(pack, "_embeddings_present", lambda conn: True)
    monkeypatch.setattr(
        pack, "semantic_search", lambda conn, q, limit=20, rerank=None: hits
    )
    seeds = pack.seed_symbols(fresh_db, "compute backoff delay")
    ids = [s.symbol_id for s in seeds]
    assert ids[0] == "x1"
    assert "s1" in ids and "s2" in ids
    assert "s3" not in ids


def test_seed_union_keeps_anchors_under_a_full_semantic_list(fresh_db, monkeypatch):
    import cairn.pack as pack

    _seed_pack_symbols(fresh_db)
    hits = [_fake_hit(f"x{i}", f"noise_{i}") for i in range(pack.SEED_LIMIT)]
    monkeypatch.setattr(pack, "_embeddings_present", lambda conn: True)
    monkeypatch.setattr(
        pack, "semantic_search", lambda conn, q, limit=20, rerank=None: hits
    )
    seeds = pack.seed_symbols(fresh_db, "retry backoff")
    ids = [s.symbol_id for s in seeds]
    assert len(ids) <= pack.SEED_LIMIT
    assert {"s1", "s2"} & set(ids)
    semantic_ids = [i for i in ids if i.startswith("x")]
    assert ids[: len(semantic_ids)] == semantic_ids
    assert ids[len(semantic_ids):] == [i for i in ids if not i.startswith("x")]


def test_seed_union_dedups_leg_overlap_preserving_order(fresh_db, monkeypatch):
    import cairn.pack as pack

    _seed_pack_symbols(fresh_db)
    hits = [_fake_hit("s2", "compute_backoff_delay")]
    monkeypatch.setattr(pack, "_embeddings_present", lambda conn: True)
    monkeypatch.setattr(
        pack, "semantic_search", lambda conn, q, limit=20, rerank=None: hits
    )
    seeds = pack.seed_symbols(fresh_db, "retry backoff")
    assert [s.symbol_id for s in seeds] == ["s2", "s1"]


def test_seed_union_is_deterministic_across_calls(fresh_db, monkeypatch):
    import cairn.pack as pack

    _seed_pack_symbols(fresh_db)
    hits = [_fake_hit("x1", "semantic_noise")]
    monkeypatch.setattr(pack, "_embeddings_present", lambda conn: True)
    monkeypatch.setattr(
        pack, "semantic_search", lambda conn, q, limit=20, rerank=None: hits
    )
    first = pack.seed_symbols(fresh_db, "retry backoff")
    second = pack.seed_symbols(fresh_db, "retry backoff")
    assert first == second


def test_pack_item_rejects_unknown_kind():
    from cairn.pack import PackItem

    with pytest.raises(ValueError, match="kind"):
        PackItem(kind="snippet", rank=0, text="x", cost=1)


def test_item_kind_vocabulary_covers_content_kinds():
    from cairn.pack import ITEM_KINDS

    assert ITEM_KINDS == frozenset({"source", "blast-radius", "compass", "memory"})


def test_item_cost_rounds_up_under_heuristic_mode(monkeypatch):
    from cairn.dashboard import tokenizer
    from cairn.pack import KIND_SOURCE, PackItem

    monkeypatch.setattr(tokenizer, "_mode", tokenizer.HEURISTIC_MODE)
    monkeypatch.setattr(tokenizer, "_tokenizer", None)
    assert PackItem.create(KIND_SOURCE, 0, "x" * 5).cost == 2
    assert PackItem.create(KIND_SOURCE, 0, "x" * 8).cost == 2
    assert PackItem.create(KIND_SOURCE, 0, "").cost == 0


def test_item_cost_uses_exact_count_when_tokenizer_active(monkeypatch):
    from cairn.dashboard import tokenizer
    from cairn.pack import KIND_MEMORY, PackItem

    class _Stub:
        def encode(self, text, add_special_tokens=False):
            return [0] * 3

    monkeypatch.setattr(tokenizer, "_mode", "exact (stub)")
    monkeypatch.setattr(tokenizer, "_tokenizer", _Stub())
    assert PackItem.create(KIND_MEMORY, 0, "any text").cost == 3


def test_build_pack_deterministic_across_runs(fresh_db):
    from cairn.pack import build_pack

    _seed_pack_symbols(fresh_db)
    first = build_pack(fresh_db, None, "retry_backoff_policy", 1500)
    second = build_pack(fresh_db, None, "retry_backoff_policy", 1500)
    assert first == second


def _fake_hit(symbol_id, name, kind="function"):
    return {
        "id": symbol_id,
        "name": name,
        "kind": kind,
        "qualified_name": f"m.{name}",
        "file_path": f"{name}.py",
    }


def _add_symbol(conn, symbol_id, name, kind="function"):
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (?, 'f1', ?, ?, ?, 100, 120)",
        (symbol_id, name, kind, f"core.{name}"),
    )


def _link(conn, edges):
    conn.executemany(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind) "
        "VALUES (?, ?, ?, ?, ?)",
        edges,
    )
    conn.commit()


def test_expansion_reaches_callers_and_callees(fresh_db):
    from cairn.pack import build_pack

    _seed_pack_symbols(fresh_db)
    _add_symbol(fresh_db, "s4", "run_retry_loop")
    _add_symbol(fresh_db, "s5", "load_retry_config")
    _link(
        fresh_db,
        [
            ("e1", "s4", "s1", "retry_backoff_policy", "calls"),
            ("e2", "s1", "s5", "load_retry_config", "calls"),
        ],
    )
    result = build_pack(fresh_db, None, "retry_backoff_policy", 1500)
    by_id = {s.symbol_id: s for s in result.symbols}
    assert set(by_id) == {"s1", "s4", "s5"}
    assert by_id["s1"].is_seed is True
    assert by_id["s4"].is_seed is False
    assert by_id["s5"].is_seed is False
    assert by_id["s4"].name == "run_retry_loop"
    assert by_id["s4"].qualified_name == "core.run_retry_loop"
    assert by_id["s5"].file_path == "/tmp/test/backoff.py"


def test_expansion_requires_precise_resolution(fresh_db):
    from cairn.pack import build_pack

    _seed_pack_symbols(fresh_db)
    _add_symbol(fresh_db, "s4", "name_only_caller")
    _link(
        fresh_db,
        [
            ("e1", "s4", None, "retry_backoff_policy", "calls"),
            ("e2", "s1", None, "external_helper", "calls"),
        ],
    )
    result = build_pack(fresh_db, None, "retry_backoff_policy", 1500)
    assert [s.symbol_id for s in result.symbols] == ["s1"]


def test_expansion_follows_call_edges_only(fresh_db):
    from cairn.pack import build_pack

    _seed_pack_symbols(fresh_db)
    _add_symbol(fresh_db, "s4", "subclass_widget", kind="class")
    _add_symbol(fresh_db, "s5", "http_client")
    _link(
        fresh_db,
        [
            ("e1", "s4", "s1", "retry_backoff_policy", "extends"),
            ("e2", "s5", "s1", "retry_backoff_policy", "http_call"),
        ],
    )
    result = build_pack(fresh_db, None, "retry_backoff_policy", 1500)
    assert [s.symbol_id for s in result.symbols] == ["s1"]


def test_expansion_caps_neighbors_per_seed(fresh_db):
    import cairn.pack as pack
    from cairn.pack import build_pack

    _seed_pack_symbols(fresh_db)
    total = pack.EXPAND_LIMIT + 2
    for i in range(total):
        _add_symbol(fresh_db, f"c{i}", f"caller_{i}")
    _link(
        fresh_db,
        [
            (f"e{i}", f"c{i}", "s1", "retry_backoff_policy", "calls")
            for i in range(total)
        ],
    )
    result = build_pack(fresh_db, None, "retry_backoff_policy", 4000)
    expanded = [s for s in result.symbols if not s.is_seed]
    assert len(expanded) == pack.EXPAND_LIMIT


def test_expansion_dedups_across_seeds_keeping_seed_flag(fresh_db, monkeypatch):
    import cairn.pack as pack

    _seed_pack_symbols(fresh_db)
    _add_symbol(fresh_db, "s4", "shared_neighbor")
    _link(
        fresh_db,
        [
            ("e1", "s1", "s4", "shared_neighbor", "calls"),
            ("e2", "s4", "s2", "compute_backoff_delay", "calls"),
            ("e3", "s1", "s2", "compute_backoff_delay", "calls"),
        ],
    )
    hits = [_fake_hit("s1", "retry_backoff_policy"), _fake_hit("s2", "compute_backoff_delay")]
    monkeypatch.setattr(pack, "_embeddings_present", lambda conn: True)
    monkeypatch.setattr(
        pack, "semantic_search", lambda conn, q, limit=20, rerank=None: hits
    )
    result = pack.build_pack(fresh_db, None, "backoff", 1500)
    assert [(s.symbol_id, s.is_seed) for s in result.symbols] == [
        ("s2", True),
        ("s1", True),
        ("s4", False),
    ]


def _add_closure_rows(conn, rows):
    conn.executemany(
        "INSERT INTO transitive_edges (source_id, target_name, target_id, distance) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()


def test_rank_orders_by_closure_centrality(fresh_db):
    from cairn.pack import build_pack

    _seed_pack_symbols(fresh_db)
    _add_symbol(fresh_db, "s4", "hub_consumer")
    _add_symbol(fresh_db, "s5", "peripheral_helper")
    _link(
        fresh_db,
        [
            ("e1", "s4", "s1", "retry_backoff_policy", "calls"),
            ("e2", "s5", "s1", "retry_backoff_policy", "calls"),
            ("e3", "s1", "s5", "peripheral_helper", "calls"),
        ],
    )
    _add_closure_rows(
        fresh_db,
        [
            ("x1", "hub_consumer", "s4", 2),
            ("x2", "hub_consumer", "s4", 2),
            ("x3", "hub_consumer", "s4", 3),
            ("x4", "peripheral_helper", "s5", 2),
            ("x5", "retry_backoff_policy", "s1", 2),
        ],
    )
    result = build_pack(fresh_db, None, "retry_backoff_policy", 4000)
    assert [s.symbol_id for s in result.symbols] == ["s1", "s4", "s5"]


def test_rank_seeds_precede_higher_centrality_neighbors(fresh_db):
    from cairn.pack import build_pack

    _seed_pack_symbols(fresh_db)
    _add_symbol(fresh_db, "s4", "hub_consumer")
    _link(fresh_db, [("e1", "s4", "s1", "retry_backoff_policy", "calls")])
    _add_closure_rows(
        fresh_db,
        [
            ("x1", "hub_consumer", "s4", 1),
            ("x2", "hub_consumer", "s4", 2),
            ("x3", "hub_consumer", "s4", 3),
            ("x4", "retry_backoff_policy", "s1", 2),
        ],
    )
    result = build_pack(fresh_db, None, "retry_backoff_policy", 4000)
    assert [s.symbol_id for s in result.symbols] == ["s1", "s4"]
    assert result.symbols[0].is_seed is True


def test_rank_falls_back_to_structural_indegree_without_closure(fresh_db):
    from cairn.pack import build_pack

    _seed_pack_symbols(fresh_db)
    _add_symbol(fresh_db, "s4", "structural_hub")
    _add_symbol(fresh_db, "s5", "service_only_hub")
    _link(
        fresh_db,
        [
            ("e1", "s4", "s1", "retry_backoff_policy", "calls"),
            ("e2", "s5", "s1", "retry_backoff_policy", "calls"),
            ("e3", "x1", "s4", "structural_hub", "calls"),
            ("e4", "x2", "s5", "service_only_hub", "http_call"),
            ("e5", "x3", "s5", "service_only_hub", "http_call"),
        ],
    )
    result = build_pack(fresh_db, None, "retry_backoff_policy", 4000)
    assert [s.symbol_id for s in result.symbols] == ["s1", "s4", "s5"]


def test_rank_breaks_ties_by_qualified_name(fresh_db, monkeypatch):
    import cairn.pack as pack

    hits = [_fake_hit("sb", "beta"), _fake_hit("sa", "alpha")]
    monkeypatch.setattr(pack, "_embeddings_present", lambda conn: True)
    monkeypatch.setattr(
        pack, "semantic_search", lambda conn, q, limit=20, rerank=None: hits
    )
    result = pack.build_pack(fresh_db, None, "anything", 1000)
    assert [s.qualified_name for s in result.symbols] == ["m.alpha", "m.beta"]


def _pin_heuristic_tokenizer(monkeypatch):
    from cairn.dashboard import tokenizer

    monkeypatch.setattr(tokenizer, "_mode", tokenizer.HEURISTIC_MODE)
    monkeypatch.setattr(tokenizer, "_tokenizer", None)


def _reported_tokens(block: str) -> int:
    line = next(l for l in block.splitlines() if l.startswith("- tokens: "))
    return int(line.removeprefix("- tokens: "))


def test_render_pack_emits_single_block_in_budget(fresh_db, monkeypatch):
    from cairn.pack import build_pack, render_pack

    _pin_heuristic_tokenizer(monkeypatch)
    _seed_pack_symbols(fresh_db)
    result = build_pack(fresh_db, None, "retry backoff policy", 4000)
    block = render_pack(result)
    lines = block.splitlines()
    assert sum(1 for l in lines if l.startswith("# ")) == 1
    assert lines[0] == "# cairn context pack"
    reported = _reported_tokens(block)
    assert 0 < reported <= result.budget


def test_render_pack_header_carries_task_budget_and_mode(fresh_db, monkeypatch):
    from cairn.dashboard import tokenizer
    from cairn.pack import build_pack, render_pack

    _pin_heuristic_tokenizer(monkeypatch)
    _seed_pack_symbols(fresh_db)
    block = render_pack(build_pack(fresh_db, None, "retry backoff policy", 4000))
    lines = block.splitlines()
    assert "- task: retry backoff policy" in lines
    assert "- budget: 4000" in lines
    assert f"- tokenizer: {tokenizer.HEURISTIC_MODE}" in lines


def test_render_pack_reports_header_inclusive_cost(fresh_db, monkeypatch):
    from cairn.pack import build_pack, item_cost, render_pack

    _pin_heuristic_tokenizer(monkeypatch)
    _seed_pack_symbols(fresh_db)
    block = render_pack(build_pack(fresh_db, None, "retry backoff policy", 4000))
    assert item_cost(block) == _reported_tokens(block)


def test_render_pack_symbol_sections_follow_rank_order(fresh_db):
    from cairn.pack import build_pack, render_pack

    _seed_pack_symbols(fresh_db)
    _add_symbol(fresh_db, "s4", "run_retry_loop")
    _add_symbol(fresh_db, "s5", "load_retry_config")
    _link(
        fresh_db,
        [
            ("e1", "s4", "s1", "retry_backoff_policy", "calls"),
            ("e2", "s1", "s5", "load_retry_config", "calls"),
        ],
    )
    result = build_pack(fresh_db, None, "retry_backoff_policy", 4000)
    block = render_pack(result)
    sections = [l for l in block.splitlines() if l.startswith("## ")]
    assert sections == [
        "## 1. core.retry_backoff_policy",
        "## 2. core.load_retry_config",
        "## 3. core.run_retry_loop",
    ]
    lines = block.splitlines()
    head = lines.index("## 1. core.retry_backoff_policy")
    assert lines[head + 2] == "- kind: function"
    assert lines[head + 3] == "- file: /tmp/test/backoff.py"


def test_render_pack_marks_seed_flag_per_symbol(fresh_db):
    from cairn.pack import build_pack, render_pack

    _seed_pack_symbols(fresh_db)
    _add_symbol(fresh_db, "s4", "run_retry_loop")
    _link(fresh_db, [("e1", "s4", "s1", "retry_backoff_policy", "calls")])
    block = render_pack(build_pack(fresh_db, None, "retry_backoff_policy", 4000))
    lines = block.splitlines()
    seed_line = lines[lines.index("## 1. core.retry_backoff_policy") + 4]
    neighbor_line = lines[lines.index("## 2. core.run_retry_loop") + 4]
    assert seed_line == "- seed: true"
    assert neighbor_line == "- seed: false"


def test_render_pack_is_deterministic_per_build(fresh_db):
    from cairn.pack import build_pack, render_pack

    _seed_pack_symbols(fresh_db)
    result = build_pack(fresh_db, None, "retry_backoff_policy", 1500)
    assert render_pack(result) == render_pack(result)


def test_render_pack_empty_pool_reports_header_only(fresh_db):
    from cairn.pack import build_pack, render_pack

    _seed_pack_symbols(fresh_db)
    block = render_pack(build_pack(fresh_db, None, "zzz_no_match_zzz", 1500))
    assert not [l for l in block.splitlines() if l.startswith("## ")]
    assert _reported_tokens(block) > 0


def test_render_pack_keeps_task_on_one_line(fresh_db):
    from cairn.pack import build_pack, render_pack

    _seed_pack_symbols(fresh_db)
    block = render_pack(
        build_pack(fresh_db, None, "retry\nbackoff\t\tpolicy", 1500)
    )
    task_line = next(l for l in block.splitlines() if l.startswith("- task:"))
    assert task_line == "- task: retry backoff policy"


# ---------------------------------------------------------------------------
# Fit stage (FR-004)
# ---------------------------------------------------------------------------


def test_build_pack_rejects_non_positive_budget(fresh_db):
    from cairn.pack import build_pack

    for bad in (0, -1, -500):
        with pytest.raises(ValueError, match="--budget"):
            build_pack(fresh_db, None, "retry_backoff_policy", bad)


def test_build_pack_fits_all_items_when_budget_is_generous(fresh_db, monkeypatch):
    from cairn.pack import build_pack

    _pin_heuristic_tokenizer(monkeypatch)
    _seed_pack_symbols(fresh_db)
    _add_symbol(fresh_db, "s4", "run_retry_loop")
    _add_symbol(fresh_db, "s5", "load_retry_config")
    _link(
        fresh_db,
        [
            ("e1", "s4", "s1", "retry_backoff_policy", "calls"),
            ("e2", "s1", "s5", "load_retry_config", "calls"),
        ],
    )
    result = build_pack(fresh_db, None, "retry_backoff_policy", 4000)
    assert [i.rank for i in result.items] == [1, 2, 3]
    assert all(i.kind == "source" for i in result.items)
    assert result.dropped == {"symbols": 0, "compass": 0, "memories": 0}


def test_fit_items_admits_rank_order_prefix_and_counts_drops():
    from cairn.pack import KIND_SOURCE, PackItem, fit_items

    items = [
        PackItem.create(KIND_SOURCE, 1, "first block"),
        PackItem.create(KIND_SOURCE, 2, "second block"),
        PackItem.create(KIND_SOURCE, 3, "third block"),
    ]
    budget = items[0].cost + items[1].cost + 5
    kept, dropped = fit_items(items, budget, header_reserve=5)
    assert [i.rank for i in kept] == [1, 2]
    assert dropped == {"symbols": 1, "compass": 0, "memories": 0}


def test_fit_items_drops_the_whole_tail_once_one_block_misses():
    from cairn.pack import KIND_SOURCE, PackItem, fit_items

    oversized = PackItem.create(KIND_SOURCE, 1, "x" * 400)
    tail = PackItem.create(KIND_SOURCE, 2, "tiny block")
    kept, dropped = fit_items(
        [oversized, tail], budget=oversized.cost + 3, header_reserve=4
    )
    assert kept == []
    assert dropped == {"symbols": 2, "compass": 0, "memories": 0}


def test_fit_items_reserves_compass_and_memory_before_symbols():
    from cairn.pack import KIND_COMPASS, KIND_MEMORY, KIND_SOURCE, PackItem, fit_items

    compass = PackItem.create(KIND_COMPASS, 0, "c" * 40)
    memory = PackItem.create(KIND_MEMORY, 0, "m" * 40)
    hub = PackItem.create(KIND_SOURCE, 1, "s" * 40)
    leaf = PackItem.create(KIND_SOURCE, 2, "t" * 40)
    budget = compass.cost + memory.cost + hub.cost + 5
    kept, dropped = fit_items([hub, leaf, compass, memory], budget, header_reserve=5)
    assert [(i.kind, i.rank) for i in kept] == [
        ("source", 1),
        ("compass", 0),
        ("memory", 0),
    ]
    assert dropped == {"symbols": 1, "compass": 0, "memories": 0}


def test_fit_items_skips_oversized_reserved_block_keeps_rest():
    from cairn.pack import KIND_COMPASS, KIND_MEMORY, PackItem, fit_items

    oversized = PackItem.create(KIND_COMPASS, 0, "c" * 400)
    memory = PackItem.create(KIND_MEMORY, 0, "m" * 40)
    kept, dropped = fit_items(
        [oversized, memory], budget=oversized.cost + 3, header_reserve=4
    )
    assert [i.kind for i in kept] == ["memory"]
    assert dropped == {"symbols": 0, "compass": 1, "memories": 0}


def test_fit_keeps_hub_drops_leaf_when_budget_fits_one(fresh_db, monkeypatch):
    from cairn.pack import build_pack, render_pack

    _pin_heuristic_tokenizer(monkeypatch)
    _seed_pack_symbols(fresh_db)
    _add_symbol(fresh_db, "s4", "hub_consumer")
    _add_symbol(fresh_db, "s5", "peripheral_helper")
    _link(
        fresh_db,
        [
            ("e1", "s4", "s1", "retry_backoff_policy", "calls"),
            ("e2", "s5", "s1", "retry_backoff_policy", "calls"),
            ("e3", "s1", "s5", "peripheral_helper", "calls"),
        ],
    )
    full = build_pack(fresh_db, None, "retry_backoff_policy", 1_000_000)
    assert [s.symbol_id for s in full.symbols] == ["s1", "s5", "s4"]
    costs = [i.cost for i in full.items]
    budget = _reported_tokens(render_pack(full)) - costs[2] + 8
    tight = build_pack(fresh_db, None, "retry_backoff_policy", budget)
    assert [i.rank for i in tight.items] == [1, 2]
    assert tight.dropped == {"symbols": 1, "compass": 0, "memories": 0}
    block = render_pack(tight)
    assert 0 < _reported_tokens(block) <= budget
    sections = [l for l in block.splitlines() if l.startswith("## ")]
    assert sections == [
        "## 1. core.retry_backoff_policy",
        "## 2. core.peripheral_helper",
    ]


def test_build_pack_impossible_budget_degrades_and_reports_drops(fresh_db, monkeypatch):
    from cairn.pack import build_pack, render_pack

    _pin_heuristic_tokenizer(monkeypatch)
    _seed_pack_symbols(fresh_db)
    _add_symbol(fresh_db, "s4", "run_retry_loop")
    _link(fresh_db, [("e1", "s4", "s1", "retry_backoff_policy", "calls")])
    result = build_pack(fresh_db, None, "retry_backoff_policy", 10)
    assert result.items == ()
    assert result.dropped == {"symbols": 2, "compass": 0, "memories": 0}
    block = render_pack(result)
    lines = block.splitlines()
    assert "- kept: symbols=0 compass=0 memories=0" in lines
    assert "- dropped: symbols=2 compass=0 memories=0" in lines
    assert sum(1 for l in lines if l.startswith("# ")) == 1
    assert not [l for l in lines if l.startswith("## ")]
    assert _reported_tokens(block) > 0
    assert block.endswith("\n")


# ---------------------------------------------------------------------------
# Enrichment wiring (FR-003 across the pipeline and emitter)
# ---------------------------------------------------------------------------


def _seed_real_symbols(conn: sqlite3.Connection, tmp_path, text: str) -> None:
    """One repo + file over real text (absolute stored path) with the two
    backoff symbols; rebuilds the FTS index for the lexical seed stage."""
    path = tmp_path / "backoff.py"
    path.write_text(text)
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES ('repo-f1', 'f1', ?)",
        (str(tmp_path),),
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) "
        "VALUES ('f1', 'repo-f1', ?, 'python')",
        (str(path),),
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES ('s1', 'f1', 'retry_backoff_policy', 'function', "
        "'core.retry_backoff_policy', 1, 5)"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES ('s2', 'f1', 'run_retry_loop', 'function', "
        "'core.run_retry_loop', 7, 8)"
    )
    try:
        conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    except sqlite3.OperationalError:
        pass  # FTS5 not available in this build
    conn.commit()


_BACKOFF_TEXT = (
    "def retry_backoff_policy(limit: int) -> int:\n"
    "    delay = 1\n"
    "    for _ in range(limit):\n"
    "        delay *= 2\n"
    "    return delay\n"
    "\n"
    "def run_retry_loop() -> None:\n"
    "    retry_backoff_policy(3)\n"
)


def _enriched_bundle(tmp_path, *, with_memories: bool = True):
    from cairn.okf.bundle import OKFBundle
    from cairn.okf.concept import OKFConcept

    bundle = OKFBundle(str(tmp_path / ".knowledge"))
    bundle.write_concept(
        OKFConcept(
            type="Compass",
            concept_id="compass/core",
            title="core",
            resource=str(tmp_path),
            body="Core layer guide.",
        )
    )
    if with_memories:
        bundle.write_concept(
            OKFConcept(
                type="Memory",
                concept_id="memory/tribal/retry-defaults",
                title="Retry backoff policy",
                body="Use exponential backoff with jitter.",
                description="decision",
            )
        )
    return bundle


def test_symbol_block_carries_source_and_blast_radius(fresh_db, monkeypatch, tmp_path):
    from cairn.pack import build_pack, render_pack

    _pin_heuristic_tokenizer(monkeypatch)
    _seed_real_symbols(fresh_db, tmp_path, _BACKOFF_TEXT)
    _link(fresh_db, [("e1", "s2", "s1", "retry_backoff_policy", "calls")])
    result = build_pack(fresh_db, None, "retry backoff policy", 4000)
    block = render_pack(result)
    assert "def retry_backoff_policy(limit: int) -> int:" in block
    assert "    return delay" in block
    assert "- blast radius (depth<=2, precise): total=1" in block
    assert "top: run_retry_loop" in block
    lines = block.splitlines()
    head = lines.index("## 1. core.retry_backoff_policy")
    assert lines[head + 2] == "- kind: function"
    assert lines[head + 4] == "- seed: true"
    assert "def retry_backoff_policy(limit: int) -> int:" in lines[head + 6]
    blast_line = next(l for l in lines if l.startswith("- blast radius"))
    assert lines.index(blast_line) > lines.index("    return delay")


def test_symbol_block_keeps_blast_radius_without_readable_source(fresh_db, monkeypatch):
    from cairn.pack import build_pack, render_pack

    _pin_heuristic_tokenizer(monkeypatch)
    _seed_pack_symbols(fresh_db)
    block = render_pack(build_pack(fresh_db, None, "retry_backoff_policy", 1500))
    assert "def retry_backoff_policy" not in block
    assert "- blast radius (depth<=2, precise): total=0" in block


def test_build_pack_enriches_with_all_four_content_kinds(fresh_db, monkeypatch, tmp_path):
    from cairn.pack import build_pack, render_pack

    _pin_heuristic_tokenizer(monkeypatch)
    _seed_real_symbols(fresh_db, tmp_path, _BACKOFF_TEXT)
    _link(fresh_db, [("e1", "s2", "s1", "retry_backoff_policy", "calls")])
    bundle = _enriched_bundle(tmp_path)
    result = build_pack(fresh_db, bundle, "retry backoff policy", 4000)
    assert [(i.kind, i.rank) for i in result.items] == [
        ("source", 1),
        ("source", 2),
        ("compass", 0),
        ("memory", 0),
    ]
    assert result.dropped == {"symbols": 0, "compass": 0, "memories": 0}
    block = render_pack(result)
    sections = [l for l in block.splitlines() if l.startswith("## ")]
    assert sections == [
        "## 1. core.retry_backoff_policy",
        "## 2. core.run_retry_loop",
        "## Compass",
        "## Memories",
    ]
    assert "Core layer guide." in block
    assert "Retry backoff policy" in block
    assert "decision" in block


def test_build_pack_dedups_compass_excerpts_across_the_pool(
    fresh_db, monkeypatch, tmp_path
):
    from cairn.pack import build_pack

    _pin_heuristic_tokenizer(monkeypatch)
    _seed_real_symbols(fresh_db, tmp_path, _BACKOFF_TEXT)
    _link(fresh_db, [("e1", "s1", "s2", "run_retry_loop", "calls")])
    bundle = _enriched_bundle(tmp_path)
    result = build_pack(fresh_db, bundle, "retry backoff policy", 4000)
    compass_items = [i for i in result.items if i.kind == "compass"]
    assert len(compass_items) == 1
    assert compass_items[0].text.count("## Compass") == 1


def test_build_pack_omits_memories_section_when_absent(
    fresh_db, monkeypatch, tmp_path
):
    from cairn.pack import build_pack, render_pack

    _pin_heuristic_tokenizer(monkeypatch)
    _seed_real_symbols(fresh_db, tmp_path, _BACKOFF_TEXT)
    _link(fresh_db, [("e1", "s2", "s1", "retry_backoff_policy", "calls")])
    bundle = _enriched_bundle(tmp_path, with_memories=False)
    result = build_pack(fresh_db, bundle, "retry backoff policy", 4000)
    assert [i.kind for i in result.items] == ["source", "source", "compass"]
    assert result.dropped == {"symbols": 0, "compass": 0, "memories": 0}
    block = render_pack(result)
    assert "## Compass" in block
    assert "## Memories" not in block
    assert "def retry_backoff_policy(limit: int) -> int:" in block
    assert "- blast radius (depth<=2, precise): total=1" in block


def test_build_pack_without_bundle_omits_reserved_sections(
    fresh_db, monkeypatch, tmp_path
):
    from cairn.pack import build_pack, render_pack

    _pin_heuristic_tokenizer(monkeypatch)
    _seed_real_symbols(fresh_db, tmp_path, _BACKOFF_TEXT)
    block = render_pack(build_pack(fresh_db, None, "retry backoff policy", 4000))
    assert "## Compass" not in block
    assert "## Memories" not in block
    assert "def retry_backoff_policy(limit: int) -> int:" in block
    assert "- blast radius (depth<=2" in block


def test_build_pack_trims_oversized_definition_within_budget(
    fresh_db, monkeypatch, tmp_path
):
    from cairn.pack import build_pack, render_pack

    _pin_heuristic_tokenizer(monkeypatch)
    path = tmp_path / "big.py"
    path.write_text("\n".join(f"# body line {i}" for i in range(1, 101)) + "\n")
    fresh_db.execute(
        "INSERT INTO repos (id, name, path) VALUES ('repo-big', 'big', ?)",
        (str(tmp_path),),
    )
    fresh_db.execute(
        "INSERT INTO files (id, repo_id, path, language) "
        "VALUES ('fbig', 'repo-big', ?, 'python')",
        (str(path),),
    )
    fresh_db.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES ('sbig', 'fbig', 'retry_backoff_policy', 'function', "
        "'core.retry_backoff_policy', 1, 100)"
    )
    try:
        fresh_db.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    except sqlite3.OperationalError:
        pass  # FTS5 not available in this build
    fresh_db.commit()
    result = build_pack(fresh_db, None, "retry_backoff_policy", 2000)
    block = render_pack(result)
    assert "... (+60 more lines trimmed)" in block
    assert "# body line 50" not in block
    reported = _reported_tokens(block)
    assert 0 < reported <= 2000


def test_build_pack_with_enrichment_is_deterministic(fresh_db, monkeypatch, tmp_path):
    from cairn.pack import build_pack, render_pack

    _pin_heuristic_tokenizer(monkeypatch)
    _seed_real_symbols(fresh_db, tmp_path, _BACKOFF_TEXT)
    _link(fresh_db, [("e1", "s2", "s1", "retry_backoff_policy", "calls")])
    bundle = _enriched_bundle(tmp_path)
    first = render_pack(build_pack(fresh_db, bundle, "retry backoff policy", 4000))
    second = render_pack(build_pack(fresh_db, bundle, "retry backoff policy", 4000))
    assert first == second


def test_reserved_drops_reported_when_budget_cannot_fit_them(
    fresh_db, monkeypatch, tmp_path
):
    from cairn.pack import build_pack, render_pack

    _pin_heuristic_tokenizer(monkeypatch)
    _seed_real_symbols(fresh_db, tmp_path, _BACKOFF_TEXT)
    _link(fresh_db, [("e1", "s2", "s1", "retry_backoff_policy", "calls")])
    bundle = _enriched_bundle(tmp_path)
    result = build_pack(fresh_db, bundle, "retry backoff policy", 10)
    assert result.items == ()
    assert result.dropped == {"symbols": 2, "compass": 1, "memories": 1}
    block = render_pack(result)
    assert "- dropped: symbols=2 compass=1 memories=1" in block.splitlines()


def test_fit_admits_enrichment_before_lower_rank_symbols(
    fresh_db, monkeypatch, tmp_path
):
    from cairn.pack import build_pack, render_pack

    _pin_heuristic_tokenizer(monkeypatch)
    _seed_real_symbols(fresh_db, tmp_path, _BACKOFF_TEXT)
    _link(fresh_db, [("e1", "s2", "s1", "retry_backoff_policy", "calls")])
    bundle = _enriched_bundle(tmp_path)
    full = build_pack(fresh_db, bundle, "retry backoff policy", 1_000_000)
    tail_cost = next(i.cost for i in full.items if i.kind == "source" and i.rank == 2)
    budget = _reported_tokens(render_pack(full)) - tail_cost + 8
    tight = build_pack(fresh_db, bundle, "retry backoff policy", budget)
    assert [i.kind for i in tight.items] == ["source", "compass", "memory"]
    assert tight.dropped == {"symbols": 1, "compass": 0, "memories": 0}
    assert 0 < _reported_tokens(render_pack(tight)) <= budget
