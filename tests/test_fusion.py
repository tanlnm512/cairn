
from cairn.graph.fusion import rrf_fuse


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


def test_weights_shift_ranking():
    list1 = ["a", "b"]
    list2 = ["b", "a"]
    # With higher weight on list2, "b" should come first
    fused = rrf_fuse([list1, list2], k=60, weights=[1.0, 2.0])
    assert fused[0][0] == "b"
    assert fused[1][0] == "a"


