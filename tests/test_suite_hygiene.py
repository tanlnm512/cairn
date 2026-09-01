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


def test_infra_tier_shape_stays_guarded():
    """The infra marker tier must stay whole-file/class-shaped and off the t2 gate.

    The bench job runs ``pytest tests/ -q -k t2`` with NO ``-m`` filter, so an
    infra mark cannot deselect it today -- but a t2-named test inside an
    infra-marked module would break the moment anyone adds a global ``-m``
    filter (addopts), and a per-test infra mark would recreate the forbidden
    skip-quarantine shape (release-checklist bans per-test skips/xfails).
    Static AST checks keep the tier auditable:
    """
    import tomllib

    pyproject = tomllib.loads((TESTS_DIR.parent / "pyproject.toml").read_text(encoding="utf-8"))
    ini = pyproject["tool"]["pytest"]["ini_options"]
    assert any(str(m).startswith("infra:") for m in ini.get("markers", [])), (
        "infra marker unregistered in pyproject.toml"
    )
    assert "addopts" not in ini, (
        "addopts with a -m filter would silently deselect the bench job's "
        "-k t2 gate (it invokes pytest without -m)"
    )
    def _is_infra_mark(node):
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "infra"
        )

    violations = []
    for path, src in _iter_test_sources():
        tree = ast.parse(src)
        # marked iff pytestmark actually carries pytest.mark.infra (a
        # usefixtures-only pytestmark is NOT tier membership) or any
        # top-level class carries the infra decorator
        marked_module = any(
            isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in n.targets)
            and any(
                _is_infra_mark(d) or (
                    isinstance(d, ast.Attribute) and d.attr == "infra"
                )
                for d in ([n.value] if not isinstance(n.value, (ast.List, ast.Tuple)) else n.value.elts)
            )
            for n in tree.body
        ) or any(
            isinstance(n, ast.ClassDef)
            and any(_is_infra_mark(d) for d in n.decorator_list)
            for n in tree.body
        )
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                is_test = node.name.startswith("test")
                has_infra_mark = any(
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and d.func.attr == "infra"
                    for d in node.decorator_list
                )
                if is_test and has_infra_mark:
                    violations.append(f"{path.name}:{node.lineno} per-test infra mark")
                if is_test and marked_module and "t2" in node.name:
                    violations.append(f"{path.name}:{node.lineno} t2-named test in infra module")
    assert not violations, (
        "infra tier shape violations (per-test marks are quarantine-shaped; "
        f"t2-named tests must never sit in an infra module): {violations}"
    )
