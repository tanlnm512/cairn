"""Tests for the ``cairn pack`` CLI command: registration, boundary errors,
markdown and JSON emission.

Imports ``cairn.cli`` only inside test functions (test-isolation rule); the
command runs against a file-backed temp DB, never the real store.
"""
from __future__ import annotations

import sqlite3


def _seed_file_db(db_path) -> None:
    """File-backed graph DB with a small symbol set; the command opens it read-only."""
    from cairn.graph.schema import _apply_schema

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
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
    try:
        conn.execute("INSERT INTO symbols_fts(symbols_fts) VALUES('rebuild')")
    except sqlite3.OperationalError:
        pass  # FTS5 not available in this build
    conn.commit()
    conn.close()


def _invoke_pack(tmp_path, *args):
    from click.testing import CliRunner
    from cairn.cli import main

    db_path = tmp_path / "graph.db"
    _seed_file_db(db_path)
    return CliRunner().invoke(
        main,
        [
            "pack",
            "--db",
            str(db_path),
            "--knowledge",
            str(tmp_path / ".knowledge"),
            *args,
        ],
    )


def test_pack_command_is_registered():
    from click.testing import CliRunner
    from cairn.cli import main

    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0, result.output
    assert "pack" in result.output.split()


def test_pack_rejects_non_positive_budget(tmp_path):
    result = _invoke_pack(tmp_path, "--task", "retry backoff policy", "--budget", "0")
    assert result.exit_code != 0, result.output
    assert "--budget" in result.output
    assert "Traceback" not in result.output


def test_pack_rejects_negative_budget(tmp_path):
    result = _invoke_pack(tmp_path, "--task", "retry backoff policy", "--budget", "-5")
    assert result.exit_code != 0, result.output
    assert "--budget" in result.output
    assert "Traceback" not in result.output


def test_pack_rejects_blank_task(tmp_path):
    result = _invoke_pack(tmp_path, "--task", "", "--budget", "1000")
    assert result.exit_code != 0, result.output
    assert "--task" in result.output
    assert "Traceback" not in result.output


def test_pack_renders_markdown_block_to_stdout(tmp_path):
    result = _invoke_pack(
        tmp_path, "--task", "retry backoff policy", "--budget", "1000"
    )
    assert result.exit_code == 0, result.output
    assert result.output.startswith("# cairn context pack")
    assert "retry backoff policy" in result.output
    assert "retry_backoff_policy" in result.output


def test_pack_json_emits_the_pack_result(tmp_path):
    import json

    result = _invoke_pack(
        tmp_path, "--task", "retry backoff policy", "--budget", "1000", "--json"
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["task"] == "retry backoff policy"
    assert payload["budget"] == 1000
