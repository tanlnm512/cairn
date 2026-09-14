"""Tests for build_dataflow_index configuration: symbol cap resolution
(explicit arg > CAIRN_DATAFLOW_MAX_SYMBOLS env var > default), the partial
index warning text, per-repo cross_repo_deps memoization, batched upserts,
and the `cairn dataflow build --max-symbols` CLI flag.
"""
from __future__ import annotations

import sqlite3

import pytest
from click.testing import CliRunner

import cairn.graph.queries as queries
from cairn.cli.main import main
from cairn.graph.dataflow import (
    DEFAULT_MAX_SYMBOLS,
    MAX_SYMBOLS_ENV,
    _SQLITE_IN_CHUNK,
    build_dataflow_index,
    resolve_max_symbols,
)
from cairn.graph.schema import _apply_schema


def _seed(conn: sqlite3.Connection, repos=(("r1", 3), ("r2", 2))):
    """Seed `repos` public python symbols (3 in r1, 2 in r2 by default)."""
    for rid, count in repos:
        conn.execute(
            "INSERT INTO repos (id, name, path, language) VALUES (?, ?, ?, ?)",
            (rid, rid, f"/ws/{rid}", "python"),
        )
        conn.execute(
            "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, ?, ?)",
            (f"f_{rid}", rid, f"{rid}.py", "python"),
        )
        for i in range(count):
            conn.execute(
                "INSERT INTO symbols (id, file_id, name, kind) VALUES (?, ?, ?, ?)",
                (f"s_{rid}_{i}", f"f_{rid}", f"pub_{rid}_{i}", "function"),
            )
    conn.commit()


# --- resolve_max_symbols: precedence and fallbacks -------------------------

def test_explicit_arg_wins_over_env(monkeypatch):
    monkeypatch.setenv(MAX_SYMBOLS_ENV, "3")
    assert resolve_max_symbols(10) == 10


def test_env_var_used_when_no_explicit_arg(monkeypatch):
    monkeypatch.setenv(MAX_SYMBOLS_ENV, "77")
    assert resolve_max_symbols() == 77


def test_default_without_env_or_arg(monkeypatch):
    monkeypatch.delenv(MAX_SYMBOLS_ENV, raising=False)
    assert resolve_max_symbols() == DEFAULT_MAX_SYMBOLS


@pytest.mark.parametrize("raw", ["abc", "0", "-5"])
def test_invalid_env_var_falls_back_to_default(monkeypatch, capsys, raw):
    monkeypatch.setenv(MAX_SYMBOLS_ENV, raw)
    assert resolve_max_symbols() == DEFAULT_MAX_SYMBOLS
    err = capsys.readouterr().err
    assert MAX_SYMBOLS_ENV in err
    assert str(DEFAULT_MAX_SYMBOLS) in err


# --- build_dataflow_index: truncation, warning, batched upserts ------------

def test_cap_truncates_and_warning_names_env_var(fresh_db, capsys):
    _seed(fresh_db)
    count = build_dataflow_index(fresh_db, max_symbols=3)
    assert count == 3
    rows = fresh_db.execute("SELECT COUNT(*) FROM dataflow").fetchone()[0]
    assert rows == 3
    err = capsys.readouterr().err
    assert "dataflow index is partial" in err
    assert "capping at 3" in err
    assert MAX_SYMBOLS_ENV in err


def test_env_var_controls_cap(fresh_db, monkeypatch, capsys):
    _seed(fresh_db)
    monkeypatch.setenv(MAX_SYMBOLS_ENV, "3")
    assert build_dataflow_index(fresh_db) == 3
    assert "dataflow index is partial" in capsys.readouterr().err

    monkeypatch.setenv(MAX_SYMBOLS_ENV, "10")
    assert build_dataflow_index(fresh_db) == 5
    assert "dataflow index is partial" not in capsys.readouterr().err


def test_explicit_cap_overrides_env(fresh_db, monkeypatch, capsys):
    _seed(fresh_db)
    monkeypatch.setenv(MAX_SYMBOLS_ENV, "3")
    assert build_dataflow_index(fresh_db, max_symbols=10) == 5
    assert "dataflow index is partial" not in capsys.readouterr().err


def test_batched_upserts_write_every_row(fresh_db):
    # More public symbols than one executemany batch, so both the mid-run
    # flush and the final flush execute.
    _seed(fresh_db, repos=(("r1", _SQLITE_IN_CHUNK + 1),))
    seen: list[int] = []
    count = build_dataflow_index(fresh_db, progress=seen.append)
    assert count == _SQLITE_IN_CHUNK + 1
    assert seen == list(range(1, count + 1))
    rows = fresh_db.execute("SELECT COUNT(*) FROM dataflow").fetchone()[0]
    assert rows == count


# --- per-repo cross_repo_deps memoization ----------------------------------

def test_cross_repo_deps_called_once_per_repo(fresh_db, monkeypatch):
    _seed(fresh_db)  # 3 symbols in r1, 2 in r2
    calls: list[str] = []
    real = queries.cross_repo_deps

    def counting(conn, repo):
        calls.append(repo)
        return real(conn, repo)

    monkeypatch.setattr(queries, "cross_repo_deps", counting)
    count = build_dataflow_index(fresh_db)
    assert count == 5
    assert sorted(calls) == ["r1", "r2"]

    # Result is identical with the cache: cross_repo stays empty (no imports).
    row = fresh_db.execute(
        "SELECT cross_repo FROM dataflow WHERE symbol = 'pub_r1_0'"
    ).fetchone()
    assert row[0] == "[]"


def test_maintain_path_shares_repo_cache(fresh_db, monkeypatch):
    _seed(fresh_db, repos=(("r1", 2),))
    build_dataflow_index(fresh_db)

    calls: list[str] = []
    real = queries.cross_repo_deps

    def counting(conn, repo):
        calls.append(repo)
        return real(conn, repo)

    monkeypatch.setattr(queries, "cross_repo_deps", counting)
    from cairn.graph.dataflow import maintain_dataflow_index

    maintain_dataflow_index(fresh_db, ["pub_r1_0", "pub_r1_1"])
    assert calls == ["r1"]


# --- CLI flag ----------------------------------------------------------------

def test_cli_build_max_symbols(tmp_path):
    db_path = tmp_path / "graph.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    _seed(conn)
    conn.close()

    result = CliRunner().invoke(
        main, ["dataflow", "build", "--db", str(db_path), "--max-symbols", "2"]
    )
    assert result.exit_code == 0, result.output
    assert "2 public symbols indexed" in result.stdout

    check = sqlite3.connect(str(db_path))
    rows = check.execute("SELECT COUNT(*) FROM dataflow").fetchone()[0]
    check.close()
    assert rows == 2
