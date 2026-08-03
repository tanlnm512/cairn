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
    import re

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


if __name__ == "__main__":
    test_slash_commands_constant_exists()
    test_slash_commands_constant_is_used()
    test_cairn_prep_grep_count()
    print("All tests passed!")
