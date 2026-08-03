import tempfile
from pathlib import Path
import sqlite3

from cairn.graph.embeddings import chunk_for_symbol
from cairn.graph.schema import get_db
from cairn.graph.dataflow import build_transitive_closure
from cairn.parsers.scip_importer import import_scip_data
from cairn.memory.store import consolidate_memories
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept


def test_scope_enriched_chunking():
    # Construct sqlite3.Row mock-like dict
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("CREATE TABLE t (kind TEXT, qualified_name TEXT, name TEXT, file_path TEXT, parent_scope TEXT, imports_summary TEXT)")
    cur.execute("INSERT INTO t VALUES ('function', 'Auth.verify', 'verify', 'src/auth.py', 'Class Auth', 'jwt, datetime')")
    row = cur.execute("SELECT * FROM t").fetchone()

    chunk = chunk_for_symbol(row, variant="C")
    assert "File: src/auth.py" in chunk
    assert "Enclosing Scope: Class Auth" in chunk
    assert "Imports: jwt, datetime" in chunk


def test_transitive_closure_builder():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        conn = get_db(db_path)

        # Setup 3-hop chain: s1 -> s2 -> s3
        conn.execute("INSERT INTO repos (id, name, path) VALUES ('r1', 'r1', '/p')")
        conn.execute("INSERT INTO files (id, path, repo_id, hash, line_count, language) VALUES ('f1', 'a.py', 'r1', 'h', 10, 'python')")
        conn.execute("INSERT INTO symbols (id, file_id, name, kind) VALUES ('s1', 'f1', 'FuncA', 'function')")
        conn.execute("INSERT INTO symbols (id, file_id, name, kind) VALUES ('s2', 'f1', 'FuncB', 'function')")
        conn.execute("INSERT INTO symbols (id, file_id, name, kind) VALUES ('s3', 'f1', 'FuncC', 'function')")

        conn.execute("INSERT INTO edges (id, source_id, target_name, kind, line) VALUES ('e1', 's1', 'FuncB', 'call', 1)")
        conn.execute("INSERT INTO edges (id, source_id, target_name, kind, line) VALUES ('e2', 's2', 'FuncC', 'call', 2)")
        conn.commit()

        inserted = build_transitive_closure(conn, max_depth=3)
        assert inserted >= 3

        # Verify s1 reaches FuncC at distance 2
        row = conn.execute("SELECT distance FROM transitive_edges WHERE source_id = 's1' AND target_name = 'FuncC'").fetchone()
        assert row is not None
        assert row["distance"] == 2


def test_scip_importer():
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
        assert stats["symbols_added"] == 1
        assert stats["edges_added"] == 1

        exact_edge = conn.execute("SELECT resolution FROM edges WHERE target_name = 'helper'").fetchone()
        assert exact_edge is not None
        assert exact_edge["resolution"] == "exact"


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
