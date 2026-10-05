"""Merge-order risk contracts: community_overlaps ranking + --conflicts CLI."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cairn.graph.prs import community_overlaps

FIXTURES = Path(__file__).parent / "fixtures" / "gh"

CHAIN_FILE = (
    "def changed():\n"
    "    return 1\n"
    "\n"
    "\n"
    "def direct():\n"
    "    return changed()\n"
)

DIFF_TOUCHES_CHANGED = (
    "diff --git a/call_chain.py b/call_chain.py\n"
    "--- a/call_chain.py\n"
    "+++ b/call_chain.py\n"
    "@@ -1,2 +1,2 @@\n"
    " def changed():\n"
    "-    return 1\n"
    "+    return 11\n"
)

DIFF_TOUCHES_DIRECT = (
    "diff --git a/call_chain.py b/call_chain.py\n"
    "--- a/call_chain.py\n"
    "+++ b/call_chain.py\n"
    "@@ -5,2 +5,2 @@\n"
    " def direct():\n"
    "-    return changed()\n"
    "+    return changed() or 1\n"
)

COMMUNITIES_DDL = (
    "CREATE TABLE communities ("
    "id INTEGER PRIMARY KEY, label TEXT NOT NULL, size INTEGER NOT NULL)",
    "CREATE TABLE symbol_communities ("
    "community_id INTEGER NOT NULL, symbol_id TEXT NOT NULL, "
    "structural_degree INTEGER NOT NULL)",
)

HINT_PAYLOAD = {
    "pairs": [],
    "hint": "community tables absent — run cairn communities",
}


def _community_conn(*, populated: bool) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for ddl in COMMUNITIES_DDL:
        conn.execute(ddl)
    if populated:
        conn.executemany(
            "INSERT INTO communities VALUES (?, ?, ?)",
            [(1, "core", 2), (2, "api", 1)],
        )
        conn.executemany(
            "INSERT INTO symbol_communities VALUES (?, ?, ?)",
            [(1, "sym-x", 4), (2, "sym-x", 2), (1, "sym-a", 1), (1, "sym-b", 1), (2, "sym-c", 3)],
        )
    return conn


def test_community_overlaps_ranks_by_shared_count_then_pr_numbers():
    conn = _community_conn(populated=True)
    result = community_overlaps(
        conn,
        {7: ["sym-x"], 8: ["sym-a", "sym-c"], 9: ["sym-b"]},
    )
    assert result["hint"] is None
    assert [
        (pair["a"], pair["b"], pair["shared"], pair["communities"])
        for pair in result["pairs"]
    ] == [
        (7, 8, 2, ["api", "core"]),
        (7, 9, 1, ["core"]),
        (8, 9, 1, ["core"]),
    ]


def test_community_overlaps_missing_tables_yields_hint():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    assert community_overlaps(conn, {1: ["sym-x"]}) == HINT_PAYLOAD


def test_community_overlaps_empty_tables_yield_hint():
    conn = _community_conn(populated=False)
    assert community_overlaps(conn, {1: ["sym-x"]}) == HINT_PAYLOAD


def _seed_workspace_store(
    workspace: Path, db_path: Path, *, with_communities: bool
) -> None:
    from cairn.graph.schema import get_db

    conn = get_db(str(db_path))
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO repos (id, name, path, language, indexed_at) "
            "VALUES ('demo', 'demo', 'demo', 'python', ?)",
            (now,),
        )
        disk = workspace / "demo" / "call_chain.py"
        disk.parent.mkdir(parents=True, exist_ok=True)
        disk.write_text(CHAIN_FILE, encoding="utf-8")
        stat = disk.stat()
        conn.execute(
            "INSERT INTO files "
            "(id, repo_id, path, language, line_count, indexed_at, size, mtime) "
            "VALUES ('file-call_chain.py', 'demo', 'call_chain.py', 'python', "
            "?, ?, ?, ?)",
            (len(CHAIN_FILE.splitlines()), now, stat.st_size, stat.st_mtime),
        )
        for name, start, end in [("changed", 1, 2), ("direct", 5, 6)]:
            conn.execute(
                "INSERT INTO symbols "
                "(id, file_id, name, qualified_name, kind, line_start, line_end) "
                "VALUES (?, 'file-call_chain.py', ?, ?, 'function', ?, ?)",
                (f"symbol-{name}", name, name, start, end),
            )
        if with_communities:
            conn.executemany(
                "INSERT INTO communities VALUES (?, ?, ?)",
                [(1, "core", 2), (2, "api", 1)],
            )
            conn.executemany(
                "INSERT INTO symbol_communities VALUES (?, ?, ?)",
                [
                    (1, "symbol-changed", 3),
                    (1, "symbol-direct", 1),
                    (2, "symbol-direct", 2),
                ],
            )
        conn.commit()
    finally:
        conn.close()


def _install_gh_stub(tmp_path: Path, monkeypatch, body: str) -> None:
    bindir = tmp_path / "_gh_stub"
    bindir.mkdir(exist_ok=True)
    gh = bindir / "gh"
    gh.write_text("#!/bin/sh\n" + body, encoding="utf-8")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")


def _stub_body(tmp_path: Path) -> str:
    paths = {}
    for name, text in [
        ("diff_42.patch", DIFF_TOUCHES_CHANGED),
        ("diff_43.patch", DIFF_TOUCHES_DIRECT),
    ]:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        paths[name] = str(path)
    return (
        'case "$2" in\n'
        f'  list) cat \'{FIXTURES / "pr_list.json"}\' ;;\n'
        "  diff) case \"$3\" in\n"
        f"      42) cat '{paths['diff_42.patch']}' ;;\n"
        f"      43) cat '{paths['diff_43.patch']}' ;;\n"
        "    esac ;;\n"
        "esac"
    )


def _invoke_prs(db_path: Path, *args):
    from click.testing import CliRunner
    from cairn.cli import main

    return CliRunner().invoke(main, ["prs", "--db", str(db_path), *args])


@pytest.fixture
def prs_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "graph.db"
    _seed_workspace_store(workspace, db_path, with_communities=True)
    return db_path


@pytest.fixture
def prs_workspace_no_communities(tmp_path):
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "graph.db"
    _seed_workspace_store(workspace, db_path, with_communities=False)
    return db_path


def test_prs_conflicts_ranks_pair_with_shared_community(
    prs_workspace, tmp_path, monkeypatch
):
    _install_gh_stub(tmp_path, monkeypatch, _stub_body(tmp_path))
    result = _invoke_prs(prs_workspace, "--conflicts")
    assert result.exit_code == 0, result.output
    assert "Merge-order risk" in result.output
    assert "#42" in result.output and "#43" in result.output
    assert "1 shared" in result.output
    assert "core" in result.output
    assert "api" not in result.output


def test_prs_conflicts_empty_tables_shows_hint_without_pairs(
    prs_workspace_no_communities, tmp_path, monkeypatch
):
    _install_gh_stub(tmp_path, monkeypatch, _stub_body(tmp_path))
    result = _invoke_prs(prs_workspace_no_communities, "--conflicts")
    assert result.exit_code == 0, result.output
    assert "cairn communities" in result.output
    assert "shared" not in result.output
    assert "Traceback" not in result.output
