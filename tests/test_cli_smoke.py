"""Tests for the cairn CLI smoke surface and scripts/run_cli_smoke.py replay runner."""
from __future__ import annotations

import importlib.util
import json
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "run_cli_smoke.py"

# scripts/ is not a package; load the runner by file path so the object
# under test is the same module the subprocess executes.
_spec = importlib.util.spec_from_file_location("run_cli_smoke", SCRIPT)
rs = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("run_cli_smoke", rs)
_spec.loader.exec_module(rs)


def _py(body: str) -> str:
    return shlex.join([sys.executable, "-c", body])


def _write_list(tmp_path: Path, commands: list[str]) -> Path:
    path = tmp_path / "commands.txt"
    path.write_text("\n".join(commands) + "\n", encoding="utf-8")
    return path


class TestGreenReplay:
    def test_streams_group_per_command_and_exits_zero(self, tmp_path, capsys):
        fixture = tmp_path / "fixture"
        fixture.mkdir()
        first = _py("pass")
        second = _py("pass")
        list_path = _write_list(tmp_path, [first, second])

        rc = rs.main([str(list_path), "--fixture", str(fixture), "--db", str(tmp_path / "smoke.db")])
        out = capsys.readouterr().out

        assert rc == 0
        assert f"::group::{first}" in out
        assert f"::group::{second}" in out
        assert out.count("::group::") == 2
        assert out.count("::endgroup::") == 2
        assert "::error::" not in out

    def test_json_mode_emits_table_without_workflow_annotations(self, tmp_path, capsys):
        fixture = tmp_path / "fixture"
        fixture.mkdir()
        commands = [_py("pass"), _py("pass"), _py("pass")]
        list_path = _write_list(tmp_path, commands)

        rc = rs.main(
            [
                str(list_path),
                "--fixture",
                str(fixture),
                "--db",
                str(tmp_path / "smoke.db"),
                "--json",
            ]
        )
        out = capsys.readouterr().out

        assert rc == 0
        assert "::" not in out
        payload = json.loads(out)
        assert payload["ok"] is True
        assert payload["failed"] is None
        assert [row["command"] for row in payload["commands"]] == commands
        assert [row["exit_code"] for row in payload["commands"]] == [0, 0, 0]


class TestFirstFailure:
    def test_names_command_and_skips_later_ones(self, tmp_path, capsys):
        fixture = tmp_path / "fixture"
        fixture.mkdir()
        marker = fixture / "later-ran"
        failing = _py("raise SystemExit(3)")
        later = _py(f"import pathlib; pathlib.Path({str(marker)!r}).touch()")
        list_path = _write_list(tmp_path, [failing, later])

        rc = rs.main([str(list_path), "--fixture", str(fixture), "--db", str(tmp_path / "smoke.db")])
        out = capsys.readouterr().out

        assert rc == 1
        error_lines = [line for line in out.splitlines() if line.startswith("::error::")]
        assert len(error_lines) == 1
        assert failing in error_lines[0]
        assert out.count("::group::") == 1
        assert not marker.exists()

    def test_json_mode_names_failed_command_with_captured_output(self, tmp_path, capsys):
        fixture = tmp_path / "fixture"
        fixture.mkdir()
        ok_cmd = _py("pass")
        failing = _py("import sys; print('boom'); raise SystemExit(7)")
        list_path = _write_list(tmp_path, [ok_cmd, failing, ok_cmd])

        rc = rs.main(
            [
                str(list_path),
                "--fixture",
                str(fixture),
                "--db",
                str(tmp_path / "smoke.db"),
                "--json",
            ]
        )
        out = capsys.readouterr().out

        assert rc == 1
        assert "::" not in out
        payload = json.loads(out)
        assert payload["ok"] is False
        assert payload["failed"] == failing
        assert [row["command"] for row in payload["commands"]] == [ok_cmd, failing]
        assert [row["exit_code"] for row in payload["commands"]] == [0, 7]
        assert payload["commands"][1]["stdout"] == "boom\n"

    def test_subprocess_entrypoint_wires_the_exit_code(self, tmp_path):
        fixture = tmp_path / "fixture"
        fixture.mkdir()
        list_path = _write_list(tmp_path, [_py("raise SystemExit(1)")])

        proc = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                str(list_path),
                "--fixture",
                str(fixture),
                "--db",
                str(tmp_path / "smoke.db"),
            ],
            capture_output=True,
            text=True,
        )

        assert proc.returncode == 1
        assert "::error::" in proc.stdout


