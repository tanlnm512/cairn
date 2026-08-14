"""T04: parse-errors block in `cairn status`.

`parse_errors` rows are written by the builder (`graph/builder.py:901`) and the
incremental indexer (`graph/incremental.py:156`) but were read by zero
commands. This covers the new `status` block that surfaces the total count plus
the newest 5 (path shortened via `_shorten`, message truncated to ~100 chars),
and the "silent when empty" invariant -- a clean DB's status output must be
identical to today's.
"""
from __future__ import annotations

import sqlite3
import uuid

from click.testing import CliRunner

from cairn.cli import main
from cairn.graph.schema import _apply_schema


def _make_db(path, errors):
    """Create a file-backed DB with the full schema + parse_errors rows.

    `errors` is a list of (file_path, error_message) tuples. A repos row is
    seeded first so the parse_errors.repo_id FK holds. Timestamps are assigned
    monotonically per row so the last-inserted row is the newest (DESC order).
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    conn.execute(
        "INSERT INTO repos (id, name, path, language, git_remote, indexed_at) "
        "VALUES ('r1', 'r1', '.', '', NULL, '2026-08-13T00:00:00')"
    )
    for i, (fp, msg) in enumerate(errors):
        conn.execute(
            "INSERT INTO parse_errors "
            "(id, file_path, repo_id, error_message, stack_trace, timestamp) "
            "VALUES (?, ?, 'r1', ?, NULL, ?)",
            (str(uuid.uuid4()), fp, msg, f"2026-08-13T00:00:{i:02d}"),
        )
    conn.commit()
    conn.close()


def test_status_shows_parse_errors_block(tmp_path):
    """Errors present -> block appears with the true total + each entry."""
    db = tmp_path / "graph.db"
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _make_db(
        db,
        [
            ("/repo/src/a.py", "SyntaxError: invalid syntax"),
            ("/repo/src/b.py", "unexpected indent"),
        ],
    )

    result = CliRunner().invoke(
        main, ["status", "--db", str(db), "--knowledge", str(knowledge)]
    )
    assert result.exit_code == 0, result.output
    assert "Parse errors: 2" in result.output
    # Each shortened path and its message appear on the detail lines.
    assert "a.py" in result.output
    assert "SyntaxError: invalid syntax" in result.output
    assert "b.py" in result.output
    assert "unexpected indent" in result.output


def test_status_silent_when_no_parse_errors(tmp_path):
    """Empty table -> the block is entirely absent (clean DB unchanged)."""
    db = tmp_path / "graph.db"
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    _make_db(db, [])

    result = CliRunner().invoke(
        main, ["status", "--db", str(db), "--knowledge", str(knowledge)]
    )
    assert result.exit_code == 0, result.output
    assert "Parse errors" not in result.output


def test_status_caps_listing_at_five_shows_true_total(tmp_path):
    """>5 rows -> exactly the 5 newest listed, but the true total in the header."""
    db = tmp_path / "graph.db"
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    rows = [(f"/repo/src/file{i}.py", f"err{i}") for i in range(7)]
    _make_db(db, rows)

    result = CliRunner().invoke(
        main, ["status", "--db", str(db), "--knowledge", str(knowledge)]
    )
    assert result.exit_code == 0, result.output
    # True total in the header.
    assert "Parse errors: 7" in result.output
    # Newest 5 by DESC timestamp = file6..file2 (highest indices first).
    for i in (2, 3, 4, 5, 6):
        assert f"file{i}.py" in result.output
    # The two oldest (file0, file1) are capped out and must not be listed.
    assert "file0.py" not in result.output
    assert "file1.py" not in result.output


def test_status_truncates_long_error_messages(tmp_path):
    """Messages longer than ~100 chars are truncated (first 100 + ellipsis)."""
    db = tmp_path / "graph.db"
    knowledge = tmp_path / "knowledge"
    knowledge.mkdir()
    long_msg = "x" * 250
    _make_db(db, [("/repo/src/long.py", long_msg)])

    result = CliRunner().invoke(
        main, ["status", "--db", str(db), "--knowledge", str(knowledge)]
    )
    assert result.exit_code == 0, result.output
    assert "Parse errors: 1" in result.output
    # The full message must not appear verbatim.
    assert long_msg not in result.output
    # Exactly 100 'x' chars survive (the message is truncated to the first 100,
    # then "..."). Counting chars is robust to rich's 80-col line wrapping.
    assert result.output.count("x") == 100
