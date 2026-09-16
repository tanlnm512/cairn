"""Skillgen pre-write critic gate: ``verify_draft`` + ``GateResult``.

- gates the exact ``render_skill`` bytes (frontmatter + body) through the
  compass critic's ``validate_paths`` (consume-only) before any write
- every backtick-quoted file/symbol reference must resolve against the
  graph; unresolved refs reject with the failing refs listed
- a draft with no backtick refs passes vacuously; the emitter's
  backtick-free reference pointers never trip the gate
"""
from __future__ import annotations

import pytest

from cairn.skillgen.emitter import render_skill, split_references
from cairn.skillgen.gate import GateResult, verify_draft

TRIGGER = (
    "Module context for pkg_a. Load when asked about pkg_a structure, call "
    "graphs, or blast radius before editing its files."
)


@pytest.fixture
def indexed_db(fresh_db):
    """A real indexed workspace: one repo, one file, one symbol."""
    cur = fresh_db.cursor()
    cur.execute(
        "INSERT INTO repos (id, name, path) VALUES (?, ?, ?)",
        ("repo1", "test-repo", "/tmp/test"),
    )
    cur.execute(
        "INSERT INTO files (id, repo_id, path, language) VALUES (?, ?, ?, ?)",
        ("f1", "repo1", "src/ApiClient.ts", "typescript"),
    )
    cur.execute(
        "INSERT INTO symbols (id, file_id, name, qualified_name, kind) VALUES (?, ?, ?, ?, ?)",
        ("s1", "f1", "safeApiCall", "ApiClient.safeApiCall", "function"),
    )
    fresh_db.commit()
    return fresh_db


def test_gate_result_outcomes():
    ok = GateResult.accepted()
    rejected = GateResult.rejected(["src/ghost.py"])
    assert ok.ok and bool(ok)
    assert not rejected.ok and not rejected
    assert rejected.failing_refs == ("src/ghost.py",)


def test_rendered_bytes_for_indexed_workspace_pass(indexed_db):
    rendered = render_skill(
        "pkg_a",
        TRIGGER,
        [("Usage", "Call `safeApiCall()`; see `src/ApiClient.ts`.")],
    )
    result = verify_draft(indexed_db, rendered)
    assert result.ok
    assert result.failing_refs == ()


def test_no_backtick_refs_passes_vacuously(indexed_db):
    rendered = render_skill("pkg_a", TRIGGER, [("Overview", "plain prose body")])
    assert verify_draft(indexed_db, rendered).ok


def test_non_ref_backtick_text_ignored(indexed_db):
    rendered = render_skill(
        "pkg_a",
        TRIGGER,
        [("Usage", "Run `cairn embed` and see `some phrase here`.")],
    )
    assert verify_draft(indexed_db, rendered).ok


def test_ghost_symbol_rejects_with_ref_listed(indexed_db):
    rendered = render_skill(
        "pkg_a", TRIGGER, [("Usage", "Use `ghost_symbol_qq` here.")]
    )
    result = verify_draft(indexed_db, rendered)
    assert not result
    assert result.failing_refs == ("ghost_symbol_qq",)


def test_ghost_path_rejects_with_ref_listed(indexed_db):
    rendered = render_skill(
        "pkg_a", TRIGGER, [("Usage", "Read `src/ghost_qq.py` first.")]
    )
    result = verify_draft(indexed_db, rendered)
    assert not result
    assert result.failing_refs == ("src/ghost_qq.py",)


def test_extension_only_path_ref_rejects(indexed_db):
    rendered = render_skill(
        "pkg_a", TRIGGER, [("Usage", "Check `ghost_qq.py` for details.")]
    )
    result = verify_draft(indexed_db, rendered)
    assert not result
    assert result.failing_refs == ("ghost_qq.py",)


def test_mixed_real_and_ghost_rejects_only_the_ghosts(indexed_db):
    rendered = render_skill(
        "pkg_a",
        TRIGGER,
        [
            (
                "Usage",
                "Call `safeApiCall()` or `ghost_symbol_qq`; see "
                "`src/ApiClient.ts` and `src/ghost_qq.py`.",
            )
        ],
    )
    result = verify_draft(indexed_db, rendered)
    assert not result
    assert set(result.failing_refs) == {"ghost_symbol_qq", "src/ghost_qq.py"}


def test_all_ghost_rejects_with_every_ref_listed(indexed_db):
    rendered = render_skill(
        "pkg_a", TRIGGER, [("Usage", "Try `ghost_qq` then `ghost_zz`.")]
    )
    result = verify_draft(indexed_db, rendered)
    assert not result
    assert set(result.failing_refs) == {"ghost_qq", "ghost_zz"}


def test_repeated_ghost_ref_reported_once(indexed_db):
    rendered = render_skill(
        "pkg_a",
        TRIGGER,
        [("Usage", "`ghost_symbol_qq` again `ghost_symbol_qq`.")],
    )
    result = verify_draft(indexed_db, rendered)
    assert result.failing_refs == ("ghost_symbol_qq",)


def test_backtick_free_reference_pointer_passes(indexed_db):
    inline, _refs = split_references(
        [("Short note", "short"), ("Deep detail", "word " * 500)], max_chars=200
    )
    rendered = render_skill("pkg_a", TRIGGER, inline)
    pointer = inline[1][1]
    assert "references/deep-detail.md" in pointer
    assert "`" not in pointer
    assert verify_draft(indexed_db, rendered).ok


def test_gate_rejects_on_exact_bytes_including_frontmatter(indexed_db):
    rendered = render_skill(
        "pkg_a", "Load for pkg_a. See `ghost_symbol_qq`.", [("Overview", "text")]
    )
    result = verify_draft(indexed_db, rendered)
    assert not result
    assert result.failing_refs == ("ghost_symbol_qq",)
