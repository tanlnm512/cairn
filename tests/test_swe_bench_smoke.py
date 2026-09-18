"""CI smoke for the swe-bench bench arm: one end-to-end suite run.

Runs ``run_swe_bench_suite`` over a tiny local fixture task set via its
injected ``tasks``/``workspaces`` -- probe-target extraction, graph build,
both scripted arms, per-task rows, cross-task medians -- and pins the
payload shape (the suite contract pinned in tests/test_swe_bench_suite.py:
floor keys, ArmEffort arm shape, reduction keys, medians derived from the
rows, JSON-serializable for the CLI-applied stamp) plus non-zero effort for
both arms.

Marked ``core`` (the self-demo CI-gating pattern): collected by the per-PR
``pytest -m core`` leg, so the suite cannot silently rot (FR-004, D-005).
``datasets`` is hidden for the run: the ``-m core`` leg has no ``datasets``
guarantee, and the injected-task path must never enter the fetch branch.

Hermetic (C-04): workspace and DB under ``tmp_path`` inside the suite-wide
``_hermetic_env`` sandbox; no network, no subprocesses, nothing patched but
``sys.modules``. Self-contained by design, like the other ``core`` files.
"""
from __future__ import annotations

import json
import statistics
import sys

import pytest

from cairn.bench.agent_suite import CHARS_PER_TOKEN
from cairn.bench.swe_bench_suite import run_swe_bench_suite

pytestmark = pytest.mark.core

ARM_KEYS = {"tool_calls", "chars", "est_tokens", "wall_ms"}
REDUCTION_KEYS = {"calls_pct", "tokens_pct", "time_ratio"}

INSTANCE_IDS = ["fixture__repo-1", "fixture__repo-2"]

ROUTER_PY = '''\
class SignalRouter:
    def __init__(self):
        self.routes = {}

    def route(self, name):
        return self.routes.get(name)


def dispatch_event(router, name):
    return router.route(name)
'''

WORKER_PY = '''\
from dispatch import SignalRouter, dispatch_event


def run_worker():
    return dispatch_event(SignalRouter(), "worker")
'''

STATEMENTS = {
    "fixture__repo-1": "dispatch_event drops frames when the router rebuilds.",
    "fixture__repo-2": "SignalRouter loses routes after a rebuild.",
}


def _fixture_workspace(root):
    """Tiny two-file tree whose identifiers the fixture statements name."""
    repo = root / "fixture-repo"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "dispatch.py").write_text(ROUTER_PY, encoding="utf-8")
    (repo / "worker.py").write_text(WORKER_PY, encoding="utf-8")
    return repo


def _fixture_tasks():
    """The loader's five-field task dicts, injected -- never fetched."""
    return [
        {
            "instance_id": instance_id,
            "repo": "cairn-fixture/fixture-repo",
            "base_commit": "c" * 40,
            "environment_setup_commit": "e" * 40,
            "problem_statement": STATEMENTS[instance_id],
        }
        for instance_id in INSTANCE_IDS
    ]


@pytest.fixture
def smoke_payload(tmp_path, monkeypatch):
    """One suite run over the fixture tasks, with ``datasets`` absent."""
    monkeypatch.setitem(sys.modules, "datasets", None)
    repo = _fixture_workspace(tmp_path / "ws")
    tasks = _fixture_tasks()
    workspaces = {t["instance_id"]: str(repo) for t in tasks}
    return run_swe_bench_suite(
        tasks, workspaces, str(tmp_path / "smoke.db"), runs=1
    )


def test_swe_bench_smoke_reports_both_arms_end_to_end(smoke_payload):
    payload = smoke_payload
    assert set(payload) >= {"tasks", "medians", "runs", "chars_per_token"}
    assert payload["runs"] == 1
    assert payload["chars_per_token"] == CHARS_PER_TOKEN

    # Per-task rows in input order, ArmEffort arms, non-zero effort (FR-001).
    assert [row["instance_id"] for row in payload["tasks"]] == INSTANCE_IDS
    for row in payload["tasks"]:
        assert set(row) >= {"instance_id", "cairn", "control", "reduction"}
        for arm in ("cairn", "control"):
            assert set(row[arm]) == ARM_KEYS
            assert row[arm]["tool_calls"] >= 1
            assert row[arm]["chars"] > 0
            assert row[arm]["est_tokens"] >= 1
        assert set(row["reduction"]) == REDUCTION_KEYS

    # Cross-task medians for tokens and tool calls, both arms.
    assert set(payload["medians"]) == {"cairn", "control"}
    for arm in ("cairn", "control"):
        assert set(payload["medians"][arm]) == ARM_KEYS
        for field in ("tool_calls", "est_tokens"):
            assert payload["medians"][arm][field] == statistics.median(
                row[arm][field] for row in payload["tasks"]
            )

    # The CLI stamps and persists this payload: it must round-trip JSON.
    assert json.loads(json.dumps(payload)) == payload
