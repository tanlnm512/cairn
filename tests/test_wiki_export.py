"""Pins for `cairn wiki export --dir DIR [--force]` (FR-006).

Export contract pinned here (D-019, TC-016..TC-018) against the PLANNED
subcommand of the wiki group:

* one ``DIR/{repo}/{page_id}.md`` per PROMOTED page; page ids collide
  across repos, so the repo subdirectory keeps the manifest key 1:1.
* iteration comes from the manifest rows keyed ``{repo}/{page_id}`` --
  promoted is derived per row by reading the
  ``wiki/pages/{repo}/{page_id}`` concept, never from the stored row
  state, and no rglob of the bundle leaks non-concept ``.md`` files in.
* frontmatter is preserved through the round-trip: the exported file
  parses back to the page's title/body, ``sources`` appears only for
  pages that have sources, and extensions ride along.
* success prints ``Exported N page(s) to DIR``.
* a non-empty target directory is refused (exit 1, refusal on stderr)
  without ``--force``; with it the export proceeds and overwrites.
* zero promoted pages is a valid count-0 success that writes nothing.

Manifest fixtures are written directly as JSON at
`<knowledge>/_wiki/manifest.json` (schema "cairn-wiki-manifest-2", rows
keyed "{repo}/{page_id}") and promoted articles via ``OKFBundle
.write_concept``, so the tests do not depend on `cairn.wiki.manifest`.
Only the specific CLI module is imported, never the `cairn.cli` package
root (C-04); the hermetic ``cli_env`` pattern is tests/test_wiki_cli.py's.
"""
from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from cairn.cli.wiki import wiki
from cairn.llm.tasks import create_task
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept

MANIFEST_SCHEMA = "cairn-wiki-manifest-2"
REPO = "r"

PROMOTED_SOURCED = "mod-okf"
PROMOTED_PLAIN = "mod-graph"
QUEUED = "overview"
SOURCE_PATH = "src/some-module/core.py"


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Hermetic store: cwd in tmp, CAIRN_DB/CAIRN_KNOWLEDGE under tmp_path."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CAIRN_DB", str(tmp_path / "graph.db"))
    monkeypatch.setenv("CAIRN_KNOWLEDGE", str(tmp_path / "knowledge"))
    knowledge = tmp_path / "knowledge"
    (knowledge / "_tasks").mkdir(parents=True)
    return knowledge


def _bundle(knowledge):
    return OKFBundle(str(knowledge))


def _key(page_id, repo=REPO):
    return f"{repo}/{page_id}"


def _row(page_id, *, state, task_id="", attempts=0):
    """One manifest row: the plan entry plus the D-006 tracking fields."""
    return {
        "page_id": page_id,
        "title": "Wiki page",
        "description": "Describes the module.",
        "module": "some-module",
        "seeds": ["src/some-module/core.py"],
        "input_hash": "input-hash-value",
        "task_id": task_id,
        "state": state,
        "attempts": attempts,
    }


def _write_manifest(knowledge, pages):
    manifest_dir = knowledge / "_wiki"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    doc = {"schema": MANIFEST_SCHEMA, "pages": pages}
    (manifest_dir / "manifest.json").write_text(
        json.dumps(doc, indent=2) + "\n", encoding="utf-8"
    )


def _promote(bundle, page_id, *, repo=REPO, sources=None, extensions=None):
    """Write the promoted article so the concept resolves."""
    bundle.write_concept(
        OKFConcept(
            type="Wiki-Article",
            title=f"Wiki page {page_id}",
            body=f"# {page_id}\n\nBody of {page_id}.\n\n## Sources\n",
            concept_id=f"wiki/pages/{repo}/{page_id}",
            tags=[repo, "wiki"],
            sources=sources,
            extensions=extensions or {},
        )
    )


def _export(knowledge, out_dir, *extra):
    return CliRunner().invoke(
        wiki, ["export", "--dir", str(out_dir), "--knowledge", str(knowledge),
               *extra]
    )


# --- TC-016: one frontmatter file per promoted page, count reported ----------


