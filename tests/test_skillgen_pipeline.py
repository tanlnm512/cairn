"""Pipeline integration: ranked top-K selection and the pre-write critic gate.

Contract: ``assemble_draft`` selects symbols through ``rank_candidates`` —
centrality-ranked top-K, existence-filtered, ``ranking_tier`` carried from
``RankedSymbols.tier`` (``"unranked"`` only for an empty resolution) — and
the ``generate`` command runs ``verify_draft`` on the exact rendered bytes
between ``render_skill`` and the file write: rejection exits non-zero and
writes nothing (FR-002, FR-004).

C-04: no eager ``cairn.cli`` imports — the command group is imported
inside each test function.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from cairn.graph.builder import build_graph
from cairn.graph.schema import get_db
from cairn.memory.promotion import capture_memory
from cairn.okf.bundle import OKFBundle
from cairn.skillgen.assembly import assemble_draft
from cairn.skillgen.ranking import rank_candidates
from cairn.skillgen.selector import resolve_selector

HUB_CORE = (
    "def zeta_hub():\n"
    "    return 1\n"
    "\n"
    "def mid_one():\n"
    "    return zeta_hub()\n"
    "\n"
    "def mid_two():\n"
    "    return zeta_hub()\n"
    "\n"
    "def alpha_leaf():\n"
    "    return 2\n"
)

GHOST_MEMORY_TITLE = "pkg_a zeta_hub locking contract"
GHOST_MEMORY_BODY = (
    "zeta_hub callers must hold `ghost_lock_qq` first. Why: the packaged "
    "body must carry the planted ghost. How to apply: surface with pkg_a "
    "context."
)


def _indexed_hub(ws: Path) -> None:
    """pkg_a workspace: zeta_hub (two callers), mid_one/mid_two, alpha_leaf (none)."""
    repo = ws / "demo"
    (repo / ".git").mkdir(parents=True)
    (repo / "pkg_a").mkdir()
    (repo / "pkg_a" / "core.py").write_text(HUB_CORE)
    build_graph(workspace=str(ws), db_path=str(ws / "graph.db"), verbose=False)


def _indexed_big_mod(ws: Path) -> None:
    """big_mod workspace: 200 uncalled symbols qsym_001..qsym_200."""
    repo = ws / "demo"
    (repo / ".git").mkdir(parents=True)
    (repo / "big_mod").mkdir()
    (repo / "big_mod" / "mod.py").write_text(
        "".join(f"def qsym_{i:03d}():\n    return {i}\n\n" for i in range(1, 201))
    )
    build_graph(workspace=str(ws), db_path=str(ws / "graph.db"), verbose=False)


def _closure_conn(ws: Path):
    """Graph connection with the transitive closure materialised."""
    conn = get_db(str(ws / "graph.db"))
    from cairn.graph.dataflow import build_transitive_closure

    build_transitive_closure(conn)
    return conn


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """cwd in tmp, CAIRN_DB pointing at the fixture graph db."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAIRN_DB", str(tmp_path / "graph.db"))
    return tmp_path


def _invoke(*args):
    from cairn.cli.main import main

    return CliRunner().invoke(main, ["skill", "generate", *args])


def _skill_dirs(ws: Path) -> list[Path]:
    home = ws / ".agents" / "skills"
    if not home.is_dir():
        return []
    return [d for d in home.iterdir() if d.is_dir()]


def _skill_files(ws: Path) -> list[Path]:
    home = ws / ".agents" / "skills"
    return sorted(p for p in home.rglob("*") if p.is_file()) if home.is_dir() else []


def _skill_text(ws: Path) -> str:
    dirs = _skill_dirs(ws)
    assert len(dirs) == 1, f"expected one skill dir, got {[d.name for d in dirs]}"
    return (dirs[0] / "SKILL.md").read_text(encoding="utf-8")


def test_draft_ranking_tier_reflects_the_closure_tier(tmp_path):
    _indexed_hub(tmp_path)
    conn = _closure_conn(tmp_path)
    draft = assemble_draft(conn, resolve_selector(conn, "pkg_a"))
    assert draft.ranking_tier == "closure"
    conn.close()


def test_draft_ranking_tier_reflects_the_degree_tier(tmp_path):
    _indexed_hub(tmp_path)
    conn = get_db(str(tmp_path / "graph.db"))
    draft = assemble_draft(conn, resolve_selector(conn, "pkg_a"))
    assert draft.ranking_tier == "degree"
    conn.close()


def test_empty_resolution_keeps_the_unranked_tier(tmp_path):
    _indexed_hub(tmp_path)
    conn = _closure_conn(tmp_path)
    draft = assemble_draft(conn, resolve_selector(conn, "no_such_mod_qq"))
    assert draft.ranking_tier == "unranked"
    assert draft.symbols == []
    conn.close()


def test_draft_symbol_order_matches_rank_candidates_checkpoint(tmp_path):
    _indexed_hub(tmp_path)
    conn = _closure_conn(tmp_path)
    res = resolve_selector(conn, "pkg_a")
    ranked = rank_candidates(conn, res.candidates)
    assert assemble_draft(conn, res).symbols == ranked.symbols
    assert assemble_draft(conn, res, top_k=2).symbols == ranked.symbols[:2]
    conn.close()


def test_hub_symbol_ranks_above_the_alphabetically_first_leaf(cli_env):
    _indexed_hub(cli_env)
    _closure_conn(cli_env).close()
    result = _invoke("pkg_a")
    assert result.exit_code == 0, result.stdout
    text = _skill_text(cli_env)
    zeta = text.find("zeta_hub")
    alpha = text.find("alpha_leaf")
    assert zeta != -1 and alpha != -1
    assert zeta < alpha


def test_large_module_yields_a_bounded_top_k_skill(cli_env):
    _indexed_big_mod(cli_env)
    result = _invoke("big_mod")
    assert result.exit_code == 0, result.stdout
    listed = set(re.findall(r"qsym_\d{3}", _skill_text(cli_env)))
    assert 1 <= len(listed) < 200


def test_selector_naming_a_ghost_symbol_never_writes_it(cli_env):
    _indexed_hub(cli_env)
    assert _invoke("pkg_a").exit_code == 0
    result = _invoke("zeta_hub", "ghost_symbol_qq")
    assert result.exit_code != 0
    for path in _skill_files(cli_env):
        assert "ghost_symbol_qq" not in path.read_text(encoding="utf-8")


def test_unverifiable_body_ref_rejects_and_leaves_no_new_skill(cli_env):
    _indexed_hub(cli_env)
    assert _invoke("pkg_a").exit_code == 0
    before = _skill_dirs(cli_env)
    conn = get_db(str(cli_env / "graph.db"))
    capture_memory(
        conn,
        OKFBundle(str(cli_env / ".knowledge")),
        type_="pattern",
        title=GHOST_MEMORY_TITLE,
        body=GHOST_MEMORY_BODY,
        resource="pkg_a/core.py",
    )
    conn.close()
    result = _invoke("pkg_a")
    assert result.exit_code != 0
    assert "ghost_lock_qq" in result.stderr
    assert _skill_dirs(cli_env) == before