class TestPinnedExecution:
    def test_cwd_db_and_cairn_env_are_pinned(self, tmp_path, capsys, monkeypatch):
        fixture = tmp_path / "fixture"
        fixture.mkdir()
        db = tmp_path / "nested" / "smoke.db"
        probe = fixture / "probe.txt"
        monkeypatch.setenv("CAIRN_HOME", "/ambient/home")
        monkeypatch.setenv("CAIRN_TELEMETRY", "on")
        body = (
            "import os, pathlib; "
            f"pathlib.Path({str(probe)!r}).write_text("
            "os.getcwd() + '\\n' + os.environ.get('CAIRN_DB', '') + '\\n' "
            "+ os.environ.get('CAIRN_HOME', '') + '\\n' + os.environ.get('CAIRN_TELEMETRY', ''))"
        )
        list_path = _write_list(tmp_path, [_py(body)])

        rc = rs.main([str(list_path), "--fixture", str(fixture), "--db", str(db)])
        capsys.readouterr()

        assert rc == 0
        cwd, pinned_db, home, telemetry = probe.read_text(encoding="utf-8").splitlines()
        assert cwd == str(fixture)
        assert pinned_db == str(db)
        assert home == ""
        assert telemetry == "off"

    def test_blank_and_comment_lines_are_skipped(self, tmp_path, capsys):
        fixture = tmp_path / "fixture"
        fixture.mkdir()
        only = _py("pass")
        list_path = tmp_path / "commands.txt"
        list_path.write_text(
            "# high-signal commands\n\n   \n" + only + "\n# trailing comment\n", encoding="utf-8"
        )

        rc = rs.main([str(list_path), "--fixture", str(fixture), "--db", str(tmp_path / "smoke.db")])
        out = capsys.readouterr().out

        assert rc == 0
        assert out.count("::group::") == 1
        assert f"::group::{only}" in out


class TestInfrastructureErrors:
    def test_missing_fixture_exits_two(self, tmp_path, capsys):
        list_path = _write_list(tmp_path, [_py("pass")])

        rc = rs.main(
            [str(list_path), "--fixture", str(tmp_path / "nope"), "--db", str(tmp_path / "smoke.db")]
        )
        captured = capsys.readouterr()

        assert rc == 2
        assert "fixture" in captured.err


def test_cli_help():
    from click.testing import CliRunner

    from cairn.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "metrics" in result.output
    assert "status" in result.output
    assert "eval" in result.output


def test_cli_commands_smoke(hash_backend):
    from click.testing import CliRunner

    from cairn.cli import main
    from cairn.graph.schema import get_db

    # hash embedder: the `eval` smoke leg runs the default 40-query harness;
    # shape assertions don't need the semantic model.
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        knowledge_dir = str(Path(tmpdir) / ".knowledge")
        Path(knowledge_dir).mkdir(parents=True, exist_ok=True)

        # Initialize schema via get_db
        conn = get_db(db_path)
        conn.close()

        # Test metrics
        res_metrics = runner.invoke(main, ["metrics", "--db", db_path, "--json"])
        assert res_metrics.exit_code == 0

        # Test status
        res_status = runner.invoke(main, ["status", "--db", db_path, "--knowledge", knowledge_dir])
        assert res_status.exit_code == 0
        # Status output uses the themed display module -- look for any of the
        # canonical rollup labels (lowercase "graph" now, was "Graph:" before).
        assert "graph" in res_status.output.lower()
        assert "repos" in res_status.output

        # Test eval
        res_eval = runner.invoke(main, ["eval", "--db", db_path, "--knowledge", knowledge_dir, "--json"])
        assert res_eval.exit_code == 0
        assert "L1" in res_eval.output


def test_dashboard_help():
    from click.testing import CliRunner

    from cairn.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["dashboard", "--help"])
    assert result.exit_code == 0
    assert "--db" in result.output
    assert "--host" in result.output
    assert "--port" in result.output


def test_dashboard_refuses_non_loopback_host():
    from click.testing import CliRunner

    from cairn.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["dashboard", "--host", "0.0.0.0"])
    assert result.exit_code != 0
    assert "localhost-only" in result.output


def test_dashboard_db_defaults_to_central_store(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from cairn.cli.dashboard import _resolve_db

    central = tmp_path / "central.kg"
    monkeypatch.setattr(
        "cairn.paths.resolve_store", lambda: SimpleNamespace(db=central)
    )
    assert _resolve_db(None) == str(central)
    assert _resolve_db("/tmp/other.db") == "/tmp/other.db"
