"""Click-level tests for `cairn skill generate` (FR-001, FR-005, FR-006).

Drives the real command end to end against a hermetic two-package git
workspace: default landing under ``.agents/skills/cairn-<slug>/`` with
loadable frontmatter (TC-001/TC-002), directory-prefix and explicit
symbol-list selectors (TC-005/TC-006), ``--output`` override leaving the
skills home untouched (TC-013), fail-fast on an unknown module writing
nothing (TC-014), and generation with no knowledge store still packaging
symbols (TC-015).

C-04: no eager ``cairn.cli`` imports — the command group is imported
inside each test function. The suite ``_hermetic_env`` fixture sandboxes
CAIRN_HOME; ``cli_env`` adds cwd + CAIRN_DB so the CLI resolves the
fixture graph, never the live store.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """cwd in tmp, CAIRN_DB pointing at the fixture graph db."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAIRN_DB", str(tmp_path / "graph.db"))
    return tmp_path


def _built_workspace(ws: Path) -> None:
    """pkg_a/{zeta_hub, ZetaNest.alpha_leaf}, pkg_b/{beta_one}."""
    (ws / ".git").mkdir()
    (ws / "pkg_a").mkdir()
    (ws / "pkg_b").mkdir()
    (ws / "pkg_a" / "zeta.py").write_text(
        "def zeta_hub() -> str:\n"
        "    return 'zeta'\n"
        "\n"
        "class ZetaNest:\n"
        "    def alpha_leaf(self) -> str:\n"
        "        return 'leaf'\n"
    )
    (ws / "pkg_b" / "beta.py").write_text(
        "def beta_one() -> str:\n"
        "    return 'beta'\n"
    )
    from cairn.graph.builder import build_graph

    build_graph(workspace=str(ws), db_path=str(ws / "graph.db"), verbose=False)


def _invoke(*args):
    from cairn.cli.main import main

    return CliRunner().invoke(main, ["skill", "generate", *args])


def _skill_dirs(ws: Path) -> list[Path]:
    skills_home = ws / ".agents" / "skills"
    if not skills_home.is_dir():
        return []
    return [d for d in skills_home.iterdir() if d.is_dir()]


def _skill_text(ws: Path) -> str:
    dirs = _skill_dirs(ws)
    assert len(dirs) == 1, f"expected one skill dir, got {[d.name for d in dirs]}"
    return (dirs[0] / "SKILL.md").read_text(encoding="utf-8")


def _split_frontmatter(text: str) -> tuple[dict, str]:
    assert text.splitlines()[0] == "---"
    fm_block, body = text[4:].split("\n---\n", 1)
    return yaml.safe_load(fm_block), body


def test_module_selector_lands_cairn_prefixed_skill(cli_env):
    _built_workspace(cli_env)
    result = _invoke("pkg_a")
    assert result.exit_code == 0, result.stdout
    dirs = _skill_dirs(cli_env)
    assert len(dirs) == 1
    assert dirs[0].name.startswith("cairn-")
    assert "pkg" in dirs[0].name
    assert (dirs[0] / "SKILL.md").is_file()


def test_generated_frontmatter_carries_name_description_and_body(cli_env):
    _built_workspace(cli_env)
    result = _invoke("pkg_a")
    assert result.exit_code == 0, result.stdout
    fm, body = _split_frontmatter(_skill_text(cli_env))
    assert "name" in fm
    assert "description" in fm
    assert len(fm["description"]) >= 20
    assert body.strip()


def test_directory_prefix_selector_packages_only_that_module(cli_env):
    _built_workspace(cli_env)
    result = _invoke("pkg_a/")
    assert result.exit_code == 0, result.stdout
    text = _skill_text(cli_env)
    assert "zeta_hub" in text
    assert "alpha_leaf" in text
    assert "beta_one" not in text


def test_symbol_list_selector_packages_exactly_those_symbols(cli_env):
    _built_workspace(cli_env)
    result = _invoke("zeta_hub", "alpha_leaf")
    assert result.exit_code == 0, result.stdout
    text = _skill_text(cli_env)
    assert "zeta_hub" in text
    assert "alpha_leaf" in text
    assert "beta_one" not in text


def test_output_override_writes_custom_dir_not_skills_home(cli_env):
    _built_workspace(cli_env)
    out = cli_env / "custom_out"
    result = _invoke("pkg_a", "--output", str(out))
    assert result.exit_code == 0, result.stdout
    assert (out / "SKILL.md").is_file()
    assert not (cli_env / ".agents").exists()


def test_unknown_module_fails_and_writes_nothing(cli_env):
    _built_workspace(cli_env)
    result = _invoke("no_such_mod_qq")
    assert result.exit_code != 0
    assert "no_such_mod_qq" in result.stderr
    assert _skill_dirs(cli_env) == []


def test_empty_knowledge_still_packages_symbols(cli_env):
    _built_workspace(cli_env)
    result = _invoke("pkg_a")
    assert result.exit_code == 0, result.stdout
    text = _skill_text(cli_env)
    assert "zeta_hub" in text
