"""Suite contract tests for the SWE-bench bench arm.

Pins the public contract of ``src/cairn/bench/swe_bench_suite.py`` -- the
module-level ``run_swe_bench_suite`` export the bench CLI dispatches to and
the CI smoke drives:

1. Signature: ``run_swe_bench_suite(tasks, workspaces, db_path, *, runs=3)``
   returning the report payload dict the CLI stamps and prints.
   - ``tasks``: iterable of the loader's five-field task dicts
     (``instance_id``, ``repo``, ``base_commit``, ``problem_statement``,
     ``environment_setup_commit``) -- injected here, never fetched.
   - ``workspaces``: mapping ``instance_id -> repo tree`` both arms probe
     (one shared fixture tree; the real path maps the loader's checked-out
     caches).
2. Per-task rows in input order, each keyed by ``instance_id``, with
   ArmEffort-shaped arms (``{"tool_calls", "chars", "est_tokens",
   "wall_ms"}``) and reduction percentages (``{"calls_pct", "tokens_pct",
   "time_ratio"}``).
3. Cross-task medians for tokens and tool calls, both arms (FR-001).
4. Determinism: two full runs over fresh builds yield identical per-task
   calls/est_tokens and medians (FR-002).
5. Validation: duplicate instance ids and unmapped workspaces raise
   ``ValueError``.
6. Offline contract: the injected-task path never imports ``datasets`` (no
   fetch branch); the suite restores the env vars it pins.

The swe-bench report lives at its own payload level (D-007): key sets are
pinned exactly only on the swe-bench-owned shapes (arms, reduction, medians
arms); the suite's top level is pinned as a superset floor. Shared
agent-suite ``to_dict`` shapes gain no keys.

Hermetic (C-04): corpus and DB under ``tmp_path``, hash embed, no network,
no subprocess patching; ``datasets`` is hidden via ``sys.modules``, never
imported.
"""
from __future__ import annotations

import json
import os
import statistics
import sys

import pytest

from cairn.bench.agent_suite import CHARS_PER_TOKEN
from cairn.bench.corpus import generate_corpus
from cairn.bench.swe_bench_suite import run_swe_bench_suite

pytestmark = pytest.mark.infra

# Small enough that build+embed+tasks stay fast, large enough that every
# fixture task finds cross-file caller evidence.
N_FILES = 5
RUNS = 2

ARM_KEYS = {"tool_calls", "chars", "est_tokens", "wall_ms"}
REDUCTION_KEYS = {"calls_pct", "tokens_pct", "time_ratio"}


