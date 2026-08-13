import re
import subprocess
import sys
from pathlib import Path

# After the Phase 1.3 split, agent_install is a package (src/cairn/agent_install/).
# The _SLASH_COMMANDS constant lives in agent_install/_common.py and is the single
# source of truth for every client module. These tests check it is defined once and
# not duplicated as an inline literal anywhere in the package.
PKG_DIR = Path("src/cairn/agent_install")
COMMON_PY = PKG_DIR / "_common.py"


def test_slash_commands_constant_exists():
    """Verify _SLASH_COMMANDS constant is defined once in agent_install/_common.py."""
    result = subprocess.run(
        [sys.executable, "-c", r"""
import re, sys
with open(sys.argv[1]) as f:
    content = f.read()
# Check for constant definition (multi-line friendly)
constant_match = re.search(r'^_SLASH_COMMANDS\s*=\s*\[(.*?)\]', content, re.MULTILINE | re.DOTALL)
assert constant_match, "_SLASH_COMMANDS constant must be defined"
# Extract the constant value (handle multi-line)
constant_cmds = set(cmd.strip().strip('"\'') for cmd in constant_match.group(1).split(',') if cmd.strip())
# Verify it has the expected commands
expected = {'cairn', 'cairn-prep', 'cairn-ship', 'cairn-audit', 'cairn-refresh'}
assert constant_cmds == expected, f"_SLASH_COMMANDS must contain {expected}, got {constant_cmds}"
print("_SLASH_COMMANDS constant defined correctly")
""", str(COMMON_PY)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Test failed: {result.stderr}"


def test_slash_commands_constant_is_used():
    """Verify all slash command loops reference _SLASH_COMMANDS, not inline literals.

    Greps the whole agent_install/ package: the full five-element literal list may
    appear at most once (the definition in _common.py), nowhere else.
    """
    literal_pattern = r'["\']cairn["\']\s*,\s*["\']cairn-prep["\']\s*,\s*["\']cairn-ship["\']\s*,\s*["\']cairn-audit["\']\s*,\s*["\']cairn-refresh["\']'
    inline_matches = []
    for src in sorted(PKG_DIR.rglob("*.py")):
        content = src.read_text(encoding="utf-8")
        inline_matches.extend(re.findall(literal_pattern, content))
    # Allow the definition itself, but no other occurrences
    assert len(inline_matches) <= 1, (
        f"Found {len(inline_matches)} inline literal lists; should be at most 1 (the definition)"
    )
    print("No duplicate inline literal lists found")


def test_cairn_prep_grep_count():
    """Verify grep for 'cairn-prep' returns only 1 match (the constant definition).

    Greps the whole agent_install/ package — the token should appear only in the
    _SLASH_COMMANDS definition in _common.py, not duplicated in any client module.
    """

    matches = []
    for src in sorted(PKG_DIR.rglob("*.py")):
        for line in src.read_text(encoding="utf-8").splitlines():
            if "cairn-prep" in line:
                matches.append((src, line))
    # Should be 1 - only the constant definition
    assert len(matches) == 1, (
        f"grep for 'cairn-prep' returned {len(matches)} matches; should be 1 (constant only): {matches}"
    )
    print(f"grep count verified: {len(matches)} match")


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


if __name__ == "__main__":
    test_slash_commands_constant_exists()
    test_slash_commands_constant_is_used()
    test_cairn_prep_grep_count()
    # test_rm_tree_if_cairn_refuses_non_cairn_dir needs pytest's tmp_path fixture.
    print("Run pytest for the full suite (incl. tmp_path fixtures).")
    print("All tests passed!")
