from pathlib import Path

# After the Phase 1.3 split, agent_install is a package (src/cairn/agent_install).
# The _SLASH_COMMANDS constant lives in agent_install/_common.py and is the single
# source of truth for every client module. These tests check it is defined once and
# not duplicated as an inline literal anywhere in the package.
PKG_DIR = Path("src/cairn/agent_install")
COMMON_PY = PKG_DIR / "_common.py"


def test_rm_tree_if_cairn_refuses_non_cairn_dir(tmp_path):
    """_rm_tree_if_cairn must NOT delete a directory that isn't cairn-scoped.

    Regression for the guard-in-name-only footgun: the function promised a
    content check but deleted any existing dir. A broader path must be refused.
    """
    from cairn.agent_install.merge import _rm_tree_if_cairn
    from cairn.agent_install._common import InstallResult

    # A user directory NOT named 'cairn' -- must survive.
    victim = tmp_path / ".claude"
    victim.mkdir()
    (victim / "settings.json").write_text("{}")
    res = InstallResult(client="test")
    _rm_tree_if_cairn(victim, res)
    assert victim.exists(), "non-cairn directory was deleted"
    assert (victim / "settings.json").exists()
    assert any("not cairn-scoped" in n for n in res.notes)

    # A cairn-named directory -- removed as before.
    skill = tmp_path / ".claude" / "skills" / "cairn"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("x")
    res2 = InstallResult(client="test")
    _rm_tree_if_cairn(skill, res2)
    assert not skill.exists()
    assert any("removed" in w for w in res2.written)