def test_export_writes_one_frontmatter_file_per_promoted_page(cli_env, tmp_path):
    """TC-016: every promoted page is written as DIR/{repo}/{page_id}.md with
    its frontmatter (title always; sources only when the page has them) and
    the command reports the promoted count; non-promoted pages are not
    written."""
    knowledge = cli_env
    bundle = _bundle(knowledge)
    queued = create_task(bundle, "wiki-page", QUEUED)
    _promote(bundle, PROMOTED_SOURCED,
             sources=[{"path": SOURCE_PATH}, {"symbol": "core"}],
             extensions={"commit_sha": "abc1234a"})
    _promote(bundle, PROMOTED_PLAIN)
    _write_manifest(knowledge, {
        _key(PROMOTED_SOURCED): _row(PROMOTED_SOURCED, state="promoted",
                                     task_id="spent-chain", attempts=1),
        _key(PROMOTED_PLAIN): _row(PROMOTED_PLAIN, state="promoted",
                                   task_id="spent-chain", attempts=1),
        _key(QUEUED): _row(QUEUED, state="queued", task_id=queued.id, attempts=1),
    })
    out = tmp_path / "out"

    result = _export(knowledge, out)

    assert result.exit_code == 0, result.output
    assert f"Exported 2 page(s) to {out}" in result.stdout
    assert not (out / REPO / f"{QUEUED}.md").exists()

    sourced_text = (out / REPO / f"{PROMOTED_SOURCED}.md").read_text("utf-8")
    assert sourced_text.startswith("---")
    assert f"title: Wiki page {PROMOTED_SOURCED}" in sourced_text
    assert "sources:" in sourced_text
    assert SOURCE_PATH in sourced_text
    parsed = OKFConcept.from_file(str(out / REPO / f"{PROMOTED_SOURCED}.md"))
    assert parsed.title == f"Wiki page {PROMOTED_SOURCED}"
    assert f"Body of {PROMOTED_SOURCED}." in parsed.body
    assert parsed.sources == [{"path": SOURCE_PATH}, {"symbol": "core"}]
    assert parsed.extensions["commit_sha"] == "abc1234a"

    plain_text = (out / REPO / f"{PROMOTED_PLAIN}.md").read_text("utf-8")
    assert plain_text.startswith("---")
    assert f"title: Wiki page {PROMOTED_PLAIN}" in plain_text
    assert "sources:" not in plain_text
    parsed = OKFConcept.from_file(str(out / REPO / f"{PROMOTED_PLAIN}.md"))
    assert parsed.sources is None
    assert f"Body of {PROMOTED_PLAIN}." in parsed.body


def test_export_separates_colliding_page_ids_across_repos(cli_env, tmp_path):
    """Page ids collide across repos (every repo plans an overview page), so
    each repo exports to its own subdirectory: DIR/{repo}/{page_id}.md."""
    knowledge = cli_env
    bundle = _bundle(knowledge)
    for repo, marker in (("alpha", "alpha body"), ("beta", "beta body")):
        bundle.write_concept(
            OKFConcept(
                type="Wiki-Article",
                title=f"Wiki page overview ({repo})",
                body=f"# overview\n\n{marker}\n\n## Sources\n",
                concept_id=f"wiki/pages/{repo}/overview",
                tags=[repo, "wiki"],
            )
        )
    _write_manifest(knowledge, {
        _key("overview", "alpha"): _row("overview", state="promoted",
                                        task_id="chain-a", attempts=1),
        _key("overview", "beta"): _row("overview", state="promoted",
                                       task_id="chain-b", attempts=1),
    })
    out = tmp_path / "out"

    result = _export(knowledge, out)

    assert result.exit_code == 0, result.output
    assert f"Exported 2 page(s) to {out}" in result.stdout
    alpha = (out / "alpha" / "overview.md").read_text("utf-8")
    beta = (out / "beta" / "overview.md").read_text("utf-8")
    assert "alpha body" in alpha
    assert "beta body" in beta
    assert alpha != beta


