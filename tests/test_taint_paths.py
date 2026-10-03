"""Taint path-discovery contracts: entry-terminator overlap and fuzzy hops."""

from __future__ import annotations

import sqlite3

from cairn.graph.taint import build_registry, find_paths


def _row(conn: sqlite3.Connection, table: str, **cols) -> None:
    keys = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT INTO {table} ({keys}) VALUES ({placeholders})",
        list(cols.values()),
    )


def _seed_direct_flow(conn: sqlite3.Connection) -> None:
    """handle() calls the source input() and the sink execute() itself."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'app', '/repo')")
    _row(conn, "files", id="f1", repo_id="r1", path="/repo/handler.py", language="python")
    _row(conn, "files", id="f2", repo_id="r1", path="/repo/stubs.py", language="python")
    _row(conn, "symbols", id="s1", file_id="f1", name="handle",
         qualified_name="handler.handle", kind="function", line_start=1, line_end=5)
    _row(conn, "symbols", id="s2", file_id="f2", name="input",
         qualified_name="stubs.input", kind="function", line_start=1, line_end=2)
    _row(conn, "symbols", id="s3", file_id="f2", name="execute",
         qualified_name="stubs.execute", kind="function", line_start=1, line_end=2)
    _row(conn, "edges", id="e1", source_id="s1", target_id="s2",
         target_name="input", kind="call", line=2, column=0, resolution="exact")
    _row(conn, "edges", id="e2", source_id="s1", target_id="s3",
         target_name="execute", kind="call", line=3, column=0, resolution="exact")
    conn.commit()


def test_entry_calling_both_source_and_sink_reports_direct_path(fresh_db):
    _seed_direct_flow(fresh_db)

    paths = find_paths(fresh_db, build_registry(), "cli", "sql")

    assert len(paths) == 1
    hops = paths[0].hops
    assert [hop.symbol for hop in hops] == ["handle"]


def _seed_cross_repo_ambiguous_hop(conn: sqlite3.Connection) -> None:
    """handler (r1) ambiguously calls ``util``; util exists in r1 AND r2."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'r1', '/repo/r1')")
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r2', 'r2', '/repo/r2')")
    _row(conn, "files", id="f1", repo_id="r1", path="/repo/r1/handler.py", language="python")
    _row(conn, "files", id="f2", repo_id="r1", path="/repo/r1/util.py", language="python")
    _row(conn, "files", id="f3", repo_id="r1", path="/repo/r1/stubs.py", language="python")
    _row(conn, "files", id="f4", repo_id="r2", path="/repo/r2/util.py", language="python")
    _row(conn, "files", id="f5", repo_id="r2", path="/repo/r2/stubs.py", language="python")
    _row(conn, "symbols", id="s1", file_id="f1", name="handler",
         qualified_name="handler.handler", kind="function", line_start=1, line_end=5)
    _row(conn, "symbols", id="s2", file_id="f2", name="util",
         qualified_name="util.util", kind="function", line_start=1, line_end=3)
    _row(conn, "symbols", id="s3", file_id="f3", name="input",
         qualified_name="stubs.input", kind="function", line_start=1, line_end=2)
    _row(conn, "symbols", id="s4", file_id="f3", name="execute",
         qualified_name="stubs.execute", kind="function", line_start=1, line_end=2)
    _row(conn, "symbols", id="s5", file_id="f4", name="util",
         qualified_name="util.util", kind="function", line_start=1, line_end=3)
    _row(conn, "symbols", id="s6", file_id="f5", name="execute",
         qualified_name="stubs.execute", kind="function", line_start=1, line_end=2)
    _row(conn, "edges", id="e1", source_id="s1", target_id="s3",
         target_name="input", kind="call", line=2, column=0, resolution="exact")
    _row(conn, "edges", id="e2", source_id="s1", target_id=None,
         target_name="util", kind="call", line=3, column=0, resolution="ambiguous")
    _row(conn, "edges", id="e3", source_id="s2", target_id="s4",
         target_name="execute", kind="call", line=2, column=0, resolution="exact")
    _row(conn, "edges", id="e4", source_id="s5", target_id="s6",
         target_name="execute", kind="call", line=2, column=0, resolution="exact")
    conn.commit()


def test_fuzzy_ambiguous_hop_stays_same_repo(fresh_db):
    _seed_cross_repo_ambiguous_hop(fresh_db)

    paths = find_paths(fresh_db, build_registry(), "cli", "sql", fuzzy=True)

    assert len(paths) == 1
    hops = paths[0].hops
    assert [hop.symbol for hop in hops] == ["handler", "util"]
    assert all(hop.file.startswith("/repo/r1") for hop in hops)
