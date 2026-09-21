"""Enforcing closure-budget gate: the scaling suite's closure op must stay
within the recorded wall/memory budget at the 1000-file gate point, and the
deterministic 5x structural-edge synthesis must preserve graph shape."""
from __future__ import annotations

import sqlite3

import pytest

from cairn.bench import run_scaling_suite
from cairn.bench import scaling_suite as scaling_suite_mod
from cairn.bench.scaling_suite import CLOSURE_EDGE_FACTOR, synthesize_structural_edges

# The gate point: medium complexity, exactly this corpus size.
GATE_FILES = 1000


def _structural_kinds() -> tuple[str, ...]:
    from cairn.graph.traversal import STRUCTURAL_EDGE_KINDS

    return STRUCTURAL_EDGE_KINDS


def _count_structural(conn: sqlite3.Connection) -> int:
    kinds = _structural_kinds()
    ph = ",".join("?" * len(kinds))
    return conn.execute(
        f"SELECT COUNT(*) FROM edges WHERE kind IN ({ph})", kinds
    ).fetchone()[0]


def _count_structural_by_source(conn: sqlite3.Connection) -> dict:
    kinds = _structural_kinds()
    ph = ",".join("?" * len(kinds))
    return dict(
        conn.execute(
            f"SELECT source_id, COUNT(*) FROM edges WHERE kind IN ({ph})"
            " GROUP BY source_id",
            kinds,
        ).fetchall()
    )


def _built_conn(tmp_path, monkeypatch, n_files: int = 12) -> sqlite3.Connection:
    """Build a small seeded corpus graph and return an open Row-factory conn."""
    from cairn.bench.corpus import generate_corpus
    from cairn.graph.builder import build_graph

    repo = generate_corpus(tmp_path / "corpus", n_files, complexity="medium")
    db = str(tmp_path / "gate.db")
    monkeypatch.setenv("CAIRN_DB", db)
    build_graph(workspace=str(repo), db_path=db)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def _run_gate_point(tmp_path, monkeypatch, sizes) -> object:
    # Pin CAIRN_DB: run_scaling_suite points os.environ at its throwaway DBs,
    # and only monkeypatch restores the pre-test value in this shared process.
    monkeypatch.setenv("CAIRN_DB", str(tmp_path / "gate_env.db"))
    monkeypatch.setenv("CAIRN_EMBED_BACKEND", "hash")
    report = run_scaling_suite(
        tmp_path / "run", sizes=sizes, complexity="medium", embed_backend="hash",
    )
    return report.points[0]


def _assert_closure_budget(point) -> None:
    wall_budget = scaling_suite_mod.CLOSURE_BUDGET_WALL_SECONDS
    peak_budget = scaling_suite_mod.CLOSURE_BUDGET_PEAK_MB
    assert point.closure_edges > 0, "closure op ran against no structural edges"
    assert point.closure_seconds <= wall_budget, (
        f"closure wall {point.closure_seconds:.1f}s at {point.n_files} files "
        f"exceeds budget {wall_budget:.0f}s"
    )
    assert point.closure_peak_memory_mb <= peak_budget, (
        f"closure peak memory {point.closure_peak_memory_mb:.1f} MB at "
        f"{point.n_files} files exceeds budget {peak_budget:.0f} MB"
    )
    maintain_budget = scaling_suite_mod.CLOSURE_MAINTAIN_BUDGET_WALL_SECONDS
    assert point.closure_maintain_seconds <= maintain_budget, (
        f"closure maintenance wall {point.closure_maintain_seconds:.3f}s at "
        f"{point.n_files} files exceeds budget {maintain_budget:.1f}s"
    )


@pytest.mark.infra
def test_closure_budget_gate_at_1000_files(tmp_path, monkeypatch):
    """The enforcing gate: closure at 5x structural-edge volume on the
    1000-file medium corpus must stay within the recorded budget."""
    point = _run_gate_point(tmp_path, monkeypatch, sizes=(GATE_FILES,))
    assert point.n_files == GATE_FILES
    _assert_closure_budget(point)


