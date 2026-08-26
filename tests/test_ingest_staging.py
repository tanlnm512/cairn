"""Staged document ingestion tests.

Sections, in pipeline order: source adapters (fed markdown yield
contract), staging/outbox, end-to-end pipeline. Fed fixtures are written
under TemporaryDirectory.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from cairn.knowledge.ingest import run_ingest
from cairn.knowledge.ingest.adapters import (
    FED_ORIGIN,
    FED_REPO,
    FedMarkdownAdapter,
)
from cairn.knowledge.ingest.classifier import classify_doc
from cairn.knowledge.ingest.identity import build_identity
from cairn.knowledge.ingest.parser import parse_source_doc
from cairn.knowledge.ingest.staging import StagedEntry, stage_outbox
from cairn.okf.concept import OKFConcept


@pytest.fixture
def feed_root():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


def _write(root: Path, relpath: str, content: str) -> Path:
    target = root / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


# --- Fed markdown source adapter ---


class TestFedMarkdownAdapter:
    def test_single_file_yields_one_tuple_with_path_as_given(self, feed_root):
        content = "# Runbook\n\nRestart the service.\n"
        doc = _write(feed_root, "runbook.md", content)
        docs = list(FedMarkdownAdapter([doc]).iter_docs())
        assert docs == [(FED_REPO, doc.as_posix(), content, "fed")]
        assert docs[0][3] == FED_ORIGIN == "fed"

    def test_directory_walk_is_recursive_sorted_and_relative(self, feed_root):
        _write(feed_root, "b.md", "b")
        _write(feed_root, "a.md", "a")
        _write(feed_root, "sub/nested.md", "nested")
        _write(feed_root, "notes.txt", "not markdown")
        docs = list(FedMarkdownAdapter([feed_root]).iter_docs())
        assert [(repo, relpath) for repo, relpath, _t, _o in docs] == [
            (FED_REPO, "a.md"),
            (FED_REPO, "b.md"),
            (FED_REPO, "sub/nested.md"),
        ]

    def test_every_yield_is_a_four_tuple_of_str_in_given_order(self, feed_root):
        single = _write(feed_root, "single.md", "s")
        _write(feed_root, "dir/one.md", "1")
        _write(feed_root, "dir/two.md", "2")
        docs = list(FedMarkdownAdapter([single, feed_root / "dir"]).iter_docs())
        assert docs
        assert all(isinstance(doc, tuple) and len(doc) == 4 for doc in docs)
        assert all(isinstance(field, str) for doc in docs for field in doc)
        assert docs[0][1] == single.as_posix()

    def test_non_markdown_and_empty_inputs_are_not_yielded(self, feed_root):
        png = _write(feed_root, "diagram.png", "binary-ish")
        _write(feed_root, "dir/readme.txt", "text")
        _write(feed_root, "dir/real.md", "real")
        empty = feed_root / "empty"
        empty.mkdir()
        docs = list(FedMarkdownAdapter([png, feed_root / "dir", empty]).iter_docs())
        assert [doc[1] for doc in docs] == ["real.md"]

    def test_missing_path_raises_file_not_found(self, feed_root):
        missing = feed_root / "nope.md"
        with pytest.raises(FileNotFoundError, match="nope.md"):
            list(FedMarkdownAdapter([missing]).iter_docs())

    def test_unicode_content_round_trips(self, feed_root):
        content = "# Café — ünïcode ☕\n\nÉmile & Zoë — naïve résumé.\n"
        doc = _write(feed_root, "unicode.md", content)
        docs = list(FedMarkdownAdapter([doc]).iter_docs())
        assert docs[0][2] == content


# --- Staging: OKF outbox + manifest (T005) ---


def _entry(repo, relpath, text, origin="repo-scan", include_drafts=False):
    parsed = parse_source_doc(text)
    return StagedEntry(
        repo=repo,
        relpath=relpath,
        origin=origin,
        parsed=parsed,
        classification=classify_doc(parsed, relpath, include_drafts),
        identity=build_identity(repo, relpath, parsed),
    )


class TestStageOutbox:
    def test_staged_file_is_valid_okf_with_source_line(self, feed_root):
        entry = _entry("acme", "docs/adr/0007-use-events.md", ADR_TEXT)
        manifest = stage_outbox([entry], feed_root / "outbox")

        assert manifest["counts"] == {
            "accepted": 1,
            "skipped": 0,
            "by_type": {"decision": 1},
            "by_repo": {"acme": 1},
        }
        row = manifest["rows"][0]
        staged = feed_root / "outbox" / row["staged_path"]
        assert staged.exists()
        concept = OKFConcept.from_file(str(staged))
        assert concept.type == "Knowledge-decision"
        assert concept.title == row["title"]
        assert row["title"].startswith(entry.identity.stable_id)
        assert "Source: acme/docs/adr/0007-use-events.md" in concept.body
        assert "Source:" in row["body"]

    def test_row_carries_every_add_document_argument(self, feed_root):
        entry = _entry("acme", "docs/adr/0007-use-events.md", ADR_TEXT)
        manifest = stage_outbox([entry], feed_root / "outbox")

        row = manifest["rows"][0]
        assert set(row) == {
            "concept_id",
            "title",
            "doc_type",
            "tags",
            "description",
            "resource",
            "affects_repos",
            "affects_modules",
            "origin",
            "repo",
            "source_path",
            "body",
            "staged_path",
        }
        assert row["concept_id"] == f"knowledge/{row['doc_type']}/{entry.identity.slug}"
        assert row["resource"] == row["source_path"] == "acme/docs/adr/0007-use-events.md"
        assert row["affects_repos"] == ["acme"]
        assert row["affects_modules"] == ["docs/adr"]
        assert row["origin"] == "repo-scan"
        assert manifest["version"] == 1
        assert manifest["workspace"] == str(feed_root / "outbox")

    def test_skip_rows_carry_reason_and_stage_no_file(self, feed_root):
        entry = _entry("acme", "docs/drafts/wip.md", DRAFT_TEXT)
        manifest = stage_outbox([entry], feed_root / "outbox")

        assert manifest["rows"] == [
            {"source_path": "acme/docs/drafts/wip.md", "skip": "status: draft"}
        ]
        assert manifest["counts"]["accepted"] == 0
        assert manifest["counts"]["by_type"] == {}
        staged_files = list((feed_root / "outbox").rglob("*.md"))
        assert staged_files == []

    def test_rows_sorted_and_counts_split_by_type_and_repo(self, feed_root):
        entries = [
            _entry("zeta", "docs/guide.md", GUIDE_TEXT),
            _entry("acme", "docs/adr/0007-use-events.md", ADR_TEXT),
            _entry("acme", "docs/drafts/wip.md", DRAFT_TEXT),
        ]
        manifest = stage_outbox(entries, feed_root / "outbox")

        assert [row.get("source_path") for row in manifest["rows"]] == [
            "acme/docs/adr/0007-use-events.md",
            "acme/docs/drafts/wip.md",
            "zeta/docs/guide.md",
        ]
        assert manifest["counts"] == {
            "accepted": 2,
            "skipped": 1,
            "by_type": {"decision": 1, "workflow": 1},
            "by_repo": {"acme": 1, "zeta": 1},
        }

    def test_identity_and_classification_tags_merge_deduped(self, feed_root):
        entry = _entry("acme", "docs/wip-vision.md", DRAFT_VISION_TEXT, include_drafts=True)
        manifest = stage_outbox([entry], feed_root / "outbox")

        row = manifest["rows"][0]
        tags = row["tags"]
        assert tags.count("draft") == 1
        assert "reference" in tags
        assert entry.identity.stable_id in tags
        assert "acme" in tags

    def test_empty_body_stages_source_line_only(self, feed_root):
        entry = _entry("acme", "docs/empty.md", "---\ntitle: Bare\nstatus: accepted\n---\n")
        manifest = stage_outbox([entry], feed_root / "outbox")

        assert manifest["rows"][0]["body"] == "Source: acme/docs/empty.md\n"

    def test_returned_manifest_equals_written_file(self, feed_root):
        entry = _entry("acme", "docs/adr/0007-use-events.md", ADR_TEXT)
        outbox = feed_root / "outbox"
        manifest = stage_outbox([entry], outbox)

        on_disk = json.loads((outbox / "manifest.json").read_text(encoding="utf-8"))
        assert manifest == on_disk


ADR_TEXT = (
    "---\n"
    "title: Use events for telemetry\n"
    "status: accepted\n"
    "tags: [telemetry]\n"
    "---\n"
    "# ADR: Use events for telemetry\n\n"
    "We shall publish structured events.\n"
)

DRAFT_TEXT = (
    "---\n"
    "title: Work in progress\n"
    "status: draft\n"
    "---\n"
    "Half-written notes.\n"
)

DRAFT_VISION_TEXT = (
    "---\n"
    "title: Vision statement\n"
    "status: draft\n"
    "---\n"
    "Where we are going.\n"
)

GUIDE_TEXT = (
    "---\n"
    "title: Setup runbook\n"
    "status: accepted\n"
    "---\n"
    "Install the thing.\n"
)


# --- Stage-only pipeline (T006) ---


class TestRunIngest:
    def test_mixed_fed_corpus_stages_with_counts_and_skips(self, feed_root):
        _write(feed_root, "adr-frontmatter.md", ADR_TEXT)
        _write(feed_root, "adr-inline.md", INLINE_ADR_TEXT)
        _write(feed_root, "draft.md", DRAFT_TEXT)
        _write(feed_root, "plain.md", PLAIN_TEXT)

        manifest = run_ingest(
            files=[], dirs=[feed_root], outbox=feed_root / "outbox"
        )

        assert manifest["counts"]["accepted"] == 3
        assert manifest["counts"]["skipped"] == 1
        skips = [row for row in manifest["rows"] if "skip" in row]
        assert skips == [
            {"source_path": "workspace/draft.md", "skip": "status: draft"}
        ]
        for row in manifest["rows"]:
            if "skip" not in row:
                assert (feed_root / "outbox" / row["staged_path"]).exists()

    def test_plain_fed_file_is_spec_with_fed_tag(self, feed_root):
        doc = _write(feed_root, "plain.md", PLAIN_TEXT)

        manifest = run_ingest(files=[doc], dirs=[], outbox=feed_root / "outbox")

        row = manifest["rows"][0]
        assert row["doc_type"] == "spec"
        assert "fed" in row["tags"]
        assert (feed_root / "outbox" / row["staged_path"]).exists()

    def test_same_content_same_shape_classifies_identically(self, feed_root):
        one = _write(feed_root, "one-adr.md", ADR_TEXT)
        two = _write(feed_root, "two-adr.md", ADR_TEXT)

        manifest = run_ingest(
            files=[one, two], dirs=[], outbox=feed_root / "outbox"
        )

        first, second = manifest["rows"]
        assert first["doc_type"] == second["doc_type"] == "decision"
        assert first["body"].split("\n\nSource:")[0] == second["body"].split(
            "\n\nSource:"
        )[0]
        assert first["concept_id"] != second["concept_id"]

    def test_default_outbox_under_workspace_root(self, feed_root, monkeypatch):
        doc = _write(feed_root, "plain.md", PLAIN_TEXT)
        monkeypatch.chdir(feed_root)

        manifest = run_ingest(files=[doc], dirs=[])

        row = manifest["rows"][0]
        assert (feed_root / ".cairn" / "ingest-outbox" / row["staged_path"]).exists()
        assert (feed_root / ".cairn" / "ingest-outbox" / "manifest.json").exists()

    def test_staging_never_touches_the_store(self, feed_root, monkeypatch):
        doc = _write(feed_root, "plain.md", PLAIN_TEXT)
        monkeypatch.chdir(feed_root)

        run_ingest(files=[doc], dirs=[])

        assert list(feed_root.rglob("*.sqlite*")) == []
        assert [p.name for p in feed_root.rglob(".knowledge")] == []


INLINE_ADR_TEXT = (
    "# ADR: Use events for telemetry\n\n"
    "**Status:** accepted\n\n"
    "We shall publish structured events.\n"
)

PLAIN_TEXT = "Just some operational notes with no metadata at all.\n"


# --- CLI registration (T007) ---


class TestKnowledgeIngestCli:
    def test_help_lists_ingest(self):
        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        result = CliRunner().invoke(knowledge, ["--help"])
        assert result.exit_code == 0
        assert "ingest" in result.output

    def test_run_stages_and_leaves_store_untouched(self, feed_root, monkeypatch):
        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        doc = _write(feed_root, "plain.md", PLAIN_TEXT)
        monkeypatch.chdir(feed_root)

        result = CliRunner().invoke(
            knowledge,
            ["ingest", "--file", str(doc), "--outbox", str(feed_root / "outbox")],
        )

        assert result.exit_code == 0
        assert "Staged 1 document(s), skipped 0." in result.output
        assert (feed_root / "outbox" / "manifest.json").exists()
        assert list(feed_root.rglob("*.sqlite*")) == []

    def test_no_inputs_is_an_error(self, feed_root):
        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        result = CliRunner().invoke(knowledge, ["ingest"])
        assert result.exit_code != 0
