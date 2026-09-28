"""Hygiene guards for the test suite itself.

The suite-wide ``_hermetic_env`` fixture in conftest.py makes the clean-runner
environment the default; this file adds STATIC tripwires for the known footgun
patterns so a regression in the fixture's coverage fails loudly instead of
resurfacing on a CI runner.

Each guard targets a class of environment-dependent failure -- extend the
banned-pattern list when a new class bites.
"""

from __future__ import annotations

import ast
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent


def _iter_test_sources():
    for p in sorted(TESTS_DIR.rglob("test_*.py")):
        if p.name == Path(__file__).name:
            continue  # don't scan the guard itself
        yield p, p.read_text(encoding="utf-8")


def test_no_json_loads_on_interleaved_cli_output():
    """click's ``Result.output`` interleaves stdout+stderr.

    Leaked DEBUG log lines break ``json.loads`` under full-suite ordering on CI,
    while real-world stdout stays pure JSON.
    Parse ``result.stdout`` instead. Simple AST check: a ``loads(...)`` call
    whose sole argument chains attribute access ending in ``.output``.
    """
    violations = []
    for path, src in _iter_test_sources():
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "loads"
                and len(node.args) == 1
                and isinstance(node.args[0], ast.Attribute)
                and node.args[0].attr == "output"
            ):
                violations.append(f"{path.relative_to(TESTS_DIR.parent)}:{node.lineno}")
    assert not violations, (
        "json.loads(...) on `.output` found (interleaved stdout+stderr; parse "
        f"result.stdout instead): {violations}"
    )


