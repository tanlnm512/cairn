"""Skill draft assembly from compass, symbols, and memory (FR-001).

Contract: ``assemble_draft(conn, resolution, top_k, top_n) -> SkillDraft``
with fields ``module``, ``compass_body``, ``symbols``, ``memories``,
``ranking_tier``. ``symbols`` are the centrality-ranked top-K (FR-002) of
the resolved candidates (score desc, qualified name asc); ``ranking_tier``
names the tier that scored them, ``"unranked"`` only when the resolution
has no candidates. Memories are top-N (default 5) via ``search_memory`` at
default tiers — superseded stay hidden. Knowledge sections are optional
content, not preconditions: an empty knowledge store yields empty compass
and memory sections while symbols still package.
"""
from __future__ import annotations

from dataclasses import fields as dataclass_fields

from cairn.compass.generator import generate_compass
from cairn.graph.builder import build_graph
from cairn.graph.schema import get_db
from cairn.memory.promotion import capture_memory
from cairn.okf.bundle import OKFBundle
from cairn.skillgen.assembly import DEFAULT_TOP_K, SkillDraft, assemble_draft
from cairn.skillgen.selector import resolve_selector

MARKER = "qxzephyrline"


def _two_pkg_conn(tmp_path):
    """Workspace: pkg_a/{zeta_hub, ZetaNest.alpha_leaf}, pkg_b/{beta_one},
    plus a root-level top_q so module derivation has a root-file case."""
    repo = tmp_path / "demo"
    (repo / ".git").mkdir(parents=True)
    (repo / "pkg_a").mkdir()
    (repo / "pkg_b").mkdir()
    (repo / "pkg_a" / "zeta.py").write_text(
        "def zeta_hub() -> str:\n"
        "    return 'zeta'\n"
        "\n"
        "class ZetaNest:\n"
        "    def alpha_leaf(self) -> str:\n"
        "        return 'leaf'\n"
    )
    (repo / "pkg_b" / "beta.py").write_text(
        "def beta_one() -> str:\n"
        "    return 'beta'\n"
    )
    (repo / "top_q.py").write_text(
        "def top_q() -> int:\n"
        "    return 1\n"
    )
    db_path = tmp_path / "graph.db"
    build_graph(workspace=str(tmp_path), db_path=str(db_path), verbose=False)
    return get_db(str(db_path))


def _knowledge(tmp_path):
    """Bundle beside the fixture db: <db dir>/.knowledge."""
    return OKFBundle(str(tmp_path / ".knowledge"))


def _record_marker_memory(conn, tmp_path, body=None):
    return capture_memory(
        conn,
        _knowledge(tmp_path),
        type_="pattern",
        title=f"{MARKER} zeta-hub load contract",
        body=body or (
            "zeta_hub callers depend on its return value. Why: packaged "
            "context must carry the module memory. How to apply: surface it "
            "when pkg_a context is loaded."
        ),
        resource="pkg_a/zeta.py",
    )


def _compass_for_pkg_a(conn, tmp_path):
    concept = generate_compass("pkg_a", conn, _knowledge(tmp_path))
    _knowledge(tmp_path).write_concept(concept)


