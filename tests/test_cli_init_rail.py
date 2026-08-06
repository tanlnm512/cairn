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
