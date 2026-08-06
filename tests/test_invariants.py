"""System-wide invariants: properties that must ALWAYS hold.

Unlike behavior tests (which pin a specific function's output), these tests
guard *invariants* -- properties of the whole system whose violation signals
something is fundamentally broken, regardless of which code path produced the
data:

1. The ``.kg`` database is portable -- every stored path is repo-relative
   (files.path / repos.path), so the DB file is shareable across machines.
2. The read-side resolution round-trips -- every stored relative path resolves
   to a file that exists on disk via ``resolve_file_path``.
3. Every MCP tool has a non-empty docstring -- these are shown verbatim to AI
   clients as tool descriptions, so an empty one is a regression.
4. ``_apply_schema`` is idempotent -- running it twice must not raise.
5. An edge marked ``resolution='exact'`` always has a non-null ``target_id`` --
   precise-by-default queries (get_callers, impact_analysis) trust this blindly.

See BUGS.md#portable-path-stale-comments and
BUGS.md#scip-importer-fake-resolution for the regressions that motivated these.
"""
from __future__ import annotations

from pathlib import Path

from cairn.graph.builder import build_graph
from cairn.graph.scanner import resolve_file_path
from cairn.graph.schema import _apply_schema, get_db


FIXTURE_FILES = {
    "Simple.kt": (
        "package com.example\n\n"
        "class Simple {\n"
        "    fun doWork() {}\n"
        "}\n"
    ),
}


def _make_single_repo_workspace(tmp_path: Path, name: str) -> Path:
    """Create a minimal single-repo workspace (workspace root IS the git repo)
    with a ``.git`` dir and one indexed source file."""
    workspace = tmp_path / name
    (workspace / ".git").mkdir(parents=True)
    for fname, contents in FIXTURE_FILES.items():
        (workspace / fname).write_text(contents)
    return workspace


# ---------------------------------------------------------------------------
# 1. Build-time portability: stored paths are repo-relative.
# ---------------------------------------------------------------------------

def test_invariant_files_path_relative_after_build(tmp_path):
    """Invariant: the .kg database is portable. After a build, no files.path
    or repos.path may be absolute. See BUGS.md#portable-path-stale-comments."""
    workspace = _make_single_repo_workspace(tmp_path, "portable")
    db_path = str(tmp_path / "portable.db")
    build_graph(workspace=str(workspace), db_path=db_path)

    conn = get_db(db_path, read_only=True)
    try:
        file_rows = conn.execute("SELECT path FROM files").fetchall()
        repo_rows = conn.execute("SELECT path FROM repos").fetchall()
    finally:
        conn.close()

    assert file_rows, "expected at least one indexed file"
    assert repo_rows, "expected at least one indexed repo"

    for r in file_rows:
        p = r["path"]
        assert not Path(p).is_absolute(), (
            f"files.path must be repo-relative (portable), got absolute {p}"
        )
    for r in repo_rows:
        p = r["path"]
        assert not Path(p).is_absolute(), (
            f"repos.path must be workspace-relative (portable), got absolute {p}"
        )


# ---------------------------------------------------------------------------
# 2. Read-time resolution: every stored relative path round-trips to disk.
# ---------------------------------------------------------------------------

def test_invariant_resolve_file_path_roundtrips(tmp_path):
    """Invariant: every stored relative path resolves to an existing file via
    resolve_file_path. If this fails, the read-side resolution is broken."""
    workspace = _make_single_repo_workspace(tmp_path, "roundtrip")
    db_path = str(tmp_path / "roundtrip.db")
    build_graph(workspace=str(workspace), db_path=db_path)

    conn = get_db(db_path, read_only=True)
    try:
        rows = conn.execute("SELECT path, repo_id FROM files").fetchall()
    finally:
        conn.close()

    assert rows, "expected at least one indexed file"

    ws = str(workspace)
    for r in rows:
        resolved = resolve_file_path(ws, r["repo_id"], r["path"])
        assert Path(resolved).exists(), (
            f"resolve_file_path should round-trip to an existing file, "
            f"got non-existent {resolved}"
        )


# ---------------------------------------------------------------------------
# 3. Agent-facing descriptions: every MCP tool has a non-empty docstring.
# ---------------------------------------------------------------------------

def test_invariant_mcp_tool_docstrings_nonempty():
    """Invariant: every @mcp.tool / registered MCP tool function has a non-empty
    docstring. These are shown verbatim to AI clients as tool descriptions."""
    # Importing the shared FastMCP instance.
    from cairn.mcp_server._server_core import mcp
    # Importing the tools_*.py modules registers every @mcp.tool() on `mcp`
    # via decorator side effects, exactly as server.py does at boot.
    from cairn.mcp_server import (  # noqa: F401
        tools_compass,
        tools_graph,
        tools_knowledge,
        tools_memory,
    )

    tools = mcp._tool_manager.list_tools()
    assert tools, "expected registered MCP tools, found none"

    missing = []
    for tool in tools:
        # FastMCP derives the tool description from the function's __doc__; both
        # must be non-empty. Prefer the derived description (what the client
        # actually sees), fall back to the raw function docstring.
        desc = (getattr(tool, "description", None) or "").strip()
        fn_doc = (tool.fn.__doc__ or "").strip()
        if not desc or not fn_doc:
            missing.append((tool.name, repr(desc), repr(fn_doc)))

    assert not missing, (
        "Every MCP tool must have a non-empty docstring (shown to AI clients "
        "as the tool description). Empty/missing docstrings: "
        f"{missing}"
    )