def test_draft_exposes_the_pinned_fields(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    draft = assemble_draft(conn, resolve_selector(conn, "pkg_a"))
    assert isinstance(draft, SkillDraft)
    assert {f.name for f in dataclass_fields(SkillDraft)} == {
        "module",
        "compass_body",
        "symbols",
        "memories",
        "ranking_tier",
    }


def test_symbols_are_ranked_top_k_from_candidates(tmp_path):
    from cairn.skillgen.ranking import rank_candidates

    conn = _two_pkg_conn(tmp_path)
    res = resolve_selector(conn, "pkg_a")
    draft = assemble_draft(conn, res)
    assert set(draft.symbols) <= set(res.candidates)
    assert len(draft.symbols) <= DEFAULT_TOP_K
    assert draft.symbols == rank_candidates(conn, res.candidates).symbols[:DEFAULT_TOP_K]
    capped = assemble_draft(conn, res, top_k=1)
    assert len(capped.symbols) == 1
    assert capped.symbols[0] in res.candidates


def test_default_top_k_caps_large_modules_at_twenty(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    (tmp_path / "demo" / "big").mkdir()
    lines = []
    for i in range(25):
        lines.append(f"def qsym_{i:02d}() -> int:\n    return {i}\n\n")
    (tmp_path / "demo" / "big" / "mod.py").write_text("".join(lines))
    db_path = tmp_path / "graph.db"
    build_graph(workspace=str(tmp_path), db_path=str(db_path), verbose=False)
    conn = get_db(str(db_path))
    res = resolve_selector(conn, "big")
    assert len(res.candidates) >= 21
    draft = assemble_draft(conn, res)
    assert len(draft.symbols) == 20


def test_module_is_the_common_directory_of_candidates(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    assert assemble_draft(conn, resolve_selector(conn, "pkg_a")).module == "pkg_a"
    assert assemble_draft(conn, resolve_selector(conn, "pkg_a/")).module == "pkg_a"
    cross = assemble_draft(conn, resolve_selector(conn, "zeta_hub beta_one"))
    assert cross.module == ""
    root = assemble_draft(conn, resolve_selector(conn, "top_q"))
    assert root.module == ""


def test_compass_body_carries_the_module_guide(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    _compass_for_pkg_a(conn, tmp_path)
    draft = assemble_draft(conn, resolve_selector(conn, "pkg_a"))
    assert draft.module == "pkg_a"
    assert "What Does This Module Do?" in draft.compass_body


def test_memories_carry_title_and_body_of_module_memory(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    _record_marker_memory(conn, tmp_path)
    draft = assemble_draft(conn, resolve_selector(conn, "pkg_a"))
    assert len(draft.memories) == 1
    assert MARKER in draft.memories[0]
    assert "zeta_hub callers depend on its return value" in draft.memories[0]


def test_top_n_caps_memories(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    _record_marker_memory(conn, tmp_path)
    _record_marker_memory(conn, tmp_path, body="second distinct memory payload")
    draft = assemble_draft(conn, resolve_selector(conn, "pkg_a"), top_n=1)
    assert len(draft.memories) == 1


def test_superseded_memories_stay_hidden(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    _record_marker_memory(conn, tmp_path, body="qqoldbodymarker superseded payload")
    new = _record_marker_memory(conn, tmp_path)
    old = [
        cid
        for cid in _knowledge(tmp_path).list_concepts(prefix="memory/")
        if cid != new["path"]
    ]
    assert len(old) == 1
    assert (
        _knowledge(tmp_path).read_concept(old[0]).extensions["memory_is_latest"]
        is False
    )
    draft = assemble_draft(conn, resolve_selector(conn, "pkg_a"))
    assert len(draft.memories) == 1
    assert MARKER in draft.memories[0]
    assert "qqoldbodymarker" not in "\n".join(draft.memories)


def test_empty_knowledge_store_yields_empty_knowledge_sections(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    draft = assemble_draft(conn, resolve_selector(conn, "pkg_a"))
    assert draft.symbols
    assert draft.compass_body == ""
    assert draft.memories == []


def test_unmatched_only_resolution_yields_an_empty_draft(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    draft = assemble_draft(conn, resolve_selector(conn, "no_such_mod_qq"))
    assert draft.module == ""
    assert draft.compass_body == ""
    assert draft.symbols == []
    assert draft.memories == []
    assert draft.ranking_tier == "unranked"


def test_assembly_is_read_only(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    _compass_for_pkg_a(conn, tmp_path)
    _record_marker_memory(conn, tmp_path)
    res = resolve_selector(conn, "pkg_a")
    before = conn.total_changes
    assemble_draft(conn, res)
    assemble_draft(conn, res, top_k=1, top_n=1)
    assert conn.total_changes == before


def test_assembly_is_deterministic(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    _compass_for_pkg_a(conn, tmp_path)
    _record_marker_memory(conn, tmp_path)
    res = resolve_selector(conn, "pkg_a")
    assert assemble_draft(conn, res) == assemble_draft(conn, res)


def test_non_positive_caps_are_rejected(tmp_path):
    conn = _two_pkg_conn(tmp_path)
    res = resolve_selector(conn, "pkg_a")
    try:
        assemble_draft(conn, res, top_k=0)
        raise AssertionError("top_k=0 must raise")
    except ValueError:
        pass
    try:
        assemble_draft(conn, res, top_n=0)
        raise AssertionError("top_n=0 must raise")
    except ValueError:
        pass
