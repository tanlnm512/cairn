"""End-to-end smoke test for the `cairn init` rail UI via CliRunner.

Per docs/init-ui-plan.md §Verification #6: assert exit_code == 0, the rail
open/close glyphs are present, "Initialized in" appears, and no ANSI escapes
leak into piped/CliRunner output.
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path

from click.testing import CliRunner

from cairn.cli import main


def _fixture_workspace(tmp: Path) -> Path:
    ws = tmp / "ws"
    repo = ws / "demo"
    (repo / ".git").mkdir(parents=True)
    (repo / "Hello.kt").write_text("class Hello { fun go() {} }\n")
    return ws


def test_init_no_build_renders_rail_open_and_close():
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        ws = _fixture_workspace(tmp_path)
        result = runner.invoke(
            main,
            ["init", "--workspace", str(ws), "--no-build"],
            env={"CAIRN_HOME": str(tmp_path / "cairn_home")},
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        out = result.output
        # Rail opens and closes.
        assert "┌ Initializing cairn" in out
        assert "└ Done" in out
        # Depth-0 step lines appear.
        assert "Initialized in" in out
        # Detail lines carry the rail continuation.
        assert ".kg" in out and ".knowledge" in out
        # Trailing hint prints after the rail closes.
        assert "`cairn config`" in out


def test_config_db_flag_prints_bare_path():
    """`cairn config --db` prints exactly the resolved .kg path (one line,
    machine-readable) — the form the README self-demo pipes into sqlite3."""
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        ws = _fixture_workspace(tmp_path)
        result = runner.invoke(
            main,
            ["config", "--db"],
            env={
                "CAIRN_HOME": str(tmp_path / "cairn_home"),
                "CAIRN_WORKSPACE": str(ws),
            },
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        out = result.output.strip()
        assert out.endswith(".kg")
        assert "\n" not in out, "must be a single machine-readable line"


def test_init_piped_output_has_no_ansi_escapes():
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        ws = _fixture_workspace(tmp_path)
        result = runner.invoke(
            main,
            ["init", "--workspace", str(ws), "--no-build"],
            env={"CAIRN_HOME": str(tmp_path / "cairn_home")},
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        # CliRunner output is non-TTY: no cursor/SGR escapes should appear.
        assert not re.search(r"\x1b\[[0-9;]*[A-Za-z]", result.output)


def test_init_with_build_shows_indexed_summary():
    runner = CliRunner()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        ws = _fixture_workspace(tmp_path)
        result = runner.invoke(
            main,
            ["init", "--workspace", str(ws)],
            env={"CAIRN_HOME": str(tmp_path / "cairn_home")},
            catch_exceptions=False,
        )
        assert result.exit_code == 0
        out = result.output
        # The build sub-steps render and a stats line closes the flow.
        assert "Scanning files" in out
        assert "Indexed" in out
        assert "nodes" in out


def test_fresh_init_persists_user_only_api_key_file(tmp_path):
    import stat

    from cairn import paths
    from cairn.mcp_server import auth

    ws = _fixture_workspace(tmp_path)
    result = CliRunner().invoke(
        main,
        ["init", "--workspace", str(ws), "--no-build"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0
    key_file = paths.CAIRN_HOME / "mcp-api-key"
    assert key_file.is_file()
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600
    key = auth.persisted_api_key()
    assert key
    assert key in key_file.read_text(encoding="utf-8")
    assert key not in result.output
    # Key material lives in the key file only — never in rail output or logs.
    leaked = [
        p
        for p in paths.CAIRN_HOME.rglob("*")
        if p.is_file() and p != key_file and key.encode() in p.read_bytes()
    ]
    assert leaked == []


def test_init_preserves_existing_api_key(tmp_path):
    from cairn import paths
    from cairn.mcp_server import auth

    ws = _fixture_workspace(tmp_path)
    runner = CliRunner()
    first = runner.invoke(
        main,
        ["init", "--workspace", str(ws), "--no-build"],
        catch_exceptions=False,
    )
    assert first.exit_code == 0
    assert (paths.CAIRN_HOME / "mcp-api-key").is_file()
    original = auth.persisted_api_key()
    assert original
    second = runner.invoke(
        main,
        ["init", "--workspace", str(ws), "--no-build"],
        catch_exceptions=False,
    )
    assert second.exit_code == 0
    assert auth.persisted_api_key() == original


def test_mcp_config_snippet_prefers_the_key_the_daemon_enforces(tmp_path, monkeypatch):
    """The daemon enforces resolve_api_key (explicit > env), so the snippet
    must carry the env key when set; the persisted file is only the fallback."""
    import json

    from cairn import paths

    monkeypatch.setenv("CAIRN_MCP_API_KEY", "env-key-123")
    key_file = paths.CAIRN_HOME / "mcp-api-key"
    key_file.parent.mkdir(parents=True, exist_ok=True)
    key_file.write_text("file-key-456", encoding="utf-8")

    with_env = CliRunner().invoke(
        main, ["config", "--mcp-config"], catch_exceptions=False,
    )
    assert with_env.exit_code == 0
    snippet = json.loads(with_env.stdout)
    header = snippet["mcpServers"]["cairn"]["headers"]["Authorization"]
    assert header == "Bearer env-key-123"

    monkeypatch.delenv("CAIRN_MCP_API_KEY")
    without_env = CliRunner().invoke(
        main, ["config", "--mcp-config"], catch_exceptions=False,
    )
    assert without_env.exit_code == 0
    snippet = json.loads(without_env.stdout)
    header = snippet["mcpServers"]["cairn"]["headers"]["Authorization"]
    assert header == "Bearer file-key-456"


def test_mcp_config_snippet_carries_env_key_without_persisted_file(tmp_path, monkeypatch):
    """A daemon keyed via CAIRN_MCP_API_KEY alone must not yield a keyless
    snippet that 401s on every call."""
    import json

    from cairn import paths

    assert not (paths.CAIRN_HOME / "mcp-api-key").exists()
    monkeypatch.setenv("CAIRN_MCP_API_KEY", "env-only-key")

    result = CliRunner().invoke(
        main, ["config", "--mcp-config"], catch_exceptions=False,
    )
    assert result.exit_code == 0
    snippet = json.loads(result.stdout)
    header = snippet["mcpServers"]["cairn"]["headers"]["Authorization"]
    assert header == "Bearer env-only-key"


def test_failed_legacy_move_leaves_no_partial_store(tmp_path, monkeypatch):
    """A failed cross-filesystem .kg migration must leave no partial store db
    (or temp copy) behind, so the next init can retry the migration."""
    import shutil as _shutil

    from cairn import paths
    from cairn.paths import store_key

    ws = tmp_path / "ws"
    legacy = ws / "cairn"
    legacy.mkdir(parents=True)
    (legacy / ".kg").write_text("legacy-db-bytes", encoding="utf-8")
    store_db = paths.CAIRN_HOME / store_key(ws.resolve()) / ".kg"

    real_copyfile = _shutil.copyfile
    failing = {"on": True}

    def _copyfile(src, dst, **kwargs):
        if failing["on"] and str(dst).endswith(".migrate-tmp"):
            raise OSError("simulated cross-device copy failure")
        return real_copyfile(src, dst, **kwargs)

    monkeypatch.setattr(_shutil, "copyfile", _copyfile)

    first = CliRunner().invoke(
        main,
        ["init", "--workspace", str(ws), "--no-build"],
        catch_exceptions=False,
    )
    assert first.exit_code == 0
    assert "legacy .kg migration failed" in first.output
    assert not store_db.exists(), "a failed move must not leave a partial store db"
    assert not list(paths.CAIRN_HOME.rglob("*.migrate-tmp")), (
        "the failed temp copy must be cleaned up"
    )

    failing["on"] = False
    second = CliRunner().invoke(
        main,
        ["init", "--workspace", str(ws), "--no-build"],
        catch_exceptions=False,
    )
    assert second.exit_code == 0
    assert "Migrated" in second.output
    assert store_db.exists(), "the migration retry must succeed"
