"""Determinism of the default generate path (FR-003, TC-009).

Contract: the same selector against the same store yields byte-identical
output files — across immediate reruns and across processes running under
different hash seeds — with ranking ordered score desc then qualified name
asc, memories in ``search_memory`` rank order, and no timestamps in the
draft or frontmatter. The default path never constructs an LLM client:
with every client constructor patched to raise, generation still succeeds.

C-04: no eager ``cairn.cli`` imports — the command group is imported
inside each test function. The suite ``_hermetic_env`` fixture sandboxes
CAIRN_HOME; ``cli_env`` adds cwd + CAIRN_DB so the CLI resolves the
fixture graph, never the live store.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from cairn.graph.builder import build_graph
from cairn.graph.schema import get_db
from cairn.memory.promotion import capture_memory
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept

CORE_SRC = (
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


def _indexed_workspace(ws: Path) -> None:
    """pkg_a fixture: zeta_hub has transitive impact, the other three symbols
    tie at zero; a compass concept and two memories feed the other sections."""
    (ws / ".git").mkdir(parents=True)
    (ws / "pkg_a").mkdir()
    (ws / "pkg_a" / "core.py").write_text(CORE_SRC)
    build_graph(workspace=str(ws), db_path=str(ws / "graph.db"), verbose=False)

    conn = get_db(str(ws / "graph.db"))
    from cairn.graph.dataflow import build_transitive_closure

    build_transitive_closure(conn)
    bundle = OKFBundle(str(ws / ".knowledge"))
    bundle.write_concept(
        OKFConcept(
            type="Compass",
            title="pkg_a",
            resource="pkg_a",
            concept_id="compass/pkg_a",
            tags=["pkg_a"],
            body="Hub: `zeta_hub` in `pkg_a/core.py`.",
        )
    )
    capture_memory(
        conn,
        bundle,
        type_="pattern",
        title="pkg_a zeta_hub hub contract",
        body="`zeta_hub` is the hub of pkg_a; check its callers before editing it.",
        resource="pkg_a/core.py",
    )
    capture_memory(
        conn,
        bundle,
        type_="decision",
        title="pkg_a alpha_leaf stability",
        body="`alpha_leaf` has no callers; treat it as a stable leaf.",
        resource="pkg_a/core.py",
    )
    conn.close()


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """cwd in tmp, CAIRN_DB pointing at the fixture graph db."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAIRN_DB", str(tmp_path / "graph.db"))
    return tmp_path


def _generate(*args):
    from cairn.cli.main import main

    return CliRunner().invoke(main, ["skill", "generate", *args])


def _tree_bytes(root: Path) -> dict[str, bytes]:
    """Every file under the generated tree, keyed by relative path."""
    return {
        p.relative_to(root).as_posix(): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def test_same_selector_twice_yields_byte_identical_skill(cli_env):
    _indexed_workspace(cli_env)
    out_one, out_two = cli_env / "run_one", cli_env / "run_two"
    assert _generate("pkg_a", "--output", str(out_one)).exit_code == 0
    assert _generate("pkg_a", "--output", str(out_two)).exit_code == 0
    assert _tree_bytes(out_one) == _tree_bytes(out_two)

    text = (out_one / "SKILL.md").read_text(encoding="utf-8")
    # The hub outranks the zero-impact symbols, which tie-break by name asc.
    assert (
        text.index("zeta_hub")
        < text.index("alpha_leaf")
        < text.index("mid_one")
        < text.index("mid_two")
    )
    # No wall-clock stamps in the body or frontmatter.
    assert not re.search(r"\d{4}-\d{2}-\d{2}", text)


def test_cross_process_reruns_under_different_hash_seeds_are_byte_identical(tmp_path):
    _indexed_workspace(tmp_path)
    trees = []
    for seed, dirname in (("17", "seed_a"), ("992", "seed_b")):
        out = tmp_path / dirname
        home = tmp_path / "_home"
        home.mkdir(exist_ok=True)
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from cairn.cli.main import main; "
                "main(['skill', 'generate', 'pkg_a', '--output', sys.argv[1]])",
                str(out),
            ],
            cwd=str(tmp_path),
            env={
                **os.environ,
                "CAIRN_HOME": str(home),
                "CAIRN_DB": str(tmp_path / "graph.db"),
                "PYTHONHASHSEED": seed,
            },
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert completed.returncode == 0, completed.stderr
        trees.append(_tree_bytes(out))
    assert trees[0] == trees[1]


def test_default_generation_constructs_no_llm_client(cli_env, monkeypatch):
    _indexed_workspace(cli_env)
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    def _boom(*args, **kwargs):
        raise AssertionError("default generation constructed an LLM client")

    import cairn.llm as llm_pkg
    from cairn.llm import client as llm_client

    monkeypatch.setattr(llm_client, "get_client", _boom)
    monkeypatch.setattr(llm_pkg, "get_client", _boom)
    monkeypatch.setattr(llm_client.FileQueueBackend, "__init__", _boom)
    monkeypatch.setattr(llm_client.SubprocessBackend, "__init__", _boom)

    out = cli_env / "no_llm_out"
    result = _generate("pkg_a", "--output", str(out))
    assert result.exit_code == 0, result.output
    assert (out / "SKILL.md").is_file()
