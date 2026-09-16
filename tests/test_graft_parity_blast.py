"""Diff-based blast-radius contracts."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def _hash_embeddings(hash_backend):
    return hash_backend



def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", message)


def _seed_graph(workspace: Path, db_path: Path, files: dict) -> None:
    from cairn.graph.schema import get_db

    repo_name = "demo"
    conn = get_db(str(db_path))
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO repos (id, name, path, language, indexed_at) "
            "VALUES (?, ?, ?, 'python', ?)",
            (repo_name, repo_name, repo_name, now),
        )
        file_ids: dict[str, str] = {}
        symbol_ids: dict[str, str] = {}
        for path, symbols in files.items():
            if path == "edges":
                continue
            disk_path = workspace / repo_name / path
            stat = disk_path.stat()
            file_id = f"file-{path}"
            file_ids[path] = file_id
            conn.execute(
                "INSERT INTO files "
                "(id, repo_id, path, language, line_count, indexed_at, size, mtime) "
                "VALUES (?, ?, ?, 'python', ?, ?, ?, ?)",
                (
                    file_id,
                    repo_name,
                    path,
                    len(disk_path.read_text().splitlines()),
                    now,
                    stat.st_size,
                    stat.st_mtime,
                ),
            )
            for name, kind, start, end in symbols:
                symbol_id = f"symbol-{path}-{name}"
                symbol_ids[name] = symbol_id
                conn.execute(
                    "INSERT INTO symbols "
                    "(id, file_id, name, qualified_name, kind, line_start, line_end) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (symbol_id, file_id, name, name, kind, start, end),
                )
        for edge in files["edges"]:
            if isinstance(edge, dict):
                source = edge["source"]
                target_name = edge["target"]
                resolution = edge["resolution"]
                target_id = (
                    symbol_ids[target_name]
                    if resolution == "exact"
                    else None
                )
                line = edge["line"]
            else:
                source, target_name, line = edge[:3]
                resolution = edge[3] if len(edge) > 3 else "exact"
                target_id = (
                    symbol_ids[target_name] if resolution == "exact" else None
                )
            if target_id is not None:
                target_name = None
            conn.execute(
                "INSERT INTO edges "
                "(source_id, target_id, target_name, kind, line, resolution) "
                "VALUES (?, ?, ?, 'calls', ?, ?)",
                (
                    symbol_ids[source],
                    target_id,
                    target_name,
                    line,
                    resolution,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _prepare_workspace(
    tmp_path: Path,
    monkeypatch,
    *,
    files: dict[str, str],
    graph_files: dict,
) -> tuple[Path, Path, Path]:
    workspace = tmp_path / "workspace"
    repo = workspace / "demo"
    repo.mkdir(parents=True)
    for path, contents in files.items():
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(contents)
    _git(repo, "init")
    _git(repo, "checkout", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    _commit(repo, "baseline")
    db_path = tmp_path / "graph.db"
    _seed_graph(workspace, db_path, graph_files)
    monkeypatch.setenv("CAIRN_WORKSPACE", str(workspace))
    monkeypatch.setenv("CAIRN_DB", str(db_path))
    return workspace, repo, db_path


def _blast(db_path: Path, *args: str):
    from cairn.cli.blast import blast

    return CliRunner().invoke(
        blast,
        ["--db", str(db_path), "--format", "json", *args],
        catch_exceptions=False,
    )


def _run_blast(db_path: Path, *args: str):
    from cairn.cli.blast import blast

    return CliRunner().invoke(
        blast,
        ["--db", str(db_path), *args],
        catch_exceptions=False,
    )


@pytest.fixture
def radius_workspace(tmp_path, monkeypatch):
    workspace, repo, db_path = _prepare_workspace(
        tmp_path,
        monkeypatch,
        files={
            "call_chain.py": (
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
        },
        graph_files={
            "call_chain.py": [
                ("changed", "function", 1, 2),
                ("direct", "function", 5, 6),
                ("transitive", "function", 9, 10),
            ],
            "edges": [("direct", "changed", 6), ("transitive", "direct", 10)],
        },
    )
    chain = repo / "call_chain.py"
    chain.write_text(chain.read_text().replace("    return 1", "    return 11"))
    return workspace, repo, db_path


def test_working_tree_diff_seeds_changed_symbol_and_dependents(
    radius_workspace,
):
    _workspace, _repo, db_path = radius_workspace

    result = _blast(db_path)

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [seed["name"] for seed in payload["seeds"]] == ["changed"]
    assert {row["symbol"] for row in payload["radius"]} == {
        "direct",
        "transitive",
    }


def test_changed_hunk_seeds_only_intersecting_symbols(tmp_path, monkeypatch):
    _workspace, repo, db_path = _prepare_workspace(
        tmp_path,
        monkeypatch,
        files={
            "split_symbols.py": (
                "def edited():\n"
                "    return 1\n"
                "\n"
                "\n"
                "\n"
                "\n"
                "def untouched():\n"
                "    return 2\n"
                "\n"
                "\n"
                "def caller():\n"
                "    return untouched()\n"
            )
        },
        graph_files={
            "split_symbols.py": [
                ("edited", "function", 1, 2),
                ("untouched", "function", 7, 8),
                ("caller", "function", 11, 12),
            ],
            "edges": [("caller", "untouched", 12)],
        },
    )
    source = repo / "split_symbols.py"
    source.write_text(
        source.read_text().replace("    return 1", "    return 11")
    )

    result = _blast(db_path)

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [seed["name"] for seed in payload["seeds"]] == ["edited"]
    assert payload["radius"] == []


def test_duplicate_seed_names_do_not_import_namesake_dependents(
    tmp_path, monkeypatch
):
    _workspace, repo, db_path = _prepare_workspace(
        tmp_path,
        monkeypatch,
        files={
            "call_chain.py": (
                "def edited():\n"
                "    return 1\n"
                "\n"
                "\n"
                "def dependent():\n"
                "    return edited()\n"
            ),
            "namesake.py": (
                "def edited():\n"
                "    return 2\n"
                "\n"
                "\n"
                "def unrelated_caller():\n"
                "    return edited()\n"
            ),
        },
        graph_files={
            "call_chain.py": [
                ("edited", "function", 1, 2),
                ("dependent", "function", 5, 6),
            ],
            "edges": [("dependent", "edited", 6)],
        },
    )
    namesake = repo / "namesake.py"
    stat = namesake.stat()
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO files "
            "(id, repo_id, path, language, line_count, size, mtime) "
            "VALUES ('namesake-file', 'demo', 'namesake.py', 'python', ?, ?, ?)",
            (
                len(namesake.read_text().splitlines()),
                stat.st_size,
                stat.st_mtime,
            ),
        )
        conn.executemany(
            "INSERT INTO symbols "
            "(id, file_id, name, qualified_name, kind, line_start, line_end) "
            "VALUES (?, 'namesake-file', ?, ?, 'function', ?, ?)",
            [
                ("namesake-edited", "edited", "edited", 1, 2),
                (
                    "unrelated-caller",
                    "unrelated_caller",
                    "unrelated_caller",
                    5,
                    6,
                ),
            ],
        )
        conn.execute(
            "INSERT INTO edges "
            "(id, source_id, target_id, kind, line, resolution) "
            "VALUES ('unrelated-edge', 'unrelated-caller', "
            "'namesake-edited', 'calls', 6, 'exact')"
        )
        conn.commit()
    finally:
        conn.close()
    chain = repo / "call_chain.py"
    chain.write_text(chain.read_text().replace("    return 1", "    return 11"))

    result = _blast(db_path, "--no-refresh")

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [seed["id"] for seed in payload["seeds"]] == [
        "symbol-call_chain.py-edited"
    ]
    assert [row["symbol"] for row in payload["radius"]] == ["dependent"]


@pytest.fixture
def merge_base_workspace(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    repo = workspace / "demo"
    repo.mkdir(parents=True)
    chain = repo / "call_chain.py"
    chain.write_text("def shared():\n    return 1\n\n\ndef caller():\n    return shared()\n")
    main_only = repo / "main_only.py"
    main_only.write_text("def main_only():\n    return 0\n")
    _git(repo, "init")
    _git(repo, "checkout", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    _commit(repo, "common base")
    _git(repo, "checkout", "-b", "feature")
    chain.write_text("def shared():\n    return 2\n\n\ndef caller():\n    return shared()\n")
    _commit(repo, "change shared")
    _git(repo, "checkout", "main")
    main_only.write_text("def main_only():\n    return 10\n")
    _commit(repo, "advance main")
    _git(repo, "checkout", "feature")

    db_path = tmp_path / "graph.db"
    _seed_graph(
        workspace,
        db_path,
        {
            "call_chain.py": [
                ("shared", "function", 1, 2),
                ("caller", "function", 5, 6),
            ],
            "main_only.py": [("main_only", "function", 1, 2)],
            "edges": [("caller", "shared", 6)],
        },
    )
    monkeypatch.setenv("CAIRN_WORKSPACE", str(workspace))
    monkeypatch.setenv("CAIRN_DB", str(db_path))
    return workspace, repo, db_path


def test_named_base_compares_from_merge_base(merge_base_workspace):
    _workspace, _repo, db_path = merge_base_workspace

    result = _blast(db_path, "--base", "main")

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert [seed["name"] for seed in payload["seeds"]] == ["shared"]
    assert [row["symbol"] for row in payload["radius"]] == ["caller"]


def test_unknown_base_reports_fetch_depth_guidance(merge_base_workspace):
    _workspace, _repo, db_path = merge_base_workspace

    result = _blast(db_path, "--base", "missing-ref")

    assert result.exit_code != 0
    message = result.output.lower()
    assert "missing-ref" in message
    assert "fetch-depth" in message or "shallow" in message


@pytest.mark.parametrize(
    ("output_format", "expected"),
    [
        (
            "text",
            lambda output: "Blast radius" in output
            and "changed" in output
            and "direct (depth 0)" in output,
        ),
        (
            "markdown",
            lambda output: "## Area: demo" in output
            and "### Changed symbols" in output
            and "- `changed` (`call_chain.py`)" in output
            and "### Dependents" in output,
        ),
        (
            "mermaid",
            lambda output: output.startswith("flowchart TD")
            and "changed --> direct" in output
            and "direct --> transitive" in output,
        ),
        (
            "json",
            lambda output: all(
                key in json.loads(output) for key in ("seeds", "areas", "radius")
            ),
        ),
    ],
)
def test_every_blast_format_renders(radius_workspace, output_format, expected):
    _workspace, _repo, db_path = radius_workspace

    result = _run_blast(db_path, "--format", output_format)

    assert result.exit_code == 0
    assert expected(result.stdout)


def test_text_is_default_and_output_writes_file(radius_workspace, tmp_path):
    _workspace, _repo, db_path = radius_workspace
    output = tmp_path / "blast.txt"

    result = _run_blast(db_path, "--output", str(output))

    assert result.exit_code == 0
    assert result.stdout == ""
    text = output.read_text()
    assert "Blast radius" in text
    assert "Area: demo" in text
    assert "direct (depth 0)" in text


def test_fuzzy_adds_ambiguous_dependent_with_visible_resolution(
    tmp_path, monkeypatch
):
    workspace, repo, _baseline_db = _prepare_workspace(
        tmp_path,
        monkeypatch,
        files={
            "call_chain.py": (
                "def changed():\n"
                "    return 1\n"
                "\n"
                "\n"
                "def precise():\n"
                "    return changed()\n"
            ),
            "ambiguous.py": "def ambiguous():\n    return changed()\n",
        },
        graph_files={"edges": []},
    )
    chain = repo / "call_chain.py"
    chain.write_text(chain.read_text().replace("    return 1", "    return 11"))
    db_path = tmp_path / "current-graph.db"
    _seed_graph(
        workspace,
        db_path,
        {
            "call_chain.py": [
                ("changed", "function", 1, 2),
                ("precise", "function", 5, 6),
            ],
            "ambiguous.py": [("ambiguous", "function", 1, 2)],
            "edges": [
                ("precise", "changed", 6, "exact"),
                {
                    "source": "ambiguous",
                    "target": "changed",
                    "line": 2,
                    "resolution": "ambiguous",
                },
            ],
        },
    )
    monkeypatch.setenv("CAIRN_DB", str(db_path))

    precise = _blast(db_path)
    fuzzy = _blast(db_path, "--fuzzy")

    precise_payload = json.loads(precise.stdout)
    fuzzy_payload = json.loads(fuzzy.stdout)
    assert [row["symbol"] for row in precise_payload["radius"]] == ["precise"]
    fuzzy_names = {row["symbol"] for row in fuzzy_payload["radius"]}
    assert fuzzy_names == {"precise", "ambiguous"}
    ambiguous = next(
        row for row in fuzzy_payload["radius"] if row["symbol"] == "ambiguous"
    )
    assert ambiguous["resolution"] in ("ambiguous", "unresolved", "fuzzy")


def test_empty_radius_exits_zero_and_states_no_dependents(tmp_path, monkeypatch):
    workspace, repo, _baseline_db = _prepare_workspace(
        tmp_path,
        monkeypatch,
        files={"leaf.py": "def changed():\n    return 1\n"},
        graph_files={"edges": []},
    )
    leaf = repo / "leaf.py"
    leaf.write_text(leaf.read_text().replace("    return 1", "    return 11"))
    db_path = tmp_path / "current-graph.db"
    _seed_graph(
        workspace,
        db_path,
        {
            "leaf.py": [("changed", "function", 1, 2)],
            "edges": [],
        },
    )
    monkeypatch.setenv("CAIRN_DB", str(db_path))

    result = _run_blast(db_path)

    assert result.exit_code == 0
    assert "No dependents" in result.stdout


@pytest.mark.parametrize("case", ["unindexed", "deleted"])
def test_no_seed_empty_radius_still_states_no_dependents(
    tmp_path, monkeypatch, case
):
    graph_files = {"edges": []}
    if case == "deleted":
        graph_files["leaf.py"] = [("changed", "function", 1, 2)]
    _workspace, repo, db_path = _prepare_workspace(
        tmp_path,
        monkeypatch,
        files={"leaf.py": "def changed():\n    return 1\n"},
        graph_files=graph_files,
    )
    leaf = repo / "leaf.py"
    if case == "unindexed":
        leaf.write_text(
            leaf.read_text().replace("    return 1", "    return 11"),
            encoding="utf-8",
        )
    else:
        leaf.unlink()
    monkeypatch.setenv("CAIRN_DB", str(db_path))

    result = _run_blast(db_path, "--no-refresh")

    assert result.exit_code == 0
    assert "No dependents" in result.stdout


def test_mermaid_renders_every_dependency_with_unique_nodes():
    from cairn.graph.blast import render_blast

    result = {
        "basis": {"kind": "worktree", "base": "HEAD"},
        "seeds": [
            {
                "id": "seed-one",
                "name": "a-b",
                "file_path": "one.py",
            },
            {
                "id": "seed-two",
                "name": "a_b",
                "file_path": "two.py",
            },
        ],
        "radius": [
            {
                "symbol": "dependent",
                "file": "dependent.py",
                "repo": "demo",
                "depth": 0,
                "depends_on": ["a-b", "a_b"],
                "depends_on_ids": ["seed-one", "seed-two"],
            }
        ],
    }

    rendered = render_blast(result, "mermaid")
    edges = [line.strip() for line in rendered.splitlines() if " --> " in line]

    assert edges == ["a_b --> dependent", "a_b_2 --> dependent"]
