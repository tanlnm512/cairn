"""Communities writer: seeded Louvain partition persisted to derived tables."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import sys
import types
from pathlib import Path

import pytest

from cairn.graph.communities import (
    DEFAULT_TOP_K,
    LOUVAIN_SEED,
    compute_communities,
    refresh_communities,
)
from cairn.graph.builder import build_graph
from cairn.graph.schema import get_db

FIXTURES_ROOT = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "on-demand-paths"
)


def _fixture_conn(tmp_path: Path, name: str) -> sqlite3.Connection:
    ws = tmp_path / name
    shutil.copytree(FIXTURES_ROOT / name, ws)
    (ws / ".git").mkdir(exist_ok=True)
    db = tmp_path / f"{name}.kg"
    build_graph(workspace=str(ws), db_path=str(db))
    return get_db(str(db))


def _snapshot(conn: sqlite3.Connection) -> list[tuple]:
    communities = [
        tuple(r)
        for r in conn.execute(
            "SELECT id, label, size FROM communities ORDER BY id"
        ).fetchall()
    ]
    members = [
        tuple(r)
        for r in conn.execute(
            "SELECT community_id, symbol_id, structural_degree "
            "FROM symbol_communities ORDER BY community_id, symbol_id"
        ).fetchall()
    ]
    return communities + members


def test_seeded_run_populates_both_tables(tmp_path):
    """Two disconnected call pairs partition into two 2-member communities,
    every member carrying its structural degree; ids start at 1."""
    conn = _fixture_conn(tmp_path, "islands")
    count = compute_communities(conn)

    assert count == 2
    communities = conn.execute(
        "SELECT id, label, size FROM communities ORDER BY id"
    ).fetchall()
    assert [r["id"] for r in communities] == [1, 2]
    assert [r["size"] for r in communities] == [2, 2]
    assert all(r["label"] for r in communities)

    members = conn.execute(
        "SELECT community_id, symbol_id, structural_degree FROM symbol_communities"
    ).fetchall()
    assert len(members) == 4
    assert {r["community_id"] for r in members} == {1, 2}
    assert all(r["structural_degree"] == 1 for r in members)

    edges = conn.execute(
        "SELECT source_id, target_id FROM edges WHERE kind IN ('calls', 'call', "
        "'extends', 'implements')"
    ).fetchall()
    member_ids = {r["symbol_id"] for r in members}
    edge_endpoints = {e["source_id"] for e in edges} | {
        e["target_id"] for e in edges if e["target_id"] is not None
    }
    assert member_ids == edge_endpoints
    conn.close()


def test_double_run_is_byte_equal(tmp_path):
    """Re-running on an unchanged graph reproduces identical table bytes."""
    conn = _fixture_conn(tmp_path, "islands")
    assert LOUVAIN_SEED == 0

    assert compute_communities(conn) == 2
    first = _snapshot(conn)
    assert first

    assert compute_communities(conn) == 2
    assert _snapshot(conn) == first
    conn.close()


def test_double_run_cli_output_is_byte_identical(tmp_path):
    """Two consecutive CLI runs on an unchanged store print identical bytes."""
    db = _fixture_db(tmp_path, "fork")

    first = _invoke_communities(db)
    second = _invoke_communities(db)

    assert first.exit_code == 0
    assert second.exit_code == 0
    assert first.output
    assert first.output == second.output


def test_zero_edge_store_persists_nothing(fresh_db):
    """Symbols without structural edges get no membership; a full refresh
    still clears stale rows and reports zero communities."""
    fresh_db.execute(
        "INSERT INTO files (id, repo_id, path, language) "
        "VALUES ('f1', 'r1', 'a.py', 'python')"
    )
    fresh_db.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind) "
        "VALUES ('s1', 'f1', 'solo', 'solo', 'function')"
    )
    fresh_db.execute(
        "INSERT INTO communities (id, label, size) VALUES (1, 'stale', 1)"
    )
    fresh_db.execute(
        "INSERT INTO symbol_communities (community_id, symbol_id, structural_degree) "
        "VALUES (1, 's1', 0)"
    )
    fresh_db.commit()

    assert compute_communities(fresh_db) == 0
    assert fresh_db.execute("SELECT COUNT(*) FROM communities").fetchone()[0] == 0
    assert (
        fresh_db.execute("SELECT COUNT(*) FROM symbol_communities").fetchone()[0] == 0
    )


def test_label_falls_back_to_top_hub_without_dominant_segment(fresh_db):
    """Paths spread across segments (no share above the threshold) label the
    community with its highest-degree hub, prefixed to stay distinguishable."""
    fresh_db.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES "
        "('f1', 'r1', 'alpha/one.py', 'python'), "
        "('f2', 'r1', 'beta/two.py', 'python'), "
        "('f3', 'r1', 'gamma/three.py', 'python'), "
        "('f4', 'r1', 'delta/four.py', 'python')"
    )
    fresh_db.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES "
        "('s1', 'f1', 'entry', 'entry', 'function'), "
        "('s2', 'f2', 'left', 'left', 'function'), "
        "('s3', 'f3', 'right', 'right', 'function'), "
        "('s4', 'f4', 'sink', 'sink', 'function')"
    )
    fresh_db.execute(
        "INSERT INTO edges (id, source_id, target_id, kind) VALUES "
        "('e1', 's1', 's2', 'calls'), "
        "('e2', 's1', 's3', 'calls'), "
        "('e3', 's1', 's4', 'calls')"
    )
    fresh_db.commit()

    assert compute_communities(fresh_db) == 1
    label = fresh_db.execute("SELECT label FROM communities").fetchone()[0]
    assert label == "hub:entry"


def test_missing_networkx_hints_without_opening_store(monkeypatch, tmp_path):
    """A failed probe raises the install hint before any writable store open."""
    import cairn.graph.communities as communities_mod

    opened: list[str] = []
    real_get_db = communities_mod.get_db

    def _tracking_get_db(*args, **kwargs):
        opened.append(str(args[0]) if args else str(kwargs))
        return real_get_db(*args, **kwargs)

    monkeypatch.setattr(communities_mod, "get_db", _tracking_get_db)
    monkeypatch.setitem(
        sys.modules,
        "networkx.algorithms.community",
        types.ModuleType("networkx.algorithms.community"),
    )

    db = tmp_path / "probe.kg"
    with pytest.raises(ImportError, match=r"cairn\[graph-analytics\]"):
        refresh_communities(str(db))

    assert opened == []
    assert not db.exists()


def _fixture_db(tmp_path: Path, name: str) -> Path:
    ws = tmp_path / name
    shutil.copytree(FIXTURES_ROOT / name, ws)
    (ws / ".git").mkdir(exist_ok=True)
    db = tmp_path / f"{name}.kg"
    build_graph(workspace=str(ws), db_path=str(db))
    return db


def _invoke_communities(db: Path, top_k: int | None = None):
    from click.testing import CliRunner

    from cairn.cli import main

    args = ["communities", "--db", str(db)]
    if top_k is not None:
        args += ["--top-k", str(top_k)]
    return CliRunner().invoke(
        main,
        args,
        env={"CAIRN_WORKSPACE": str(db.parent)},
    )


def test_communities_cli_prints_count_sizes_and_hubs(tmp_path):
    """The fork fixture partitions deterministically into two same-labeled
    pairs; output is the count, the global hub block, then one hub block per
    community in persisted order."""
    db = _fixture_db(tmp_path, "fork")

    result = _invoke_communities(db)

    assert result.exit_code == 0
    assert result.output == (
        "2 communities\n"
        "Global hubs:\n"
        "  hub_sink (degree 2)\n"
        "  hub_top (degree 2)\n"
        "  mid_left (degree 2)\n"
        "  mid_right (degree 2)\n"
        "Communities:\n"
        "  1: fork.py (2 symbols)\n"
        "    hub_sink (degree 2)\n"
        "    mid_left (degree 2)\n"
        "  2: fork.py (2 symbols)\n"
        "    hub_top (degree 2)\n"
        "    mid_right (degree 2)\n"
    )


def _hub_fixture_db(tmp_path: Path) -> Path:
    """One community whose members span distinct degrees and file segments."""
    db = tmp_path / "hubs.kg"
    conn = get_db(str(db))
    conn.execute(
        "INSERT INTO repos (id, name, path, language) "
        "VALUES ('r1', 'hubs', '/tmp/hubs', 'python')"
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES "
        "('f1', 'r1', 'alpha/one.py', 'python'), "
        "('f2', 'r1', 'beta/two.py', 'python'), "
        "('f3', 'r1', 'gamma/three.py', 'python'), "
        "('f4', 'r1', 'delta/four.py', 'python')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES "
        "('s1', 'f1', 'entry', 'entry', 'function'), "
        "('s2', 'f2', 'left', 'left', 'function'), "
        "('s3', 'f3', 'right', 'right', 'function'), "
        "('s4', 'f4', 'sink', 'sink', 'function')"
    )
    conn.execute(
        "INSERT INTO edges (id, source_id, target_id, kind) VALUES "
        "('e1', 's1', 's2', 'calls'), "
        "('e2', 's1', 's3', 'calls'), "
        "('e3', 's1', 's4', 'calls')"
    )
    conn.commit()
    conn.close()
    return db


def test_cli_hubs_rank_by_degree_and_honor_top_k(tmp_path):
    """Hubs list by (-structural_degree, path, qualified_name), globally and
    per community, capped by --top-k."""
    assert DEFAULT_TOP_K == 10
    db = _hub_fixture_db(tmp_path)

    result = _invoke_communities(db, top_k=2)

    assert result.exit_code == 0
    assert result.output == (
        "1 communities\n"
        "Global hubs:\n"
        "  entry (degree 3)\n"
        "  left (degree 1)\n"
        "Communities:\n"
        "  1: hub:entry (4 symbols)\n"
        "    entry (degree 3)\n"
        "    left (degree 1)\n"
    )


def test_cli_rejects_non_positive_top_k(tmp_path):
    """--top-k below 1 is a usage error, not a crash."""
    db = _fixture_db(tmp_path, "fork")

    result = _invoke_communities(db, top_k=0)

    assert result.exit_code != 0


def test_communities_cli_without_extra_hints_and_leaves_store_unchanged(
    monkeypatch, tmp_path
):
    """A failed probe exits non-zero with the install hint and the store file
    is byte-unchanged (the probe runs before any writable open)."""
    db = _fixture_db(tmp_path, "fork")
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    monkeypatch.setitem(
        sys.modules,
        "networkx.algorithms.community",
        types.ModuleType("networkx.algorithms.community"),
    )

    result = _invoke_communities(db)

    assert result.exit_code != 0
    assert "cairn[graph-analytics]" in result.output
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_communities_cli_zero_edge_store_reports_none(tmp_path):
    """A store with no structural edges prints the zero summary and exits 0."""
    db = tmp_path / "empty.kg"
    get_db(str(db)).close()

    result = _invoke_communities(db)

    assert result.exit_code == 0
    assert "no communities found" in result.output
