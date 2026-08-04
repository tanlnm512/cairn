"""Tests for task queue safety: critic integration and atomic claim.

Regression guards:
- complete_task must run critic and branch on CriticResult.errors
- claim_task must be atomic and record claimed_at timestamp

Tests follow TDD: they fail before the fix, then pass after.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path


from cairn.llm.tasks import (
    MAX_REVISE_CYCLES,
    Task,
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
        promote. This test was previously written asserting `promoted is False`,
        which contradicted the production behavior; the assertion now matches
        the intended auto-promotion contract.
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

    def test_critic_fail_drops_when_at_or_above_max_cycles(
        self, fresh_db, tmp_path
    ):
        """Critic fails with attempt >= MAX_REVISE_CYCLES: drops task."""
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

    def test_return_dict_shape_exact_match(self, fresh_db, tmp_path):
        """Return dict must be exactly {task_id, promoted, revised, dropped, errors, quality}."""
        conn = _conn_with_fixture(fresh_db)
        bundle = _create_bundle(tmp_path)

        task = create_task(
            bundle,
            task_kind="compass-synthesize",
            resource="test",
            facts={"key_files": ["src/graph/queries.py"]},
        )
        claim_task(bundle, task.id, "test-agent")

        result = (
            "# What Does This Module Do?\nSee `src/graph/queries.py`.\n"
            "# Common Modification Patterns\n...\n"
            "# Build-Failure Patterns\n...\n"
            "# Cross-Module Dependencies\n...\n"
            "# Tribal Knowledge\n...\n"
        )

        outcome = complete_task(bundle, task.id, result, conn=conn)

        # Exact key match - no extra keys, no missing keys
        expected_keys = {"task_id", "promoted", "revised", "dropped", "errors", "quality"}
        actual_keys = set(outcome.keys())
        assert actual_keys == expected_keys

        # Verify value types
        assert isinstance(outcome["task_id"], str)
        assert isinstance(outcome["promoted"], bool)
        assert isinstance(outcome["revised"], bool)
        assert isinstance(outcome["dropped"], bool)
        assert isinstance(outcome["errors"], list)
        assert isinstance(outcome["quality"], (int, float))


# --- C2: Atomic Claim and claimed_at Tests ---

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


class TestTaskDataclassWithClaimedAt:
    """Verify Task dataclass has claimed_at field and it round-trips correctly."""

    def test_task_dataclass_has_claimed_at_field(self):
        """Task dataclass must have claimed_at field."""
        task = Task(
            id="test123",
            task_kind="compass-synthesize",
            resource="test",
        )
        # Should have claimed_at field (even if empty initially)
        assert hasattr(task, "claimed_at")
        # Initially empty when created directly
        assert task.claimed_at == ""