def test_over_budget_closure_fails_the_gate(tmp_path, monkeypatch):
    """An over-budget closure fails the gate assertion (enforced, not
    advisory): a zero budget must trip on any completed closure run."""
    monkeypatch.setattr(scaling_suite_mod, "CLOSURE_BUDGET_WALL_SECONDS", 0.0)
    point = _run_gate_point(tmp_path, monkeypatch, sizes=(6,))
    with pytest.raises(AssertionError, match="exceeds budget"):
        _assert_closure_budget(point)


class TestSynthesizeStructuralEdges:
    def test_exact_factor_volume_and_hub_fanout(self, tmp_path, monkeypatch):
        """Every source's structural out-degree scales by exactly the factor
        (hub fan-out preserved) and total structural volume hits factor x."""
        conn = _built_conn(tmp_path, monkeypatch)
        try:
            before_by_src = _count_structural_by_source(conn)
            before = _count_structural(conn)
            assert before > 0

            inserted = synthesize_structural_edges(conn)

            assert inserted == (CLOSURE_EDGE_FACTOR - 1) * before
            assert _count_structural(conn) == CLOSURE_EDGE_FACTOR * before
            after_by_src = _count_structural_by_source(conn)
            assert set(after_by_src) == set(before_by_src)
            for src, n in before_by_src.items():
                assert after_by_src[src] == CLOSURE_EDGE_FACTOR * n
        finally:
            conn.close()

    def test_replica_targets_cyclically_shifted_over_sorted_ids(
        self, tmp_path, monkeypatch
    ):
        """Replica k of a resolved edge targets the k-th next symbol id in
        sorted order, under a fresh id, carrying that symbol's name."""
        conn = _built_conn(tmp_path, monkeypatch)
        try:
            synthesize_structural_edges(conn)
            ids = sorted(r[0] for r in conn.execute("SELECT id FROM symbols"))
            pos = {sid: i for i, sid in enumerate(ids)}
            kinds = _structural_kinds()
            ph = ",".join("?" * len(kinds))
            orig = conn.execute(
                f"SELECT id, target_id FROM edges WHERE kind IN ({ph})"
                " AND target_id IS NOT NULL LIMIT 1",
                kinds,
            ).fetchone()
            assert orig is not None
            for k in range(1, CLOSURE_EDGE_FACTOR):
                replica = conn.execute(
                    "SELECT target_id, target_name FROM edges WHERE id = ?",
                    (f"{orig['id']}~synth{k}",),
                ).fetchone()
                assert replica is not None
                expected = ids[(pos[orig["target_id"]] + k) % len(ids)]
                assert replica["target_id"] == expected
                assert replica["target_name"] == conn.execute(
                    "SELECT name FROM symbols WHERE id = ?", (expected,)
                ).fetchone()[0]
        finally:
            conn.close()

    def test_deterministic_on_identical_db_state(self, tmp_path, monkeypatch):
        """Synthesis is a pure function of the DB state: two byte-identical
        DBs get identical replica sets (edge ids themselves are not stable
        across rebuilds, so the state is cloned, not rebuilt)."""
        import shutil

        conn = _built_conn(tmp_path, monkeypatch)
        conn.close()
        original = tmp_path / "gate.db"
        clone = tmp_path / "clone.db"
        shutil.copyfile(original, clone)

        sets = []
        for path in (original, clone):
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            try:
                synthesize_structural_edges(conn)
                rows = conn.execute(
                    "SELECT id, source_id, target_id, target_name FROM edges"
                    " WHERE id LIKE '%~synth%' ORDER BY id"
                ).fetchall()
                sets.append([tuple(r) for r in rows])
            finally:
                conn.close()
        assert sets[0], "synthesis produced no replicas"
        assert sets[0] == sets[1]
