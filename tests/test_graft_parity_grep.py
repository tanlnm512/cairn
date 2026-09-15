"""Graph grep grouping, ranking, filtering, and truncation contracts."""

from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest


@dataclass(frozen=True)
class _SymbolSpec:
    name: str
    qualified_name: str
    path: str
    kind: str
    line_start: int
    line_end: int
    incoming: int = 0


def _seed_graph(
    workspace: Path,
    db_path: Path,
    files: dict[str, str],
    symbols: tuple[_SymbolSpec, ...],
) -> None:
    from cairn.graph.schema import _apply_schema

    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".git").mkdir()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        _apply_schema(conn)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO repos (id, name, path) VALUES ('grep', 'grep', '.')"
        )
        file_ids: dict[str, str] = {}
        for relative, source in files.items():
            destination = workspace / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(source, encoding="utf-8")
            stat = destination.stat()
            file_id = f"file:{relative}"
            file_ids[relative] = file_id
            conn.execute(
                """
                INSERT INTO files (
                    id, repo_id, path, language, line_count, size, mtime
                ) VALUES (?, 'grep', ?, 'python', ?, ?, ?)
                """,
                (
                    file_id,
                    relative,
                    source.count("\n"),
                    stat.st_size,
                    stat.st_mtime,
                ),
            )

        symbol_ids: dict[str, str] = {}
        for symbol in symbols:
            symbol_id = f"symbol:{symbol.qualified_name}"
            symbol_ids[symbol.qualified_name] = symbol_id
            conn.execute(
                """
                INSERT INTO symbols (
                    id, file_id, name, qualified_name, kind,
                    line_start, line_end, column_start, column_end
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)
                """,
                (
                    symbol_id,
                    file_ids[symbol.path],
                    symbol.name,
                    symbol.qualified_name,
                    symbol.kind,
                    symbol.line_start,
                    symbol.line_end,
                ),
            )

        callers: list[str] = []
        for symbol in symbols:
            for index in range(symbol.incoming):
                caller_name = f"caller_{symbol.name}_{index}"
                callers.append(caller_name)
                conn.execute(
                    """
                    INSERT INTO symbols (
                        id, file_id, name, qualified_name, kind,
                        line_start, line_end
                    ) VALUES (?, ?, ?, ?, 'function', ?, ?)
                    """,
                    (
                        f"symbol:{caller_name}",
                        file_ids["callers.py"],
                        caller_name,
                        caller_name,
                        len(callers),
                        len(callers),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO edges (
                        id, source_id, target_id, kind, resolution
                    ) VALUES (?, ?, ?, 'calls', 'exact')
                    """,
                    (
                        f"edge:{caller_name}:{symbol.name}",
                        f"symbol:{caller_name}",
                        symbol_ids[symbol.qualified_name],
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def _fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    files: dict[str, str],
    symbols: tuple[_SymbolSpec, ...] = (),
) -> SimpleNamespace:
    workspace = tmp_path / name
    db_path = tmp_path / f"{name}.db"
    if any(symbol.incoming for symbol in symbols):
        caller_count = sum(symbol.incoming for symbol in symbols)
        files = {
            **files,
            "callers.py": "\n".join(
                f"def caller_{index}(): pass" for index in range(caller_count)
            )
            + "\n",
        }
    _seed_graph(workspace, db_path, files, symbols)
    monkeypatch.setenv("CAIRN_WORKSPACE", str(workspace))
    monkeypatch.setenv("CAIRN_DB", str(db_path))
    return SimpleNamespace(workspace=workspace, db_path=db_path)


def _run_grep(fixture: SimpleNamespace, *arguments: str):
    from click.testing import CliRunner

    import cairn.cli.grep  # noqa: F401
    from cairn.cli import main

    return CliRunner().invoke(
        main,
        ["grep", "--json", "--db", str(fixture.db_path), *arguments],
        catch_exceptions=False,
    )


def _payload(fixture: SimpleNamespace, *arguments: str) -> dict:
    result = _run_grep(fixture, *arguments)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


@pytest.fixture
def drifted_grep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hash_backend
) -> SimpleNamespace:
    from cairn.graph.builder import build_graph

    workspace = tmp_path / "drifted-grep-workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    source = workspace / "search.py"
    source.write_text(
        "def old_marker():\n    return 'legacy_needle'\n", encoding="utf-8"
    )
    monkeypatch.setenv("CAIRN_WORKSPACE", str(workspace))
    baseline_db = tmp_path / "drifted-grep-baseline.db"
    build_graph(
        workspace=str(workspace), db_path=str(baseline_db), verbose=False
    )
    source.write_text(
        "def new_marker():\n    return 'fresh_needle'\n", encoding="utf-8"
    )

    def activate(name: str) -> Path:
        active_db = tmp_path / f"{name}.db"
        shutil.copyfile(baseline_db, active_db)
        monkeypatch.setenv("CAIRN_DB", str(active_db))
        return active_db

    return SimpleNamespace(
        workspace=workspace, db_path=baseline_db, activate=activate
    )


def _symbol_names(db_path: Path) -> set[str]:
    conn = sqlite3.connect(db_path)
    try:
        return {row[0] for row in conn.execute("SELECT name FROM symbols")}
    finally:
        conn.close()


@pytest.fixture()
def nested_hits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    return _fixture(
        tmp_path,
        monkeypatch,
        "nested",
        {
            "nested.py": (
                "needle module\n"
                "def outer():\n"
                "    needle outer\n"
                "    def inner():\n"
                "        pass\n"
                "        needle inner\n"
            )
        },
        (
            _SymbolSpec("outer", "outer", "nested.py", "function", 2, 7),
            _SymbolSpec("inner", "outer.inner", "nested.py", "function", 4, 7),
        ),
    )


def test_hits_group_under_innermost_enclosing_symbol(
    nested_hits: SimpleNamespace,
) -> None:
    payload = _payload(nested_hits, "needle")

    groups = {
        group["qualified_name"]: [hit["line"] for hit in group["hits"]]
        for group in payload["groups"]
    }
    assert groups == {
        None: [1],
        "outer": [3],
        "outer.inner": [6],
    }
    file_group = next(
        group for group in payload["groups"] if group["qualified_name"] is None
    )
    assert file_group["kind"] == "file"
    assert payload["searched_files"] == 1


def test_grep_refreshes_a_drifted_store_before_answering(
    drifted_grep,
) -> None:
    db_path = drifted_grep.activate("grep-refresh")

    payload = _payload(drifted_grep, "needle", "--db", str(db_path))

    assert [group["qualified_name"] for group in payload["groups"]] == [
        "new_marker"
    ]
    assert payload["groups"][0]["hits"][0]["text"] == (
        "    return 'fresh_needle'"
    )
    names = _symbol_names(db_path)
    assert "new_marker" in names
    assert "old_marker" not in names


def test_grep_no_refresh_answers_from_storage_with_banner(
    drifted_grep,
) -> None:
    db_path = drifted_grep.activate("grep-no-refresh")

    result = _run_grep(
        drifted_grep,
        "needle",
        "--db",
        str(db_path),
        "--no-refresh",
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [group["qualified_name"] for group in payload["groups"]] == [
        "old_marker"
    ]
    assert payload["groups"][0]["hits"][0]["text"] == (
        "    return 'fresh_needle'"
    )
    assert "Stale graph" in result.stderr
    assert "search.py" in result.stderr


def test_grep_read_only_answers_from_storage_with_banner(
    drifted_grep, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = drifted_grep.activate("grep-read-only")
    monkeypatch.setenv("CAIRN_READ_ONLY", "1")

    result = _run_grep(
        drifted_grep,
        "needle",
        "--db",
        str(db_path),
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert [group["qualified_name"] for group in payload["groups"]] == [
        "old_marker"
    ]
    assert "Stale graph" in result.stderr
    assert "search.py" in result.stderr
    names = _symbol_names(db_path)
    assert "old_marker" in names
    assert "new_marker" not in names


@pytest.fixture()
def coupled_hits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    return _fixture(
        tmp_path,
        monkeypatch,
        "coupled",
        {
            "hub.py": "needle\n",
            "z/tie_a.py": "needle\n",
            "a/tie_b.py": "needle\n",
            "dead.py": "needle\n",
        },
        (
            _SymbolSpec("hub", "hub", "hub.py", "function", 1, 1, incoming=3),
            _SymbolSpec("tie_a", "tie_a", "z/tie_a.py", "function", 1, 1, incoming=1),
            _SymbolSpec("tie_b", "tie_b", "a/tie_b.py", "function", 1, 1, incoming=1),
            _SymbolSpec("dead", "dead", "dead.py", "function", 1, 1),
        ),
    )


def test_groups_rank_by_incoming_edges_then_path(coupled_hits: SimpleNamespace) -> None:
    payload = _payload(coupled_hits, "needle")

    assert [group["qualified_name"] for group in payload["groups"]] == [
        "hub",
        "tie_b",
        "tie_a",
        "dead",
    ]
    assert [group["incoming"] for group in payload["groups"]] == [3, 1, 1, 0]
    assert payload["searched_files"] == 5


@pytest.fixture()
def filtered_hits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    return _fixture(
        tmp_path,
        monkeypatch,
        "filtered",
        {
            "alpha/mixed.py": "NEEDLE\n",
            "beta/mixed.py": "needle\n",
            "alpha/fixed.py": "rate+limit\n",
            "beta/fixed.py": "ratelimit\n",
            "alpha/hit.py": "needle\n",
            "beta/hit.py": "needle\n",
        },
    )


def test_case_literal_and_path_filters(filtered_hits: SimpleNamespace) -> None:
    insensitive = _payload(filtered_hits, "-i", "needle")
    assert [group["path"] for group in insensitive["groups"]] == [
        "alpha/hit.py",
        "alpha/mixed.py",
        "beta/hit.py",
        "beta/mixed.py",
    ]
    assert insensitive["searched_files"] == 6

    fixed = _payload(filtered_hits, "--fixed", "rate+limit")
    assert [group["path"] for group in fixed["groups"]] == ["alpha/fixed.py"]
    assert fixed["searched_files"] == 6

    scoped = _payload(filtered_hits, "--in", "alpha/", "needle")
    assert [group["path"] for group in scoped["groups"]] == ["alpha/hit.py"]
    assert scoped["searched_files"] == 3


@pytest.fixture()
def capped_hits(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    return _fixture(
        tmp_path,
        monkeypatch,
        "capped",
        {
            "first.py": "needle\n",
            "second.py": "needle\n",
            "third.py": "needle\n",
        },
        (
            _SymbolSpec("first", "first", "first.py", "function", 1, 1, incoming=3),
            _SymbolSpec("second", "second", "second.py", "function", 1, 1, incoming=2),
            _SymbolSpec("third", "third", "third.py", "function", 1, 1, incoming=1),
        ),
    )


def test_truncation_counts_are_explicit(capped_hits: SimpleNamespace) -> None:
    payload = _payload(capped_hits, "--max-hits", "2", "needle")

    assert [group["qualified_name"] for group in payload["groups"]] == [
        "first",
        "second",
    ]
    assert sum(len(group["hits"]) for group in payload["groups"]) == 2
    assert payload["dropped_hits"] == 1
    assert payload["dropped_groups"] == 1
    assert payload["searched_files"] == 4