def _fixture_corpus(root):
    """Generated corpus plus three probe targets.

    Targets prefer class names present in at least two files, so caller
    probes have evidence in both arms; the tail of all names keeps the
    count at three for any corpus size.
    """
    repo = generate_corpus(root, N_FILES, complexity="low")
    counts: dict = {}
    for path in sorted(repo.rglob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("class "):
                name = line.split()[1].split("(")[0].rstrip(":")
                counts[name] = counts.get(name, 0) + 1
    names = sorted(counts)
    preferred = [n for n in names if counts[n] >= 2]
    targets = (preferred + [n for n in names if counts[n] < 2])[:3]
    return repo, targets


def _fixture_tasks(targets):
    """Three five-field task dicts (the loader's output contract).

    Each problem statement names exactly one corpus identifier, capitalized
    as its first word, so identifier extraction has a single candidate.
    """
    rows = [
        ("django__django-16379", "django/django"),
        ("sympy__sympy-24102", "sympy/sympy"),
        ("astropy__astropy-14995", "astropy/astropy"),
    ]
    return [
        {
            "instance_id": instance_id,
            "repo": repo,
            "base_commit": "c" * 40,
            "environment_setup_commit": "e" * 40,
            "problem_statement": (
                f"{target} raises when the migration optimizer reorders operations."
            ),
        }
        for (instance_id, repo), target in zip(rows, targets)
    ]


def _fixture(tmp_path, name):
    repo, targets = _fixture_corpus(tmp_path / name)
    tasks = _fixture_tasks(targets)
    workspaces = {t["instance_id"]: str(repo) for t in tasks}
    return tasks, workspaces, str(tmp_path / f"{name}.db")


def _run_fixture(tmp_path, name, runs=RUNS):
    tasks, workspaces, db = _fixture(tmp_path, name)
    return run_swe_bench_suite(tasks, workspaces, db, runs=runs)


class TestSweBenchSuite:
    def test_both_arms_report_nonzero_effort_per_task(self, tmp_path):
        payload = _run_fixture(tmp_path, "effort")
        assert len(payload["tasks"]) == 3
        for row in payload["tasks"]:
            for arm in ("cairn", "control"):
                effort = row[arm]
                assert effort["tool_calls"] >= 1
                assert effort["chars"] > 0
                assert effort["est_tokens"] >= 1
                # The documented token proxy: chars / 4.
                assert effort["est_tokens"] == effort["chars"] // CHARS_PER_TOKEN
                assert effort["wall_ms"] >= 0

    def test_rows_follow_input_order_keyed_by_instance_id(self, tmp_path):
        tasks, workspaces, db = _fixture(tmp_path, "order")
        payload = run_swe_bench_suite(tasks, workspaces, db, runs=1)
        assert [row["instance_id"] for row in payload["tasks"]] == [
            t["instance_id"] for t in tasks
        ]

    def test_medians_cover_tokens_and_tool_calls_for_both_arms(self, tmp_path):
        payload = _run_fixture(tmp_path, "medians")
        assert set(payload["medians"]) == {"cairn", "control"}
        for arm in ("cairn", "control"):
            reported = payload["medians"][arm]
            assert set(reported) == ARM_KEYS
            for field in ("tool_calls", "chars", "est_tokens"):
                assert reported[field] == statistics.median(
                    row[arm][field] for row in payload["tasks"]
                )

    def test_two_runs_identical_calls_and_tokens(self, tmp_path):
        """Full reruns over fresh builds reproduce calls and est_tokens
        exactly (FR-002); wall time is advisory and excluded."""
        first = _run_fixture(tmp_path, "det_a")
        second = _run_fixture(tmp_path, "det_b")
        assert [r["instance_id"] for r in first["tasks"]] == [
            r["instance_id"] for r in second["tasks"]
        ]
        for ra, rb in zip(first["tasks"], second["tasks"]):
            for arm in ("cairn", "control"):
                assert ra[arm]["tool_calls"] == rb[arm]["tool_calls"]
                assert ra[arm]["est_tokens"] == rb[arm]["est_tokens"]
        for arm in ("cairn", "control"):
            for field in ("tool_calls", "est_tokens"):
                assert first["medians"][arm][field] == second["medians"][arm][field]

    def test_injected_tasks_run_without_datasets(self, tmp_path, monkeypatch):
        """Injected tasks bypass the fetch branch entirely: the suite runs
        with the optional dependency absent (mirrors the loader's offline
        contract)."""
        monkeypatch.setitem(sys.modules, "datasets", None)
        payload = _run_fixture(tmp_path, "offline")
        assert len(payload["tasks"]) == 3

    def test_env_pins_restored_after_run(self, tmp_path, monkeypatch):
        """The suite snapshots and restores the env vars it pins, like
        ``run_agent_suite``."""
        sentinels = {
            "CAIRN_DB": str(tmp_path / "pre.db"),
            "CAIRN_EMBED_BACKEND": "sentinel",
            "CAIRN_RERANK": "0",
        }
        for var, val in sentinels.items():
            monkeypatch.setenv(var, val)
        _run_fixture(tmp_path, "envdisc")
        for var, val in sentinels.items():
            assert os.environ[var] == val

    def test_duplicate_instance_ids_rejected(self, tmp_path):
        repo, targets = _fixture_corpus(tmp_path / "dup")
        tasks = _fixture_tasks(targets)
        workspaces = {t["instance_id"]: str(repo) for t in tasks}
        tasks[1]["instance_id"] = tasks[0]["instance_id"]
        with pytest.raises(ValueError):
            run_swe_bench_suite(tasks, workspaces, str(tmp_path / "dup.db"), runs=1)

    def test_unmapped_workspace_rejected(self, tmp_path):
        repo, targets = _fixture_corpus(tmp_path / "unmapped")
        tasks = _fixture_tasks(targets)
        workspaces = {tasks[0]["instance_id"]: str(repo)}
        with pytest.raises(ValueError):
            run_swe_bench_suite(tasks, workspaces, str(tmp_path / "unmapped.db"), runs=1)


class TestSuitePayload:
    def test_payload_level_and_floor_keys(self, tmp_path):
        payload = _run_fixture(tmp_path, "payload")
        assert isinstance(payload, dict)
        assert set(payload) >= {"tasks", "medians", "runs", "chars_per_token"}
        assert payload["runs"] == RUNS
        assert payload["chars_per_token"] == CHARS_PER_TOKEN

    def test_row_shapes(self, tmp_path):
        payload = _run_fixture(tmp_path, "shapes")
        for row in payload["tasks"]:
            assert set(row) >= {"instance_id", "cairn", "control", "reduction"}
            for arm in ("cairn", "control"):
                assert set(row[arm]) == ARM_KEYS
            assert set(row["reduction"]) == REDUCTION_KEYS

    def test_reduction_math_consistent_with_rows(self, tmp_path):
        payload = _run_fixture(tmp_path, "reduction")
        for row in payload["tasks"]:
            c, k = row["cairn"], row["control"]
            assert row["reduction"]["calls_pct"] == pytest.approx(
                (1 - c["tool_calls"] / k["tool_calls"]) * 100, abs=0.06
            )
            assert row["reduction"]["tokens_pct"] == pytest.approx(
                (1 - c["est_tokens"] / k["est_tokens"]) * 100, abs=0.06
            )

    def test_payload_serializes_for_stamping(self, tmp_path):
        payload = _run_fixture(tmp_path, "json")
        assert json.loads(json.dumps(payload)) == payload
