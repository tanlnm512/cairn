"""Repo doc-tree scan: allowlist walk, skip-list with reasons (FR-001)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cairn.knowledge.ingest import run_ingest
from cairn.knowledge.ingest.adapters import (
    INGEST_MAX_FILE_SIZE,
    RepoScanAdapter,
)


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

    def test_multi_segment_dir_rule_matches(self, repo_root):
        """A workspace dir pattern like "docs/notes/" catches docs/notes/x.md."""
        _write(repo_root, "docs/notes/secret.md", "# Secret notes")
        _write(repo_root, "docs/adr/0001-public.md", ADR)

        adapter = RepoScanAdapter(repo_root, skip_add=("docs/notes/",))
        docs = list(adapter.iter_docs())

        assert [relpath for _r, relpath, _t, _o in docs] == [
            "docs/adr/0001-public.md"
        ]
        assert adapter.skipped == [
            ("docs/notes/secret.md", "skip-list: docs/notes/ (workspace)")
        ]

    def test_single_segment_dir_rule_matching_is_unchanged(self, repo_root):
        """A one-segment pattern still matches per directory segment."""
        _write(repo_root, "docs/internal/wip.md", "Internal notes.")
        _write(repo_root, "docs/adr/0001-public.md", ADR)

        adapter = RepoScanAdapter(repo_root, skip_add=("internal/",))
        docs = list(adapter.iter_docs())

        assert [relpath for _r, relpath, _t, _o in docs] == [
            "docs/adr/0001-public.md"
        ]
        assert adapter.skipped == [
            ("docs/internal/wip.md", "skip-list: internal/ (workspace)")
        ]

    def test_oversize_file_gets_skip_row(self, repo_root):
        """Files over INGEST_MAX_FILE_SIZE skip with a reason, not a read."""
        _write(repo_root, "docs/runbook.md", RUNBOOK)
        huge = repo_root / "docs" / "huge.md"
        huge.write_text("x" * (INGEST_MAX_FILE_SIZE + 1), encoding="utf-8")

        adapter = RepoScanAdapter(repo_root)
        docs = list(adapter.iter_docs())

        assert [relpath for _r, relpath, _t, _o in docs] == ["docs/runbook.md"]
        assert adapter.skipped == [
            (
                "docs/huge.md",
                "file too large "
                f"({INGEST_MAX_FILE_SIZE + 1} > {INGEST_MAX_FILE_SIZE} bytes)",
            )
        ]


class TestFedAccounting:
    """Fed markdown feeds: every doc yields or gets a manifest skip row.

    Fed files keep their path as given, so these tests chdir into the
    feed dir and feed bare file names.
    """

    def test_fed_non_markdown_file_gets_skip_row(self, tmp_path, monkeypatch):
        _write(tmp_path, "runbook.md", RUNBOOK)
        _write(tmp_path, "notes.txt", "not markdown")
        monkeypatch.chdir(tmp_path)

        manifest = run_ingest(
            files=["runbook.md", "notes.txt"], dirs=[], outbox=tmp_path / "outbox"
        )

        rows = {row.get("source_path"): row for row in manifest["rows"]}
        assert "skip" not in rows["workspace/runbook.md"]
        assert rows["workspace/notes.txt"]["skip"] == "unsupported type: .txt"
        assert manifest["counts"] == {
            "accepted": 1,
            "skipped": 1,
            "by_type": {"workflow": 1},
            "by_repo": {"workspace": 1},
        }

    def test_fed_uppercase_md_suffix_is_accepted(self, tmp_path, monkeypatch):
        _write(tmp_path, "README.MD", RUNBOOK)
        monkeypatch.chdir(tmp_path)

        manifest = run_ingest(
            files=["README.MD"], dirs=[], outbox=tmp_path / "outbox"
        )

        assert manifest["counts"]["accepted"] == 1
        assert manifest["counts"]["skipped"] == 0
        assert manifest["rows"][0]["source_path"] == "workspace/README.MD"

    def test_fed_directory_walk_picks_up_uppercase_md(self, tmp_path):
        feed = tmp_path / "feed"
        _write(feed, "README.MD", RUNBOOK)
        _write(feed, "guide.md", RUNBOOK)

        manifest = run_ingest(files=[], dirs=[feed], outbox=tmp_path / "outbox")

        paths = sorted(row["source_path"] for row in manifest["rows"])
        assert paths == ["workspace/README.MD", "workspace/guide.md"]

    def test_fed_oversize_file_gets_skip_row(self, tmp_path, monkeypatch):
        _write(tmp_path, "runbook.md", RUNBOOK)
        (tmp_path / "huge.md").write_text(
            "x" * (INGEST_MAX_FILE_SIZE + 1), encoding="utf-8"
        )
        monkeypatch.chdir(tmp_path)

        manifest = run_ingest(
            files=["runbook.md", "huge.md"], dirs=[], outbox=tmp_path / "outbox"
        )

        rows = {row.get("source_path"): row for row in manifest["rows"]}
        assert rows["workspace/huge.md"]["skip"] == (
            f"file too large ({INGEST_MAX_FILE_SIZE + 1} > "
            f"{INGEST_MAX_FILE_SIZE} bytes)"
        )
        assert "skip" not in rows["workspace/runbook.md"]
        assert manifest["counts"]["accepted"] == 1
        assert manifest["counts"]["skipped"] == 1


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
