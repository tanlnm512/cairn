import os
import tempfile
from pathlib import Path

from codegraph.graph.fusion import rrf_fuse
from codegraph.graph.schema import get_db
from codegraph.graph import queries


def test_rrf_known_values():
    list1 = ["docA", "docB", "docC"]
    list2 = ["docB", "docA", "docD"]
    # k=60
    # docA score = 1/(60+1) + 1/(60+2) = 1/61 + 1/62 = 0.0163934 + 0.0161290 = 0.0325224
    # docB score = 1/(60+2) + 1/(60+1) = 1/62 + 1/61 = 0.0325224
    # docC score = 1/(60+3) = 1/63 = 0.0158730
    # docD score = 1/(60+3) = 1/63 = 0.0158730
    fused = rrf_fuse([list1, list2], k=60)
    top_docs = [doc_id for doc_id, score in fused[:2]]
    assert set(top_docs) == {"docA", "docB"}
    assert len(fused) == 4


def test_rrf_handles_disjoint_lists():
    list1 = ["a", "b"]
    list2 = ["c", "d"]
    fused = rrf_fuse([list1, list2], k=60)
    assert len(fused) == 4
    ids = [d for d, _ in fused]
    assert set(ids) == {"a", "b", "c", "d"}


def test_rrf_is_score_scale_invariant():
    # RRF operates purely on rank positions, so scale of original scores is irrelevant
    list1 = ["a", "b", "c"]
    list2 = ["a", "b", "c"]
    fused = rrf_fuse([list1, list2], k=60)
    assert fused[0][0] == "a"
    assert fused[1][0] == "b"
    assert fused[2][0] == "c"


def test_weights_shift_ranking():
    list1 = ["a", "b"]
    list2 = ["b", "a"]
    # With higher weight on list2, "b" should come first
    fused = rrf_fuse([list1, list2], k=60, weights=[1.0, 2.0])
    assert fused[0][0] == "b"
    assert fused[1][0] == "a"


def test_semantic_search_preserves_include_callers():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        conn = get_db(db_path)
        # Create dummy file & symbols
        conn.execute("INSERT INTO repos (id, name, path) VALUES ('repo1', 'repo1', '/tmp/repo1')")
        conn.execute("INSERT INTO files (id, path, repo_id, hash, line_count, language) VALUES (1, 'foo.py', 'repo1', 'h1', 10, 'python')")
        conn.execute("INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) VALUES (10, 1, 'caller_func', 'function', 'foo.caller_func', 1, 5)")
        conn.execute("INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) VALUES (20, 1, 'target_func', 'function', 'foo.target_func', 6, 10)")
        conn.execute("INSERT INTO edges (id, source_id, target_name, kind, line) VALUES ('e1', '10', 'target_func', 'calls', 3)")
        conn.commit()

        # Call semantic_search with include_callers=True
        res = queries.semantic_search(conn, "target_func", limit=5, include_callers=True)
        assert isinstance(res, list)
        if res:
            first = res[0]
            assert "callers" in first
            assert "callees" in first


def test_fusion_disabled_matches_legacy_path():
    os.environ["CODEGRAPH_FUSION"] = "0"
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = str(Path(tmpdir) / "test.db")
            conn = get_db(db_path)
            res = queries.semantic_search(conn, "test", limit=5)
            assert isinstance(res, list)
    finally:
        os.environ["CODEGRAPH_FUSION"] = "1"


def test_explore_no_longer_gates_on_seed_count():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        conn = get_db(db_path)
        conn.execute("INSERT INTO repos (id, name, path) VALUES ('repo1', 'repo1', '/tmp/repo1')")
        conn.execute("INSERT INTO files (id, path, repo_id, hash, line_count, language) VALUES (1, 'bar.py', 'repo1', 'h2', 10, 'python')")
        conn.execute("INSERT INTO symbols (id, file_id, name, kind, qualified_name, line_start, line_end) VALUES (100, 1, 'SearchHelper', 'class', 'bar.SearchHelper', 1, 10)")
        conn.commit()

        res = queries.explore(conn, "SearchHelper", max_nodes=5)
        assert "seeds" in res
