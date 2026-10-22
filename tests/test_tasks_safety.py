"""Tests for task queue safety: critic integration and atomic claim.

Regression guards:
- complete_task must run critic and branch on CriticResult.errors
- claim_task must be atomic and record claimed_at timestamp

Tests follow TDD: they fail before the fix, then pass after.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from cairn.llm.tasks import (
    MAX_REVISE_CYCLES,
    claim_task,
    complete_task,
    create_task,
    list_tasks,
)
from cairn.okf.bundle import OKFBundle


def _seed_graph(conn: sqlite3.Connection) -> None:
    """Seed a minimal graph for critic validation."""
    conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'r1', '/tmp/r1')")
    conn.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES "
        "(1, 'r1', '/tmp/r1/src/graph/queries.py', 'python')"
    )
    conn.execute(
        "INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) "
        "VALUES (1, 1, 'queries', 'module', 'queries', 1, 100)"
    )
    conn.commit()


def _conn_with_fixture(fresh_db) -> sqlite3.Connection:
    _seed_graph(fresh_db)
    return fresh_db


def _create_bundle(tmp_path: Path) -> OKFBundle:
    """Create a test bundle in the temp directory."""
    knowledge_dir = tmp_path / ".knowledge"
    knowledge_dir.mkdir(exist_ok=True)
    tasks_dir = knowledge_dir / "_tasks"
    tasks_dir.mkdir(exist_ok=True)
    return OKFBundle(knowledge_dir)


# --- C1: Critic Integration Tests ---

class TestCompleteTaskCriticIntegration:
    """C1: complete_task runs critic and branches correctly."""

    def test_critic_on_pass_marks_status_and_returns_correct_dict(
        self, fresh_db, tmp_path
    ):
        """Critic passes: result marked with critic_status=passed and auto-promoted.

        A passing compass-synthesize task is auto-promoted into compass/<module>
        (see complete_task + the `cairn task complete` CLI branch that prints
        "completed and promoted"). Other task kinds are left for the caller to
        promote; a compass-synthesize pass is auto-promoted by the same contract.
        """
        conn = _conn_with_fixture(fresh_db)
        bundle = _create_bundle(tmp_path)

        # Create a task
        task = create_task(
            bundle,
            task_kind="compass-synthesize",
            resource="test",
            facts={"key_files": ["src/graph/queries.py"]},
        )
        # Claim it
        claim_task(bundle, task.id, "test-agent")

        # Provide a passing result (references real file)
        passing_result = (
            "# What Does This Module Do?\nSee `src/graph/queries.py`.\n"
            "# Common Modification Patterns\n...\n"
            "# Build-Failure Patterns\n...\n"
            "# Cross-Module Dependencies\n...\n"
            "# Tribal Knowledge\n...\n"
        )

        outcome = complete_task(bundle, task.id, passing_result, conn=conn)

        # Verify return dict shape (exactly these keys)
        assert set(outcome.keys()) == {
            "task_id",
            "promoted",
            "revised",
            "dropped",
            "errors",
            "quality",
        }
        assert outcome["task_id"] == task.id
        assert outcome["promoted"] is True  # Auto-promoted on pass (compass-synthesize)
        assert outcome["revised"] is False
        assert outcome["dropped"] is False
        assert outcome["errors"] == []  # No errors on pass
        assert outcome["quality"] >= 0.5  # Passing quality threshold

        # Verify result concept marked with critic_status extension
        result_concept = bundle.read_concept(task.result_concept_id)
        assert result_concept.extensions.get("critic_status") == "passed"

    def test_critic_fail_spawns_revise_when_below_max_cycles(
        self, fresh_db, tmp_path
    ):
        """Critic fails with attempt < MAX_REVISE_CYCLES: spawns revise task."""
        conn = _conn_with_fixture(fresh_db)
        bundle = _create_bundle(tmp_path)

        # Create a task with low attempt count
        task = create_task(
            bundle,
            task_kind="compass-synthesize",
            resource="test",
            facts={"key_files": ["src/graph/queries.py"]},
            parent_attempt=0,  # attempt will be 1
        )
        claim_task(bundle, task.id, "test-agent")

        # Provide a failing result (hallucinated file) but with missing sections to lower quality
        failing_result = (
            "See `src/DoesNotExist.kt` for details."
        )

        outcome = complete_task(bundle, task.id, failing_result, conn=conn)

        # Verify return dict shape
        assert set(outcome.keys()) == {
            "task_id",
            "promoted",
            "revised",
            "dropped",
            "errors",
            "quality",
        }
        assert outcome["task_id"] == task.id
        assert outcome["promoted"] is False
        assert outcome["revised"] is True  # Revised task spawned
        assert outcome["dropped"] is False
        assert len(outcome["errors"]) > 0  # Has errors

        # Verify a revise task was spawned
        tasks = list_tasks(bundle, status="pending")
        revise_tasks = [t for t in tasks if "revise" in t.task_kind]
        assert len(revise_tasks) == 1
        assert revise_tasks[0].task_kind == "compass-revise"

        # Verify result concept marked with critic_status extension
        result_concept = bundle.read_concept(task.result_concept_id)
        assert result_concept.extensions.get("critic_status") == "failed"

    def test_critic_checks_notes_section_of_task_results(self, fresh_db, tmp_path):
        """A hallucinated ref inside the result's `## Notes` fails the gate
        (the queue promotes the raw body, unlike spliced regeneration)."""
        conn = _conn_with_fixture(fresh_db)
        bundle = _create_bundle(tmp_path)

        task = create_task(
            bundle,
            task_kind="compass-synthesize",
            resource="test",
            facts={"key_files": ["src/graph/queries.py"]},
        )
        claim_task(bundle, task.id, "test-agent")

        passing = (
            "# What Does This Module Do?\nSee `src/graph/queries.py`.\n"
            "# Common Modification Patterns\n...\n"
            "# Build-Failure Patterns\n...\n"
            "# Cross-Module Dependencies\n...\n"
            "# Tribal Knowledge\n...\n"
        )
        result = passing + "\n## Notes\n- see `src/DoesNotExist.kt`\n"

        outcome = complete_task(bundle, task.id, result, conn=conn)

        assert outcome["revised"] is True
        assert any("src/DoesNotExist.kt" in e for e in outcome["errors"])
        assert outcome["promoted"] is False

    def test_critic_fail_drops_when_at_or_above_max_cycles(
        self, fresh_db, tmp_path
    ):
        """Critic fails with attempt >= MAX_REVISE_CYCLES: chain exhausted, the
        task persists the failed status (never a fake done)."""
        conn = _conn_with_fixture(fresh_db)
        bundle = _create_bundle(tmp_path)

        # Create a task at max attempt count
        task = create_task(
            bundle,
            task_kind="compass-synthesize",
            resource="test",
            facts={"key_files": ["src/graph/queries.py"]},
            parent_attempt=MAX_REVISE_CYCLES,  # attempt will be MAX_REVISE_CYCLES + 1
        )
        claim_task(bundle, task.id, "test-agent")

        # Provide a failing result (hallucinated file with low quality)
        failing_result = "See `src/DoesNotExist.kt`."

        outcome = complete_task(bundle, task.id, failing_result, conn=conn)

        # Verify return dict shape
        assert set(outcome.keys()) == {
            "task_id",
            "promoted",
            "revised",
            "dropped",
            "errors",
            "quality",
        }
        assert outcome["task_id"] == task.id
        assert outcome["promoted"] is False
        assert outcome["revised"] is False  # No revise spawned
        assert outcome["dropped"] is True  # Dropped
        assert len(outcome["errors"]) > 0

        # Verify no revise task was spawned
        tasks = list_tasks(bundle, status="pending")
        revise_tasks = [t for t in tasks if "revise" in t.task_kind]
        assert len(revise_tasks) == 0

        # The exhausted chain persists status failed, so the failed filter
        # finds it and the done vocabulary stays truthful.
        from cairn.llm.tasks import get_task

        assert get_task(bundle, task.id).status == "failed"
        assert [t.id for t in list_tasks(bundle, status="failed")] == [task.id]

    def test_exhausted_chain_emits_failed_lifecycle_event(
        self, fresh_db, tmp_path, monkeypatch
    ):
        """The exhausted-revise branch persists 'failed'; its lifecycle
        telemetry must carry the same event name, not 'dropped'."""
        conn = _conn_with_fixture(fresh_db)
        bundle = _create_bundle(tmp_path)

        events = []
        monkeypatch.setattr(
            "cairn.llm.tasks._emit",
            lambda *args, **kwargs: events.append(kwargs.get("event")),
        )

        task = create_task(
            bundle,
            task_kind="compass-synthesize",
            resource="test",
            facts={"key_files": ["src/graph/queries.py"]},
            parent_attempt=MAX_REVISE_CYCLES,
        )
        claim_task(bundle, task.id, "test-agent")
        outcome = complete_task(
            bundle, task.id, "See `src/DoesNotExist.kt`.", conn=conn
        )

        assert outcome["dropped"] is True
        assert events == ["claimed", "failed"]

# --- C2: Atomic Claim and claimed_at Tests ---

class TestCompleteTaskCriticFailureLeavesTaskReCompletable:
    """Two-kind contract: done is never written without a critic verdict.

    Reordering completion (result written -> critic runs -> done written
    only in an outcome branch) means a critic that raises mid-run leaves
    the task in-progress and re-completable — the old ordering marked done
    first, so a critic exception created a zombie that displayed queued
    forever and that retry could never reach."""

    def test_critic_exception_leaves_task_in_progress_and_recompletable(
        self, fresh_db, tmp_path, monkeypatch
    ):
        conn = _conn_with_fixture(fresh_db)
        bundle = _create_bundle(tmp_path)
        task = create_task(
            bundle,
            task_kind="compass-synthesize",
            resource="test",
            facts={"key_files": ["src/graph/queries.py"]},
        )
        claim_task(bundle, task.id, "test-agent")
        passing_result = (
            "# What Does This Module Do?\nSee `src/graph/queries.py`.\n"
            "# Common Modification Patterns\n...\n"
            "# Build-Failure Patterns\n...\n"
            "# Cross-Module Dependencies\n...\n"
            "# Tribal Knowledge\n...\n"
        )

        def _boom(*args, **kwargs):
            raise RuntimeError("critic exploded")

        monkeypatch.setattr("cairn.compass.critic.critic_concept", _boom)

        outcome = complete_task(bundle, task.id, passing_result, conn=conn)

        assert outcome == {
            "task_id": task.id,
            "promoted": False,
            "revised": False,
            "dropped": False,
            "errors": ["critic execution failed"],
            "quality": 0.0,
        }
        from cairn.llm.tasks import get_task

        reloaded = get_task(bundle, task.id)
        assert reloaded.status == "in-progress"
        # Re-completable: the same result completes successfully now.
        monkeypatch.setattr(
            "cairn.compass.critic.critic_concept",
            lambda *args, **kwargs: __import__(
                "cairn.compass.critic", fromlist=["CriticResult"]
            ).CriticResult(
                errors=[],
                warnings=[],
                quality_score=0.9,
                passed=True,
            ),
        )
        second = complete_task(bundle, task.id, passing_result, conn=conn)
        assert second["promoted"] is True
        assert get_task(bundle, task.id).status == "done"


class TestClaimTaskAtomicity:
    """C2: claim_task uses atomic filesystem primitive and records claimed_at."""

    def test_concurrent_claim_single_winner(self, tmp_path):
        """Two concurrent claims on the same pending task yield exactly one winner."""
        bundle = _create_bundle(tmp_path)

        # Create a pending task
        task = create_task(
            bundle,
            task_kind="compass-synthesize",
            resource="test",
            facts={},
        )

        # Track winners and claim counts
        winners = []
        claim_counts = {"winner": 0, "loser": 0}
        claim_lock = threading.Lock()
        barrier = threading.Barrier(2)  # Make both threads wait before claiming

        def try_claim(agent_name: str):
            try:
                barrier.wait()  # Wait for both threads to be ready
                claimed = claim_task(bundle, task.id, agent_name)
                if claimed:
                    with claim_lock:
                        winners.append(agent_name)
                        claim_counts["winner"] += 1
                else:
                    with claim_lock:
                        claim_counts["loser"] += 1
            except Exception:
                # Should not raise - atomic operation
                pass

        # Launch two threads trying to claim simultaneously
        threads = [
            threading.Thread(target=try_claim, args=("agent1",)),
            threading.Thread(target=try_claim, args=("agent2",)),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one winner
        assert len(winners) == 1
        assert claim_counts["winner"] == 1
        assert claim_counts["loser"] == 1

        # Task status is in-progress
        tasks_in_progress = list_tasks(bundle, status="in-progress")
        assert len(tasks_in_progress) == 1
        assert tasks_in_progress[0].id == task.id

    def test_re_claim_rejected(self, tmp_path):
        """An already-claimed task cannot be re-claimed."""
        bundle = _create_bundle(tmp_path)

        # Create and claim a task
        task = create_task(
            bundle,
            task_kind="compass-synthesize",
            resource="test",
            facts={},
        )
        first_claim = claim_task(bundle, task.id, "agent1")
        assert first_claim is not None
        assert first_claim.assigned_to == "agent1"

        # Second claim attempt fails
        second_claim = claim_task(bundle, task.id, "agent2")
        assert second_claim is None

        # Task still owned by first claimer
        tasks = list_tasks(bundle, status="in-progress")
        assert len(tasks) == 1
        assert tasks[0].assigned_to == "agent1"

    def test_claimed_at_set_and_persisted(self, tmp_path):
        """claim_task sets a claimed_at timestamp that persists through serialization."""
        bundle = _create_bundle(tmp_path)

        # Create and claim a task
        task = create_task(
            bundle,
            task_kind="compass-synthesize",
            resource="test",
            facts={},
        )
        claimed = claim_task(bundle, task.id, "agent1")

        # Verify claimed_at is set
        assert claimed is not None
        assert claimed.claimed_at != ""
        assert claimed.claimed_at is not None

        # Verify it's an ISO-8601 timestamp
        import re
        iso_pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z"
        assert re.match(iso_pattern, claimed.claimed_at)

        # Verify it persists through round-trip (re-read from bundle)
        task_from_disk = list_tasks(bundle, status="in-progress")
        assert len(task_from_disk) == 1
        assert task_from_disk[0].claimed_at == claimed.claimed_at

        # Verify it's persisted in extensions for programmatic access
        concept = bundle.read_concept(task.concept_id)
        assert concept.extensions.get("claimed_at") == claimed.claimed_at

    def test_stale_marker_and_stale_claim_reclaim_in_progress_task(self, tmp_path):
        """A claimant that died after writing in-progress is reclaimable once
        both its claim marker and its claimed_at pass the staleness horizon."""
        from cairn.llm.tasks import (
            CLAIM_STALE_SECONDS,
            _task_to_concept,
            get_task,
        )

        bundle = _create_bundle(tmp_path)
        task = create_task(
            bundle,
            task_kind="compass-synthesize",
            resource="test",
            facts={},
        )
        first = claim_task(bundle, task.id, "agent1")
        assert first is not None

        # Simulate the dead claimant: backdate the claim timestamp and marker.
        stale = get_task(bundle, task.id)
        stale.claimed_at = "2000-01-01T00:00:00Z"
        bundle.write_concept(_task_to_concept(stale))
        marker = bundle.root / "_tasks" / f"{task.id}.claim"
        old = time.time() - CLAIM_STALE_SECONDS - 60
        os.utime(marker, (old, old))

        second = claim_task(bundle, task.id, "agent2")

        assert second is not None
        assert second.assigned_to == "agent2"
        assert second.claimed_at != stale.claimed_at
        reloaded = get_task(bundle, task.id)
        assert reloaded.status == "in-progress"
        assert reloaded.assigned_to == "agent2"

    def test_stale_marker_with_fresh_claim_is_never_stolen(self, tmp_path):
        """A stale marker alone must not steal a live claim: claimed_at
        freshness gates the in-progress reclaim."""
        from cairn.llm.tasks import CLAIM_STALE_SECONDS, get_task

        bundle = _create_bundle(tmp_path)
        task = create_task(
            bundle,
            task_kind="compass-synthesize",
            resource="test",
            facts={},
        )
        assert claim_task(bundle, task.id, "agent1") is not None

        marker = bundle.root / "_tasks" / f"{task.id}.claim"
        old = time.time() - CLAIM_STALE_SECONDS - 60
        os.utime(marker, (old, old))

        assert claim_task(bundle, task.id, "agent2") is None
        assert get_task(bundle, task.id).assigned_to == "agent1"

    def test_drop_landing_in_claim_window_cannot_resurrect(self, tmp_path, monkeypatch):
        """A drop racing the claim's read-check-write serializes on the bundle
        lock: a successful drop is never followed by a resurrecting claim
        write (the task must not read in-progress after drop won)."""
        import cairn.llm.tasks as tasks_mod

        bundle = _create_bundle(tmp_path)
        task = create_task(
            bundle,
            task_kind="compass-synthesize",
            resource="test",
            facts={},
        )
        real_to_concept = tasks_mod._task_to_concept
        claim_writing = threading.Event()

        def slow_once(t):
            concept = real_to_concept(t)
            if t.status == "in-progress" and not claim_writing.is_set():
                claim_writing.set()
                time.sleep(0.3)  # widen the claim write window for the racer
            return concept

        monkeypatch.setattr(tasks_mod, "_task_to_concept", slow_once)
        drop_outcome = {}

        def do_drop():
            claim_writing.wait(timeout=5)
            drop_outcome["value"] = tasks_mod.drop_task(bundle, task.id)

        dropper = threading.Thread(target=do_drop)
        dropper.start()
        claimed = claim_task(bundle, task.id, "agent")
        dropper.join(timeout=10)

        dropped_ok = drop_outcome.get("value", {}).get("dropped") is True
        final_status = tasks_mod.get_task(bundle, task.id).status
        assert not (dropped_ok and final_status == "in-progress"), (
            "a successful drop must not be resurrected by the concurrent claim"
        )
        if dropped_ok and claimed is None:
            assert final_status == "dropped"


class TestTaskDataclassWithClaimedAt:
    """Verify Task dataclass has claimed_at field and it round-trips correctly."""



class TestCompleteTaskRefusalsAreNotDrops:
    """C31: a refused completion is reported as refused, never as a drop."""

    def test_complete_of_pending_task_refuses_not_drops(self, tmp_path):
        bundle = _create_bundle(tmp_path)
        task = create_task(bundle, "compass-synthesize", "test", facts={})

        outcome = complete_task(bundle, task.id, "irrelevant", claimer="agent")

        assert outcome["refused"] is True
        assert outcome["dropped"] is False
        assert "not in-progress" in outcome["errors"][0]
        from cairn.llm.tasks import get_task

        assert get_task(bundle, task.id).status == "pending"

    def test_ownership_mismatch_refuses_not_drops(self, tmp_path):
        bundle = _create_bundle(tmp_path)
        task = create_task(bundle, "compass-synthesize", "test", facts={})
        claim_task(bundle, task.id, "alice")

        outcome = complete_task(bundle, task.id, "irrelevant", claimer="bob")

        assert outcome["refused"] is True
        assert outcome["dropped"] is False
        assert "ownership mismatch" in outcome["errors"][0]
        from cairn.llm.tasks import get_task

        assert get_task(bundle, task.id).status == "in-progress"

    def test_matching_claimer_completes(self, tmp_path):
        bundle = _create_bundle(tmp_path)
        task = create_task(bundle, "compass-synthesize", "test", facts={})
        claim_task(bundle, task.id, "alice")

        outcome = complete_task(bundle, task.id, "irrelevant", claimer="alice")

        assert not outcome.get("refused")
        from cairn.llm.tasks import get_task

        assert get_task(bundle, task.id).status == "done"


class TestCompleteDropRaceSafety:
    """C32: concurrent complete/drop serialize on the bundle lock — exactly
    one terminal write wins and the loser reports a refusal, never both."""

    def test_concurrent_complete_and_drop_single_winner(self, tmp_path):
        bundle = _create_bundle(tmp_path)
        task = create_task(bundle, "compass-synthesize", "test", facts={})
        claim_task(bundle, task.id, "agent")
        barrier = threading.Barrier(2)
        outcomes = {}

        def do_complete():
            barrier.wait(timeout=5)
            outcomes["complete"] = complete_task(
                bundle, task.id, "# What Does This Module Do?\nprose\n"
            )

        def do_drop():
            barrier.wait(timeout=5)
            from cairn.llm.tasks import drop_task

            outcomes["drop"] = drop_task(bundle, task.id)

        worker = threading.Thread(target=do_complete)
        worker.start()
        do_drop()
        worker.join(timeout=10)

        complete_won = (
            not outcomes["complete"].get("refused")
            and outcomes["complete"]["dropped"] is False
        )
        drop_won = outcomes["drop"]["dropped"] is True
        assert complete_won != drop_won

        from cairn.llm.tasks import get_task

        final_status = get_task(bundle, task.id).status
        assert final_status == ("dropped" if drop_won else "done")


class TestLosingCompletionPersistsNoPromotions:
    """A completion that loses the terminal race is refused and must not
    leave its promotion writes behind."""

    def test_race_lost_promotions_do_not_persist(self, fresh_db, tmp_path, monkeypatch):
        """Another writer finishing the task first forces the refusal before
        any promotion write: the compass concept never lands."""
        from cairn.llm.tasks import _RACE_LOST, _task_to_concept, get_task

        conn = _conn_with_fixture(fresh_db)
        bundle = _create_bundle(tmp_path)
        task = create_task(
            bundle,
            task_kind="compass-synthesize",
            resource="test",
            facts={"key_files": ["src/graph/queries.py"]},
        )
        claim_task(bundle, task.id, "agent")

        real_write = bundle.write_concept

        def rival_finishes_then_write(concept):
            if concept.concept_id == task.result_concept_id:
                rival = get_task(bundle, task.id)
                rival.status = "done"
                rival.completed_at = "2000-01-01T00:00:00Z"
                real_write(_task_to_concept(rival))
            return real_write(concept)

        monkeypatch.setattr(bundle, "write_concept", rival_finishes_then_write)

        passing_result = (
            "# What Does This Module Do?\nSee `src/graph/queries.py`.\n"
            "# Common Modification Patterns\n...\n"
            "# Build-Failure Patterns\n...\n"
            "# Cross-Module Dependencies\n...\n"
            "# Tribal Knowledge\n...\n"
        )
        outcome = complete_task(bundle, task.id, passing_result, conn=conn)

        assert outcome["refused"] is True
        assert outcome["errors"] == [_RACE_LOST]
        with pytest.raises(FileNotFoundError):
            bundle.read_concept("compass/test")