def test_export_iterates_manifest_rows_not_the_bundle_tree(cli_env, tmp_path):
    """Iteration is the manifest, not an rglob: a readable page concept with
    no manifest row is invisible to export, a promoted-state row whose
    concept is missing is not exported, and non-concept .md files in the
    bundle never leak out as exported pages."""
    knowledge = cli_env
    bundle = _bundle(knowledge)
    _promote(bundle, "real")
    _promote(bundle, "ghost")  # readable concept, but no manifest row
    (knowledge / "_wiki").mkdir(parents=True, exist_ok=True)
    (knowledge / "_wiki" / "index.md").write_text(
        "# not a concept\n", encoding="utf-8")
    _write_manifest(knowledge, {
        _key("real"): _row("real", state="promoted",
                           task_id="spent-chain", attempts=1),
        _key("missing"): _row("missing", state="promoted",
                              task_id="lost-chain", attempts=1),
    })
    out = tmp_path / "out"

    result = _export(knowledge, out)

    assert result.exit_code == 0, result.output
    assert f"Exported 1 page(s) to {out}" in result.stdout
    exported = sorted(
        p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()
    )
    assert exported == [f"{REPO}/real.md"]


# --- TC-017: non-empty target dir refuses without --force, proceeds with it --


def _seed_prior_export(knowledge, out):
    """A non-empty target dir holding a stale prior export of one page."""
    stale = out / REPO / f"{PROMOTED_SOURCED}.md"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text(
        "---\ntitle: stale\n---\n\nStale export body.\n", encoding="utf-8")
    return stale


def test_export_refuses_nonempty_dir_without_force_and_changes_nothing(
    cli_env, tmp_path
):
    """TC-017: exporting into a directory that already contains files refuses
    with exit 1 and a refusal on stderr, and leaves every pre-existing byte
    untouched."""
    knowledge = cli_env
    bundle = _bundle(knowledge)
    _promote(bundle, PROMOTED_SOURCED)
    _write_manifest(knowledge, {
        _key(PROMOTED_SOURCED): _row(PROMOTED_SOURCED, state="promoted",
                                     task_id="spent-chain", attempts=1),
    })
    out = tmp_path / "out"
    stale = _seed_prior_export(knowledge, out)
    before = stale.read_bytes()

    result = _export(knowledge, out)

    assert result.exit_code == 1, result.output
    assert result.stderr.strip()
    assert stale.read_bytes() == before
    assert sorted(
        p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()
    ) == [f"{REPO}/{PROMOTED_SOURCED}.md"]


def test_export_force_overwrites_into_nonempty_dir(cli_env, tmp_path):
    """TC-017: with --force the export proceeds into the non-empty directory
    and overwrites the stale files with the current pages."""
    knowledge = cli_env
    bundle = _bundle(knowledge)
    _promote(bundle, PROMOTED_SOURCED)
    _write_manifest(knowledge, {
        _key(PROMOTED_SOURCED): _row(PROMOTED_SOURCED, state="promoted",
                                     task_id="spent-chain", attempts=1),
    })
    out = tmp_path / "out"
    stale = _seed_prior_export(knowledge, out)

    result = _export(knowledge, out, "--force")

    assert result.exit_code == 0, result.output
    assert f"Exported 1 page(s) to {out}" in result.stdout
    exported = stale.read_text("utf-8")
    assert "Stale export body." not in exported
    assert f"Body of {PROMOTED_SOURCED}." in exported


# --- TC-018: zero promoted pages is a count-0 success ------------------------


def test_export_with_zero_promoted_pages_reports_zero_and_writes_nothing(
    cli_env, tmp_path
):
    """TC-018: queued/failed pages only -- the command reports 0 exported and
    the target directory stays empty."""
    knowledge = cli_env
    _write_manifest(knowledge, {
        _key(QUEUED): _row(QUEUED, state="queued", task_id="live", attempts=1),
        _key("mod-dashboard"): _row("mod-dashboard", state="failed",
                                    task_id="dead", attempts=2),
    })
    out = tmp_path / "out"
    out.mkdir()

    result = _export(knowledge, out)

    assert result.exit_code == 0, result.output
    assert "Exported 0 page(s)" in result.stdout
    assert list(out.rglob("*")) == []
