import tempfile
from pathlib import Path

from cairn.graph.schema import get_db
from cairn.parsers.scip_importer import import_scip_data
from cairn.memory.store import consolidate_memories
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept


def test_scip_importer():
    """Regression: the SCIP importer must produce a REAL resolution, not the
    legacy placeholder that hardcoded resolution='exact' for every edge.

    This fixture has helper as a reference (symbol_roles=0) with NO definition in
    the payload, so the correct resolution is 'unresolved' (the reference points
    at something the index doesn't define). The pre-rewrite importer wrongly
    tagged this 'exact'; see docs/scip-hybrid-plan.md §Bugs and
    BUGS.md#2026-08-06/scip-importer-fake-resolution. Cross-file exact resolution
    is covered by tests/test_scip_importer.py against real protobuf indexes.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        conn = get_db(db_path)

        scip_payload = {
            "documents": [
                {
                    "relative_path": "src/main.py",
                    "language": "python",
                    "occurrences": [
                        {"symbol": "scip-python python repo main main_func#", "symbol_roles": 1, "range": [0, 0, 0, 9]},
                        {"symbol": "scip-python python repo main helper#", "symbol_roles": 0, "range": [5, 4, 5, 10]},
                    ]
                }
            ]
        }

        stats = import_scip_data(conn, scip_payload)
        assert stats["files_added"] == 1
        assert stats["symbols_added"] == 1  # only main_func is a definition
        assert stats["edges_added"] == 1

        # helper has no definition in the payload -> real resolution is
        # 'unresolved', NOT the legacy placeholder 'exact'.
        edge = conn.execute("SELECT resolution FROM edges WHERE target_name = 'helper'").fetchone()
        assert edge is not None
        assert edge["resolution"] == "unresolved"


def test_memory_consolidation():
    with tempfile.TemporaryDirectory() as tmpdir:
        bundle = OKFBundle(tmpdir)

        c1 = OKFConcept(type="RawMemory", title="Fix DB Lock", body="Use WAL mode for sqlite", concept_id="memory/raw/fix-db-lock-1")
        c2 = OKFConcept(type="RawMemory", title="Fix DB Lock", body="Set busy_timeout to 5000ms", concept_id="memory/raw/fix-db-lock-2")
        bundle.write_concept(c1)
        bundle.write_concept(c2)

        consolidated = consolidate_memories(bundle)
        assert consolidated == 2

        tribal = bundle.list_concepts(prefix="memory/tribal")
        assert len(tribal) == 1
        merged = bundle.read_concept(tribal[0])
        assert "WAL mode" in merged.body
        assert "busy_timeout" in merged.body
