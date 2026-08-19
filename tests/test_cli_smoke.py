import tempfile
from pathlib import Path
from click.testing import CliRunner

from cairn.cli import main
from cairn.graph.schema import get_db


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "metrics" in result.output
    assert "status" in result.output
    assert "eval" in result.output


def test_cli_commands_smoke():
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
    runner = CliRunner()
    result = runner.invoke(main, ["dashboard", "--help"])
    assert result.exit_code == 0
    assert "--db" in result.output
    assert "--host" in result.output
    assert "--port" in result.output


def test_dashboard_refuses_non_loopback_host():
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
