"""Renderer node-id contract: distinct nodes never collapse to one rendered id."""
from __future__ import annotations


def _colliding_graph():
    return {
        "nodes": [
            {"id": "a-b", "kind": "function"},
            {"id": "a_b", "kind": "function"},
        ],
        "edges": [{"source": "a-b", "target": "a_b", "kind": "calls"}],
        "metadata": {"scope": "symbol"},
    }


def _mermaid_node_ids(out: str) -> list[str]:
    ids = []
    for line in out.splitlines():
        s = line.strip()
        if not s or s.startswith("graph ") or "-->" in s:
            continue
        ids.append(s.split("(", 1)[0])
    return ids


def _mermaid_edge_ends(out: str) -> list[tuple[str, str]]:
    ends = []
    for line in out.splitlines():
        if "-->" not in line:
            continue
        left, right = line.strip().split("-->", 1)
        ends.append((left.strip(), right.split("|")[-1].strip()))
    return ends


def test_mermaid_distinct_nodes_keep_distinct_ids():
    from cairn.viz.renderers import to_mermaid

    out = to_mermaid(_colliding_graph())
    assert len(set(_mermaid_node_ids(out))) == 2
    assert all(s != t for s, t in _mermaid_edge_ends(out))


def test_dot_distinct_nodes_keep_distinct_ids():
    from cairn.viz.renderers import to_dot

    out = to_dot(_colliding_graph())
    ids = [
        line.strip().split(" ", 1)[0]
        for line in out.splitlines()
        if "[label=" in line
    ]
    assert len(set(ids)) == 2
    assert "a_b -> a_b_1;" in out


if __name__ == "__main__":
    import pytest

    pytest.main([__file__, "-v"])
