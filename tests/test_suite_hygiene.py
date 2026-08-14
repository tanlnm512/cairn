"""Hygiene guards for the test suite itself (prevention layer, 2026-08-14).

Two CI failures on one branch came from tests that were green locally only
because of the dev machine's surroundings (agent CLIs detected; interleaved
CLI output parsed as JSON). The suite-wide ``_hermetic_env`` fixture in
conftest.py makes the clean-runner environment the default; this file adds
STATIC tripwires for the known footgun patterns so a regression in the
fixture's coverage fails loudly instead of resurfacing on a CI runner.

Each guard corresponds to a real incident -- extend the banned-pattern list
when a new class bites (that is the whole point: every incident becomes a
permanent tripwire).
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

    Incidents (2026-08-14): leaked DEBUG log lines broke ``json.loads`` only
    in full-suite order, on CI, while real-world stdout stays pure JSON.
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


def test_hermetic_fixture_remains_autouse():
    """The clean-runner default must stay suite-wide and un-deactivated.

    If someone deletes the autouse flag or renames the fixture, environment
    dependence silently returns. Guard the contract from the inside.
    """
    conf = (TESTS_DIR / "conftest.py").read_text(encoding="utf-8")
    assert "@pytest.fixture(autouse=True)" in conf, "autouse fixture missing from conftest.py"
    assert "def _hermetic_env(" in conf, "_hermetic_env fixture was renamed/removed"
    # The fixture must actually gate on the opt-out marker -- an unmarked
    # fixture that ignores real_env would break the documented escape hatch.
    assert "real_env" in conf, "real_env opt-out marker handling missing"


def test_agent_cli_names_covered_by_fixture():
    """Every shutil.which() name detect.py probes must be blocked by the fixture.

    A new client added to detect.py without extending _AGENT_CLIS re-opens the
    detection-dependence hole on dev machines that have that CLI installed.
    """
    detect_src = (
        TESTS_DIR.parent / "src" / "cairn" / "agent_install" / "detect.py"
    ).read_text(encoding="utf-8")
    import re

    probed = set(re.findall(r'shutil\.which\("([a-z-]+)"\)', detect_src))
    conf = (TESTS_DIR / "conftest.py").read_text(encoding="utf-8")
    blocked = set(re.findall(r'"([a-z-]+)"', conf.split("_AGENT_CLIS = ")[1].split("\n")[0]))
    missing = probed - blocked
    assert not missing, (
        f"detect.py probes {sorted(probed)} but _hermetic_env only blocks "
        f"{sorted(blocked)} -- add {sorted(missing)} to _AGENT_CLIS in conftest.py"
    )
