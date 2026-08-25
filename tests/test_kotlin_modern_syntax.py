"""ERROR/MISSING-node scan over the modern Kotlin-syntax fixtures.

The fixture directory is enumerated, not hardcoded, so every fixture is
covered mechanically. Fixtures are arranged around known grammar
limitations (full-form KEEP-0438 destructuring parses ERROR-free only as
the first statement of a block; navigation-expression rename sources
error even there) -- a failing fixture is a grammar regression to
surface, never a fixture to weaken.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from tree_sitter import Language, Parser

import cairn._tree_sitter_kotlin

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "kotlin" / "modern"

# Parse with the vendored fwcd grammar module directly, not the registry
# default; the module remains the Kotlin grammar source after any
# registry flip.
_PARSER = Parser(Language(cairn._tree_sitter_kotlin.language()))

FIXTURES = sorted(FIXTURE_DIR.glob("*.kt"))


def _count_missing_nodes(root) -> int:
    count = 0
    stack = [root]
    while stack:
        node = stack.pop()
        if node.is_missing:
            count += 1
        stack.extend(node.children)
    return count


@pytest.mark.parametrize("fixture", FIXTURES, ids=lambda path: path.name)
def test_fixture_parses_without_error_or_missing_nodes(fixture: Path) -> None:
    tree = _PARSER.parse(fixture.read_bytes())
    assert not tree.root_node.has_error, (
        f"{fixture.name}: parse tree contains ERROR nodes"
    )
    assert _count_missing_nodes(tree.root_node) == 0, (
        f"{fixture.name}: parse tree contains MISSING nodes"
    )


def test_fixture_directory_is_populated() -> None:
    # An empty directory would make the parametrized scan vacuously pass.
    assert FIXTURES, f"no .kt fixtures found under {FIXTURE_DIR}"
