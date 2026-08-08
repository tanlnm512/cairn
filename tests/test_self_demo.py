"""The 'cairn on cairn' self-demo (Phase 2.3) — cairn indexes itself, verbatim.

This is both the flagship demo (a visitor can run these exact commands on
cairn's own repo and see real output) AND a regression guard (it runs under
`-m core` in CI, so the demo cannot silently rot as cairn evolves). It builds
cairn's own source tree in an isolated temp DB, then asserts that the core
query commands return correct, non-empty results for known symbols.

The target symbols are real and verified to exist in cairn's own tree:
  - `build_graph`     — src/cairn/graph/builder.py (the build entry point)
  - `critic_concept`  — src/cairn/compass/critic.py (the critic gate)

Note on isolation: we pass explicit `--db` / `--workspace` flags rather than
relying on `CAIRN_HOME`, because `cairn.paths.CAIRN_HOME` is bound at module
import — setting the env var in-process (as CliRunner does) has no effect.
Explicit flags are deterministic and don't touch the user's ~/.cairn.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from cairn.cli import main

# Runs in the `-m core` smoke subset so the demo is gated in CI.
pytestmark = pytest.mark.core


def _cairn_repo_root() -> Path:
    """The cairn repo root (this file lives at <root>/tests/test_self_demo.py)."""
    return Path(__file__).resolve().parent.parent


def test_self_demo_build_and_query():
    """Build cairn on itself, then exercise def / impact on real symbols.

    This is the verbatim walkthrough referenced from the README quick-start.
    """
    runner = CliRunner()
    repo = _cairn_repo_root()
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.kg"
        build_common = ["--db", str(db), "--workspace", str(repo)]
        query_common = ["--db", str(db)]

        # 1. Build cairn's own graph.
        build = runner.invoke(main, ["build", *build_common], catch_exceptions=False)
        assert build.exit_code == 0, build.output
        # The build summary reports resolved edges — the core of promise #1.
        assert "edges resolved" in build.output

        # 2. find_definition: build_graph is defined and the def command finds it.
        def_result = runner.invoke(
            main, ["def", "build_graph", *query_common], catch_exceptions=False
        )
        assert def_result.exit_code == 0, def_result.output
        assert "builder" in def_result.output  # defined in graph/builder.py

        # 3. impact_analysis: build_graph is reachable.
        impact = runner.invoke(
            main, ["impact", "build_graph", *query_common], catch_exceptions=False
        )
        assert "build_graph" in impact.output

        # 4. The critic gate exists and is queryable.
        critic_def = runner.invoke(
            main, ["def", "critic_concept", *query_common], catch_exceptions=False
        )
        assert critic_def.exit_code == 0, critic_def.output
        assert "critic" in critic_def.output  # defined in compass/critic.py


def test_self_demo_resolution_invariant_holds():
    """On cairn's own freshly-built graph, no exact edge has a NULL target_id.

    This is promise #1 of the verification contract, demonstrated on cairn's
    own code — the strongest possible dogfood.
    """
    runner = CliRunner()
    repo = _cairn_repo_root()
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "test.kg"
        common = ["--db", str(db), "--workspace", str(repo)]
        build = runner.invoke(main, ["build", *common], catch_exceptions=False)
        assert build.exit_code == 0, build.output

        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        violations = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE resolution = 'exact' AND target_id IS NULL"
        ).fetchone()[0]
        conn.close()
        assert violations == 0, (
            f"{violations} exact edge(s) with NULL target_id in cairn's own graph "
            "— the verification contract is violated on cairn's own code"
        )
