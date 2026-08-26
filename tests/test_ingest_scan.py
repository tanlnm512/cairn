"""Repo doc-tree scan: allowlist walk, skip-list with reasons (FR-001)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cairn.knowledge.ingest import run_ingest
from cairn.knowledge.ingest.adapters import RepoScanAdapter


@pytest.fixture
def repo_root():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "acme"
        root.mkdir()
        yield root


def _write(root: Path, relpath: str, content: str) -> Path:
    target = root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


ADR = (
    "---\ntitle: Use events\nstatus: accepted\n---\n"
    "# ADR: Use events\n\nPublish structured events.\n"
)
RUNBOOK = "---\ntitle: Setup runbook\nstatus: accepted\n---\nInstall it.\n"


def _fixture_corpus(root: Path) -> None:
    _write(root, "docs/adr/0001-use-events.md", ADR)
    _write(root, "docs/runbook.md", RUNBOOK)
    _write(root, "docs/drafts/wip.md", "---\nstatus: draft\n---\nHalf-written.")
    _write(root, "docs/meetings/2026-08-26.md", "Meeting notes.")
    _write(root, "docs/meeting-notes-2026.md", "Meeting notes file.")
    _write(root, "docs/api_generated/reference.md", "Generated mirror.")
    _write(root, "docs/changelogs/2026.md", "Changelog entry.")
    _write(root, "docs/CHANGELOG.md", "Top-level changelog.")
    _write(root, "docs/templates/adr-template.md", "# ADR template")
    _write(root, "docs/issue-template.md", "Issue template")


class TestRepoScanAdapter:
    def test_yield_contract_repo_origin_and_posix_relpath(self, repo_root):
        _fixture_corpus(repo_root)
        docs = list(RepoScanAdapter(repo_root).iter_docs())
        assert docs
        for repo, relpath, text, origin in docs:
            assert repo == origin == "acme"
            assert isinstance(relpath, str) and not relpath.startswith("/")
        kinds = [relpath for _r, relpath, _t, _o in docs]
        assert "docs/adr/0001-use-events.md" in kinds
        assert "docs/runbook.md" in kinds
        assert not any("drafts" in k or "meetings" in k for k in kinds)

    def test_every_skip_carries_a_reason(self, repo_root):
        _fixture_corpus(repo_root)
        adapter = RepoScanAdapter(repo_root)
        list(adapter.iter_docs())
        skips = dict(adapter.skipped)
        assert skips["docs/drafts/wip.md"] == "skip-list: drafts directory"
        assert skips["docs/meetings/2026-08-26.md"] == "skip-list: meetings directory"
        assert skips["docs/meeting-notes-2026.md"] == "skip-list: meeting-notes file"
        assert skips["docs/api_generated/reference.md"] == "skip-list: generated mirror directory"
        assert skips["docs/changelogs/2026.md"] == "skip-list: changelogs directory"
        assert skips["docs/CHANGELOG.md"] == "skip-list: changelog file"
        assert skips["docs/templates/adr-template.md"] == "skip-list: templates directory"
        assert skips["docs/issue-template.md"] == "skip-list: template file"

    def test_missing_repo_root_raises(self, repo_root):
        with pytest.raises(FileNotFoundError):
            list(RepoScanAdapter(repo_root / "nope").iter_docs())

    def test_repo_without_doc_dirs_yields_nothing(self, repo_root):
        _write(repo_root, "README.md", "not in a doc dir")
        adapter = RepoScanAdapter(repo_root)
        assert list(adapter.iter_docs()) == []
        assert adapter.skipped == []


class TestScanPipeline:
    def test_manifest_accounts_for_every_doc(self, repo_root, tmp_path):
        _fixture_corpus(repo_root)
        manifest = run_ingest(
            files=[], dirs=[], repos=[repo_root], outbox=tmp_path / "outbox"
        )

        accepted = [r for r in manifest["rows"] if "skip" not in r]
        skipped = [r for r in manifest["rows"] if "skip" in r]
        all_md = [p for p in repo_root.rglob("*.md")]
        assert len(accepted) + len(skipped) == len(all_md)

        by_path = {r["source_path"]: r for r in accepted}
        assert by_path["acme/docs/adr/0001-use-events.md"]["doc_type"] == "decision"
        assert by_path["acme/docs/runbook.md"]["doc_type"] == "workflow"
        skip_reasons = {r["source_path"]: r["skip"] for r in skipped}
        assert skip_reasons["acme/docs/drafts/wip.md"] == "skip-list: drafts directory"
        assert (
            skip_reasons["acme/docs/api_generated/reference.md"]
            == "skip-list: generated mirror directory"
        )

    def test_cli_repo_scan_invocation(self, repo_root, tmp_path, monkeypatch):
        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        _fixture_corpus(repo_root)
        monkeypatch.chdir(tmp_path)

        result = CliRunner().invoke(
            knowledge,
            ["ingest", "--repo", str(repo_root), "--outbox", str(tmp_path / "outbox")],
        )
        assert result.exit_code == 0, result.output
        assert "Staged 2 document(s), skipped 8." in result.output