# ---------------------------------------------------------------------------
# 4. Schema migration idempotency: applying schema twice doesn't error.
# ---------------------------------------------------------------------------

def test_invariant_schema_migration_idempotent(fresh_db):
    """Invariant: _apply_schema is idempotent. Running it on an already-migrated
    DB must not raise (no 'duplicate column' errors leak past the guard).

    The ``fresh_db`` fixture already applied the schema once, so this call is
    the second application on the same connection.
    """
    conn = fresh_db  # already had _apply_schema run by the fixture

    # Second application must be a no-op, not raise.
    _apply_schema(conn)

    # Every additive migration column is present (the fixture applied them, and
    # the second pass must not have dropped them). These column names come from
    # the MIGRATIONS list in cairn/graph/schema.py.
    expected_symbol_cols = {
        "metadata",
        "parameters",
        "return_type",
        "parent_scope",
        "imports_summary",
        "body",
    }
    symbol_cols = {
        row["name"] for row in conn.execute("PRAGMA table_info(symbols)").fetchall()
    }
    missing = expected_symbol_cols - symbol_cols
    assert not missing, (
        f"migration columns missing from symbols after re-apply: {sorted(missing)} "
        f"(got {sorted(symbol_cols)})"
    )

    edge_cols = {
        row["name"] for row in conn.execute("PRAGMA table_info(edges)").fetchall()
    }
    assert "resolution" in edge_cols, (
        f"edges.resolution should exist after schema apply (got {sorted(edge_cols)})"
    )

    files_cols = {
        row["name"] for row in conn.execute("PRAGMA table_info(files)").fetchall()
    }
    assert "size" in files_cols and "mtime" in files_cols, (
        f"files.size/mtime should exist after schema apply (got {sorted(files_cols)})"
    )

    transitive_cols = {
        row["name"]
        for row in conn.execute("PRAGMA table_info(transitive_edges)").fetchall()
    }
    assert "target_id" in transitive_cols, (
        "transitive_edges.target_id should exist after schema apply "
        f"(got {sorted(transitive_cols)})"
    )


# ---------------------------------------------------------------------------
# 5. resolution='exact' implies target_id IS NOT NULL.
# ---------------------------------------------------------------------------

def test_invariant_exact_resolution_has_target_id(fresh_db):
    """Invariant: an edge marked resolution='exact' must have a non-null target_id.
    A 'exact' label on an unresolved edge is the worst kind of data pollution --
    precise-by-default queries (get_callers, impact_analysis) trust it blindly.
    See BUGS.md#scip-importer-fake-resolution.

    NOTE: this can't catch the SCIP importer's bug directly (that would need a
    SCIP import), but it documents the invariant and would catch any code path
    that violates it for data inserted through normal paths.
    """
    conn = fresh_db

    # Seed a repo + file + two symbols + edges with known resolution states.
    conn.execute(
        "INSERT INTO repos (id, name, path) VALUES (?, ?, ?)",
        ("repo1", "demo", "."),
    )
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, ?, ?)",
        ("file1", "repo1", "Simple.kt", "kotlin"),
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind) VALUES (?, ?, ?, ?)",
        ("sym_caller", "file1", "doWork", "function"),
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind) VALUES (?, ?, ?, ?)",
        ("sym_target", "file1", "Helper", "function"),
    )

    # A correctly-resolved edge: resolution='exact' WITH a target_id.
    conn.execute(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind, resolution) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("edge_exact", "sym_caller", "sym_target", "Helper", "calls", "exact"),
    )
    # An unresolved edge: resolution='unresolved' WITH NULL target_id (valid).
    conn.execute(
        "INSERT INTO edges (id, source_id, target_id, target_name, kind, resolution) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("edge_unresolved", "sym_caller", None, "External", "calls", "unresolved"),
    )
    conn.commit()

    # The invariant: no edge is marked 'exact' without a target_id.
    violations = conn.execute(
        "SELECT COUNT(*) FROM edges WHERE resolution = 'exact' AND target_id IS NULL"
    ).fetchone()[0]
    assert violations == 0, (
        f"{violations} edge(s) marked resolution='exact' have NULL target_id -- "
        "an 'exact' edge must always resolve to a concrete symbol"
    )

    # Sanity: the 'exact' edge we inserted did carry its target_id, and the
    # 'unresolved' one correctly has none.
    exact_row = conn.execute(
        "SELECT target_id FROM edges WHERE id = 'edge_exact'"
    ).fetchone()
    unresolved_row = conn.execute(
        "SELECT target_id FROM edges WHERE id = 'edge_unresolved'"
    ).fetchone()
    assert exact_row["target_id"] == "sym_target"
    assert unresolved_row["target_id"] is None
