"""`cairn taint` contract over the spec fixtures: the auto pass conditions.

Each test copies a fixture workspace from tests/fixtures/taint-tracking
into tmp_path, re-creates the `.git` repo marker (empty dirs do not
survive clones), builds the graph, and drives the command through
CliRunner. Hermeticity per CONSTITUTION C-04: `cairn.cli` is imported
lazily inside the tests, every invocation passes `--db` into tmp_path,
and `CAIRN_WORKSPACE` scopes config resolution to the fixture copy, so
no store outside the sandbox is read or written.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from cairn.graph.builder import build_graph

FIXTURES_ROOT = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "taint-tracking"
)

# One probe per category-matrix micro-flow: every default source category
# into the shell sink, and the http source into every default sink category.
_MATRIX_PROBES = [
    ("http", "shell", "http_shell_entry", "http_shell_sink"),
    ("http", "shell", "http_shell_entry_b", "http_shell_sink_b"),
    ("cli", "shell", "cli_shell_entry", "cli_shell_sink"),
    ("env", "shell", "env_shell_entry", "env_shell_sink"),
    ("file-read", "shell", "file_read_shell_entry", "file_read_shell_sink"),
    ("api-response", "shell", "api_shell_entry", "api_shell_sink"),
    ("http", "sql", "http_sql_entry", "http_sql_sink"),
    ("http", "file-write", "http_write_entry", "http_write_sink"),
    ("http", "network-send", "http_send_entry", "http_send_sink"),
    ("http", "eval-deserialize", "http_eval_entry", "http_eval_sink"),
]


def _fixture_workspace(tmp_path: Path, name: str) -> Path:
    ws = tmp_path / name
    shutil.copytree(FIXTURES_ROOT / name, ws)
    (ws / ".git").mkdir(exist_ok=True)
    return ws


def _build_db(ws: Path, tmp_path: Path) -> Path:
    db = tmp_path / "graph.kg"
    build_graph(workspace=str(ws), db_path=str(db))
    return db


def _invoke_taint(ws: Path, db: Path, *args):
    from click.testing import CliRunner

    from cairn.cli import main

    return CliRunner().invoke(
        main,
        ["taint", "--db", str(db), *args],
        env={"CAIRN_WORKSPACE": str(ws)},
    )


def test_sql_flow_traces_end_to_end_on_defaults(tmp_path):
    ws = _fixture_workspace(tmp_path, "sql-flow")
    db = _build_db(ws, tmp_path)
    result = _invoke_taint(ws, db, "--from", "http", "--to", "sql")
    assert result.exit_code == 0
    for symbol in ("handle_request", "validate_input", "run_query"):
        assert symbol in result.output
    assert result.output.count("[exact]") == 3


def test_config_declared_categories_trace_outside_defaults(tmp_path):
    ws = _fixture_workspace(tmp_path, "custom-config")
    db = _build_db(ws, tmp_path)
    result = _invoke_taint(ws, db, "--from", "queue-msg", "--to", "render")
    assert result.exit_code == 0
    assert "consume_job" in result.output
    assert "render_invoice" in result.output


def test_default_propagation_stops_at_ambiguous_hop(tmp_path):
    ws = _fixture_workspace(tmp_path, "ambiguous-hop")
    db = _build_db(ws, tmp_path)
    result = _invoke_taint(ws, db, "--from", "http", "--to", "sql")
    assert result.exit_code != 0
    assert "run_migration" not in result.output


def test_fuzzy_opt_in_crosses_ambiguous_hop_with_label(tmp_path):
    ws = _fixture_workspace(tmp_path, "ambiguous-hop")
    db = _build_db(ws, tmp_path)
    result = _invoke_taint(ws, db, "--from", "http", "--to", "sql", "--fuzzy")
    assert result.exit_code == 0
    assert "run_migration" in result.output
    assert "[ambiguous]" in result.output


def test_clean_workspace_yields_no_path_default_and_fuzzy(tmp_path):
    ws = _fixture_workspace(tmp_path, "clean-workspace")
    db = _build_db(ws, tmp_path)
    for args in (
        ("--from", "http", "--to", "sql"),
        ("--from", "http", "--to", "sql", "--fuzzy"),
    ):
        result = _invoke_taint(ws, db, *args)
        assert result.exit_code != 0
        assert "format_report" not in result.output


def test_unmatched_source_pattern_degrades_gracefully(tmp_path):
    ws = _fixture_workspace(tmp_path, "sql-flow")
    db = _build_db(ws, tmp_path)
    result = _invoke_taint(ws, db, "--from", "no-such-source", "--to", "sql")
    assert result.exit_code != 0
    assert "run_query" not in result.output
    assert "Traceback" not in result.output


def test_framework_only_flow_stays_unflagged_default_and_fuzzy(tmp_path):
    ws = _fixture_workspace(tmp_path, "framework-only")
    db = _build_db(ws, tmp_path)
    for args in (
        ("--from", "http", "--to", "sql"),
        ("--from", "http", "--to", "sql", "--fuzzy"),
    ):
        result = _invoke_taint(ws, db, *args)
        assert result.exit_code != 0
        assert "save_order" not in result.output


def test_cli_shell_traces_on_defaults(tmp_path):
    ws = _fixture_workspace(tmp_path, "cli-shell")
    db = _build_db(ws, tmp_path)
    result = _invoke_taint(ws, db, "--from", "cli", "--to", "shell")
    assert result.exit_code == 0
    assert ":main [" in result.output
    assert ":run_command [" in result.output
    assert "[exact]" in result.output


@pytest.mark.parametrize(
    ("from_pattern", "to_pattern", "entry", "sink"),
    _MATRIX_PROBES,
    ids=[f"{src}-to-{dst}-{entry}" for src, dst, entry, _ in _MATRIX_PROBES],
)
def test_category_matrix_probe_traces_a_path(
    tmp_path, from_pattern, to_pattern, entry, sink
):
    ws = _fixture_workspace(tmp_path, "category-matrix")
    db = _build_db(ws, tmp_path)
    result = _invoke_taint(ws, db, "--from", from_pattern, "--to", to_pattern)
    assert result.exit_code == 0
    assert f":{entry} [" in result.output
    assert f":{sink} [" in result.output
