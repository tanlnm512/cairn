"""Module resolution for `cairn compass generate` in multi-repo workspaces.

- bare module names resolve to one repo; ambiguity raises
- matching is segment-anchored, never substring
- one concept identity across all generation paths
- files.path is repo-relative here; test_compass_critic.py covers the
  legacy absolute-path form
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from cairn.compass.generator import (
    ModuleResolutionError,
    _cross_module_deps,
    _infer_repo,
    _resolve_module,
    _symbols_in_module,
    generate_compass,
    generate_compass_with_llm,
)
from cairn.okf.bundle import OKFBundle


class _PassCriticClient:
    """Minimal LLM client whose first draft passes the deterministic critic."""

    def synthesize(self, kind, facts):
        return (
            "# What Does This Module Do?\n- `agent_run` drives the module.\n"
            "# Common Modification Patterns\n- Edit `agent.py` carefully.\n"
            "# Build-Failure Patterns\n- None known.\n"
            "# Cross-Module Dependencies\n- Calls out via utils.\n"
            "# Tribal Knowledge\n- None yet.\n"
        )

    def revise(self, kind, draft, errors, facts):
        return self.synthesize(kind, facts)


def _row(conn, table, **cols):
    keys = ", ".join(cols)
    placeholders = ", ".join("?" for _ in cols)
    conn.execute(f"INSERT INTO {table} ({keys}) VALUES ({placeholders})", list(cols.values()))


def _seed_workspace(conn: sqlite3.Connection) -> None:
    """Two repos sharing a directory name (`app`), plus a substring decoy.

    polaris-app: app/adk/agent.py, happy/utils.py, trapper/decoy.py
    polaris-api: app/adk/api.py
    edges: agent_run -> api_handler (cross-repo), agent_run -> util_fn.
    """
    _row(conn, "repos", id="polaris-app", name="polaris-app", path="/work/polaris-app")
    _row(conn, "repos", id="polaris-api", name="polaris-api", path="/work/polaris-api")
    _row(conn, "files", id="f1", repo_id="polaris-app", path="app/adk/agent.py", language="python")
    _row(conn, "files", id="f2", repo_id="polaris-app", path="happy/utils.py", language="python")
    _row(conn, "files", id="f3", repo_id="polaris-app", path="trapper/decoy.py", language="python")
    _row(conn, "files", id="f4", repo_id="polaris-api", path="app/adk/api.py", language="python")
    _row(conn, "symbols", id="s1", file_id="f1", name="agent_run", qualified_name="adk.agent_run", kind="function", line_start=1, line_end=10)
    _row(conn, "symbols", id="s2", file_id="f2", name="util_fn", qualified_name="happy.util_fn", kind="function", line_start=1, line_end=10)
    _row(conn, "symbols", id="s3", file_id="f3", name="decoy_fn", qualified_name="trap.decoy_fn", kind="function", line_start=1, line_end=10)
    _row(conn, "symbols", id="s4", file_id="f4", name="api_handler", qualified_name="adk.api_handler", kind="function", line_start=1, line_end=10)
    _row(conn, "edges", id="e1", source_id="s1", target_id="s4", target_name="api_handler", kind="call", line=5, column=0, resolution="exact")
    _row(conn, "edges", id="e2", source_id="s1", target_id="s2", target_name="util_fn", kind="call", line=6, column=0, resolution="exact")
    conn.commit()


@pytest.fixture
def conn(fresh_db) -> sqlite3.Connection:
    _seed_workspace(fresh_db)
    return fresh_db


class TestModuleResolution:
    def test_bare_ambiguous_module_raises(self, conn):
        # Ambiguous bare name fails loudly instead of mixing repos.
        with pytest.raises(ModuleResolutionError) as exc:
            generate_compass("app", conn, OKFBundle("/tmp/k"))
        msg = str(exc.value)
        assert "polaris-app" in msg and "polaris-api" in msg
        assert "--repo" in msg

    def test_module_unique_to_one_repo_infers(self, conn):
        # A bare name that exists in exactly one repo resolves to it.
        assert _infer_repo(conn, "happy") == "polaris-app"
        assert _infer_repo(conn, "trapper") == "polaris-app"

    def test_repo_prefixed_module_infers_and_normalizes(self, conn):
        # `repo/module` resolves to (repo, repo-relative module).
        assert _resolve_module(conn, "polaris-app/app", None) == ("polaris-app", "app")

    def test_substring_path_not_matched(self, conn):
        # `app` must not match `trapper/decoy.py`.
        syms = _symbols_in_module(conn, "app", "polaris-app")
        names = {s["name"] for s in syms}
        assert names == {"agent_run"}

    def test_module_like_wildcards_are_literal(self, conn):
        # '%'/'_' in a module name are literals.
        _row(conn, "files", id="f9", repo_id="polaris-app", path="graph/core/mod.py", language="python")
        _row(conn, "symbols", id="s9", file_id="f9", name="core_fn", qualified_name="core.core_fn", kind="function", line_start=1, line_end=5)
        conn.commit()
        assert _symbols_in_module(conn, "graph_core", "polaris-app") == []
        assert {s["name"] for s in _symbols_in_module(conn, "graph/core", "polaris-app")} == {"core_fn"}

    def test_empty_module_path_rejected(self, conn, tmp_path):
        with pytest.raises(ModuleResolutionError):
            generate_compass("/", conn, OKFBundle(str(tmp_path / "k")))


class TestGenerateCompass:
    def test_repo_scoped_compass_not_polluted(self, conn, tmp_path):
        bundle = OKFBundle(str(tmp_path / "k"))
        concept = generate_compass("app", conn, bundle, repo="polaris-app")
        assert concept.tags[0] == "polaris-app"
        assert concept.resource == "app"
        assert "agent_run" in concept.body
        assert "api_handler" not in concept.body
        assert "decoy" not in concept.body

    def test_repo_prefixed_module_generates(self, conn, tmp_path):
        bundle = OKFBundle(str(tmp_path / "k"))
        concept = generate_compass("polaris-app/app", conn, bundle)
        assert concept.resource == "app"
        assert concept.tags[0] == "polaris-app"
        assert "agent_run" in concept.body
        assert "api_handler" not in concept.body

    def test_llm_path_identity_matches_deterministic(self, conn, tmp_path):
        # One module, one concept identity, regardless of generation path.
        bundle = OKFBundle(str(tmp_path / "k"))
        det = generate_compass("polaris-app/app", conn, bundle)
        fallback = generate_compass_with_llm("polaris-app/app", conn, bundle, client=None)
        assert fallback["mode"] == "deterministic"
        assert fallback["concept"].concept_id == det.concept_id == "compass/app"
        assert fallback["concept"].resource == det.resource == "app"
        assert fallback["concept"].tags == det.tags

        llm = generate_compass_with_llm(
            "polaris-app/app", conn, bundle, client=_PassCriticClient()
        )
        assert llm["mode"] == "llm"
        assert llm["concept"].concept_id == det.concept_id
        assert llm["concept"].resource == det.resource
        assert llm["concept"].tags == det.tags


def test_no_match_module_reports_empty_not_cross_repo(fresh_db, tmp_path):
    # Single-repo workspace: a no-match module still infers the repo.
    _row(fresh_db, "repos", id="solo", name="solo", path="/work/solo")
    _row(fresh_db, "files", id="sf1", repo_id="solo", path="app/agent.py", language="python")
    _row(fresh_db, "symbols", id="ss1", file_id="sf1", name="solo_fn", qualified_name="solo.solo_fn", kind="function", line_start=1, line_end=5)
    fresh_db.commit()
    concept = generate_compass("typo", fresh_db, OKFBundle(str(tmp_path / "k")))
    assert "(no symbols detected in this module)" in concept.body
    assert concept.tags[0] == "solo"


class TestCrossModuleDeps:
    def test_scoped_to_module_repo_with_labeled_cross_repo_target(self, conn):
        deps = _cross_module_deps(conn, "app", "polaris-app")
        # The cross-repo target keeps its repo-qualified label; the in-repo
        # target names the module it lives in.
        assert set(deps) == {"polaris-api/app/adk", "polaris-app/happy/utils.py"}

    def test_deterministic_template_passes_own_critic(self, conn, tmp_path):
        # The template backticks the repo-qualified dep labels; the critic's
        # repo bridge must validate them (previously: hallucinated path).
        from cairn.compass.critic import critic_concept

        bundle = OKFBundle(str(tmp_path / "k"))
        concept = generate_compass("app", conn, bundle, repo="polaris-app")
        result = critic_concept(concept, conn)
        assert result.errors == [], f"critic rejected graph-sourced body: {result.errors}"
        assert result.passed is True


class TestCompassGenerateCLI:
    def _setup(self, tmpdir: str):
        db = str(Path(tmpdir) / "test.db")
        know = str(Path(tmpdir) / ".knowledge")
        Path(know).mkdir(parents=True, exist_ok=True)
        from cairn.graph.schema import get_db
        c = get_db(db)
        _seed_workspace(c)
        c.close()
        return db, know

    def test_ambiguous_bare_module_exits_with_guidance(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            db, know = self._setup(tmp)
            from cairn.cli import main
            result = runner.invoke(main, ["compass", "generate", "app", "--db", db, "--knowledge", know])
            assert result.exit_code == 1
            assert "multiple repos" in result.output
            assert "--repo" in result.output
            # Nothing written.
            assert not list(Path(know).rglob("compass/*.md"))

    def test_use_llm_queue_path_ambiguity_fails_at_enqueue(self):
        # File-queue backend: ambiguity fails at enqueue time.
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            db, know = self._setup(tmp)
            from cairn.cli import main
            result = runner.invoke(
                main,
                ["compass", "generate", "app", "--use-llm", "--db", db, "--knowledge", know],
            )
            assert result.exit_code == 1
            assert "multiple repos" in result.output
            assert "Queued compass task" not in result.output
            assert not list(Path(know).rglob("compass/*.md"))

    def test_repo_scoped_generation_passes_critic(self):
        runner = CliRunner()
        with tempfile.TemporaryDirectory() as tmp:
            db, know = self._setup(tmp)
            from cairn.cli import main
            result = runner.invoke(
                main,
                ["compass", "generate", "app", "--repo", "polaris-app",
                 "--db", db, "--knowledge", know, "--dry-run"],
            )
            assert result.exit_code == 0, result.output
            assert "passed: True" in result.output
