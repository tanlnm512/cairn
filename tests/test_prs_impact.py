"""PR-diff impact contracts."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cairn.graph.blast import BlastBaseError, compute_pr_impact, pr_seed_symbols
from cairn.graph.schema import get_db

CHAIN_FILE = (
    "def changed():\n"
    "    return 1\n"
    "\n"
    "\n"
    "def direct():\n"
    "    return changed()\n"
    "\n"
    "\n"
    "def transitive():\n"
    "    return direct()\n"
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

DIFF_UNINDEXED_AND_DELETED = (
    DIFF_TOUCHES_CHANGED
    + "diff --git a/docs/design-note.md b/docs/design-note.md\n"
    "new file mode 100644\n"
    "--- /dev/null\n"
    "+++ b/docs/design-note.md\n"
    "@@ -0,0 +1,2 @@\n"
    "+Design note prose.\n"
    "diff --git a/gone.py b/gone.py\n"
    "deleted file mode 100644\n"
    "--- a/gone.py\n"
    "+++ /dev/null\n"
    "@@ -1,2 +0,0 @@\n"
    "-def changed():\n"
    "-    return 1\n"
)

BLAST_RESULT_KEYS = {
    "basis",
    "changed_files",
    "seeds",
    "areas",
    "radius",
    "cycles",
    "unindexed_files",
    "deleted_files",
    "truncated",
    "taint_paths",
}


def _seed_store(workspace: Path, db_path: Path) -> None:
    conn = get_db(str(db_path))
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO repos (id, name, path, language, indexed_at) "
            "VALUES ('demo', 'demo', 'demo', 'python', ?)",
            (now,),
        )
        for path, contents in {
            "call_chain.py": CHAIN_FILE,
            "gone.py": "def changed():\n    return 1\n",
        }.items():
            disk_path = workspace / "demo" / path
            disk_path.parent.mkdir(parents=True, exist_ok=True)
            disk_path.write_text(contents)
            stat = disk_path.stat()
            file_id = f"file-{path}"
            conn.execute(
                "INSERT INTO files "
                "(id, repo_id, path, language, line_count, indexed_at, size, mtime) "
                "VALUES (?, 'demo', ?, 'python', ?, ?, ?, ?)",
                (
                    file_id,
                    path,
                    len(contents.splitlines()),
                    now,
                    stat.st_size,
                    stat.st_mtime,
                ),
            )
        for name, start, end in [("changed", 1, 2), ("direct", 5, 6), ("transitive", 9, 10)]:
            conn.execute(
                "INSERT INTO symbols "
                "(id, file_id, name, qualified_name, kind, line_start, line_end) "
                "VALUES (?, 'file-call_chain.py', ?, ?, 'function', ?, ?)",
                (f"symbol-{name}", name, name, start, end),
            )
        conn.execute(
            "INSERT INTO edges (source_id, target_id, kind, line, resolution) "
            "VALUES ('symbol-direct', 'symbol-changed', 'calls', 6, 'exact')"
        )
        conn.execute(
            "INSERT INTO edges (source_id, target_id, kind, line, resolution) "
            "VALUES ('symbol-transitive', 'symbol-direct', 'calls', 10, 'exact')"
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def pr_store(tmp_path):
    workspace = tmp_path / "workspace"
    db_path = tmp_path / "graph.db"
    _seed_store(workspace, db_path)
    return workspace, db_path


def _read_only(db_path: Path):
    return get_db(str(db_path), read_only=True)


def test_pr_diff_seeds_changed_symbol_and_dependents(pr_store):
    workspace, db_path = pr_store
    conn = _read_only(db_path)
    try:
        payload = compute_pr_impact(
            conn,
            str(workspace),
            diff_text=DIFF_TOUCHES_CHANGED,
            base_ref="main",
        )
    finally:
        conn.close()

    assert set(payload) == BLAST_RESULT_KEYS
    assert payload["basis"] == {"kind": "pr", "base": "main"}
    assert [seed["name"] for seed in payload["seeds"]] == ["changed"]
    assert {row["symbol"] for row in payload["radius"]} == {"direct", "transitive"}
    assert payload["unindexed_files"] == []
    assert payload["deleted_files"] == []
    assert payload["truncated"] is False

    ro = _read_only(db_path)
    try:
        seeds, unindexed = pr_seed_symbols(ro, str(workspace), DIFF_TOUCHES_CHANGED)
    finally:
        ro.close()
    assert [seed["name"] for seed in seeds] == ["changed"]
    assert unindexed == []


def test_pr_diff_lists_unindexed_and_deleted_files(pr_store):
    workspace, db_path = pr_store
    conn = _read_only(db_path)
    try:
        payload = compute_pr_impact(
            conn,
            str(workspace),
            diff_text=DIFF_UNINDEXED_AND_DELETED,
            base_ref="main",
        )
    finally:
        conn.close()

    assert [seed["name"] for seed in payload["seeds"]] == ["changed"]
    assert payload["unindexed_files"] == ["demo:docs/design-note.md"]
    assert payload["deleted_files"] == ["demo:gone.py"]
    assert all(seed["file_path"] != "gone.py" for seed in payload["seeds"])


def test_impact_serves_and_only_serves_a_read_only_store(pr_store):
    workspace, db_path = pr_store
    conn = _read_only(db_path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("CREATE TABLE write_probe (id)")
        payload = compute_pr_impact(
            conn,
            str(workspace),
            diff_text=DIFF_TOUCHES_CHANGED,
            base_ref="main",
        )
    finally:
        conn.close()
    assert payload["seeds"]


def test_unregistered_workspace_fails_clean(tmp_path):
    db_path = tmp_path / "graph.db"
    get_db(str(db_path)).close()
    conn = _read_only(db_path)
    try:
        with pytest.raises(BlastBaseError):
            compute_pr_impact(
                conn,
                str(tmp_path),
                diff_text=DIFF_TOUCHES_CHANGED,
                base_ref="main",
            )
    finally:
        conn.close()


def test_ambiguous_workspace_root_fails_clean(pr_store):
    workspace, db_path = pr_store
    conn = get_db(str(db_path))
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO repos (id, name, path, language, indexed_at) "
            "VALUES ('root-a', 'root-a', '.', 'python', ?)",
            (now,),
        )
        conn.execute(
            "INSERT INTO repos (id, name, path, language, indexed_at) "
            "VALUES ('root-b', 'root-b', ?, 'python', ?)",
            (str(workspace), now),
        )
        conn.commit()
    finally:
        conn.close()

    ro = _read_only(db_path)
    try:
        with pytest.raises(BlastBaseError):
            compute_pr_impact(
                ro,
                str(workspace),
                diff_text=DIFF_TOUCHES_CHANGED,
                base_ref="main",
            )
    finally:
        ro.close()
