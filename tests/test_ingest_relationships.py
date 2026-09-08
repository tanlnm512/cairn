"""Author-declared relationship survival through ingest (architecture D1.1).

Covers the validation-contract behaviors: VAL-INGEST-001 (a declared
``relates_to`` reaches the promoted ``.knowledge`` frontmatter verbatim) and
VAL-INGEST-002 (the dry-run manifest and console output surface
relationships before any store write), plus the parser/staging/store
plumbing between them.
"""
from __future__ import annotations

import pytest

from cairn.knowledge.ingest import run_ingest
from cairn.knowledge.ingest.classifier import classify_doc
from cairn.knowledge.ingest.executor import execute_manifest
from cairn.knowledge.ingest.identity import build_identity
from cairn.knowledge.ingest.parser import parse_source_doc
from cairn.knowledge.ingest.staging import StagedEntry, stage_outbox
from cairn.knowledge.relationships import (
    extract_relationships,
    normalize_relationships,
)
from cairn.knowledge.store import add_document, list_documents
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept
from cairn.paths import resolve_store

LINKED_ADR = (
    "---\n"
    "title: Use events for telemetry\n"
    "status: accepted\n"
    "relates_to:\n"
    "  - concept_id: knowledge/decision/0006-raw-logs\n"
    "    relation: relates-to\n"
    "    kind: extracted\n"
    "---\n"
    "# ADR: Use events for telemetry\n\n"
    "Publish structured events.\n"
)

SUPERSEDE_ADR = (
    "---\n"
    "title: Adopt event mesh\n"
    "status: accepted\n"
    "supersedes: [knowledge/decision/0001-raw-logs]\n"
    "superseded-by: knowledge/decision/0003-mesh-v2\n"
    "---\n"
    "We adopt the event mesh.\n"
)

KINDLESS_LINK = (
    "---\n"
    "title: Kindless link\n"
    "status: accepted\n"
    "relates_to:\n"
    "  - concept_id: knowledge/spec/other\n"
    "    relation: references\n"
    "---\n"
    "Body.\n"
)

PLAIN_DOC = (
    "---\n"
    "title: Plain doc\n"
    "status: accepted\n"
    "tags: [ops]\n"
    "description: No relationships declared.\n"
    "---\n"
    "Ordinary operational notes.\n"
)

MALFORMED_FENCED = (
    "---\n"
    "title: Malformed recovery\n"
    "broken yaml [unclosed\n"
    "relates_to: [{concept_id: knowledge/spec/x, relation: relates-to}]\n"
    "---\n"
    "Recovered body.\n"
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Hermetic store: every write lands under tmp_path (see
    test_ingest_execute.py for the rationale behind each env patch)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CAIRN_WORKSPACE", raising=False)
    monkeypatch.delenv("CAIRN_STORE_KEY", raising=False)
    home = tmp_path / "cairn-home"
    monkeypatch.setenv("CAIRN_HOME", str(home))
    monkeypatch.setenv("CAIRN_DB", str(home / ".kg"))
    monkeypatch.setenv("CAIRN_KNOWLEDGE", str(home / ".knowledge"))

    import cairn.paths as paths_mod

    monkeypatch.setattr(paths_mod, "CAIRN_HOME", home)
    monkeypatch.setattr(paths_mod, "REGISTRY_FILE", home / "workspaces.json")
    yield tmp_path


def _bundle() -> OKFBundle:
    return OKFBundle(str(resolve_store().knowledge))


def _entry(repo, relpath, text):
    parsed = parse_source_doc(text)
    return StagedEntry(
        repo=repo,
        relpath=relpath,
        origin="repo-scan",
        parsed=parsed,
        classification=classify_doc(parsed, relpath, False),
        identity=build_identity(repo, relpath, parsed),
    )


def _stage_one(tmp_path, text, relpath="docs/adr/doc.md"):
    return stage_outbox([_entry("acme", relpath, text)], tmp_path / "outbox")


def _ingest(workspace, text, name="adr.md"):
    docs = workspace / "docs"
    docs.mkdir(exist_ok=True)
    (docs / name).write_text(text, encoding="utf-8")
    manifest = run_ingest(files=[], dirs=[docs], outbox=workspace / "outbox")
    from cairn.cli.main import get_db

    resolve_store().ensure()
    conn = get_db()
    try:
        execute_manifest(manifest, conn)
    finally:
        conn.close()


# --- parser: unknown frontmatter is kept, not discarded ---


class TestParserKeepsUnknownFrontmatter:
    def test_unknown_keys_land_in_extensions_verbatim(self):
        text = (
            "---\n"
            "title: T\n"
            "status: accepted\n"
            "owner_team: [core, cli]\n"
            "custom_field: hello\n"
            "---\n"
            "Body.\n"
        )
        doc = parse_source_doc(text)
        assert doc.extensions["custom_field"] == "hello"
        assert doc.extensions["owner_team"] == ["core", "cli"]

    def test_source_keys_do_not_leak_into_extensions(self):
        doc = parse_source_doc(PLAIN_DOC)
        assert doc.extensions == {}

    def test_malformed_yaml_fallback_keeps_relationship_values(self):
        doc = parse_source_doc(MALFORMED_FENCED)
        assert doc.title == "Malformed recovery"
        assert extract_relationships(doc.extensions) == [
            {
                "concept_id": "knowledge/spec/x",
                "relation": "relates-to",
                "kind": "extracted",
            }
        ]


# --- relationship normalization ---


class TestNormalizeRelationships:
    def test_idempotent_on_normalized_entries(self):
        entries = [
            {"concept_id": "a", "relation": "supersedes", "kind": "extracted"}
        ]
        assert normalize_relationships(entries) == entries

    def test_single_dict_and_bare_string_shapes(self):
        assert extract_relationships(
            {"relates_to": {"concept_id": "x", "relation": "references"}}
        ) == [{"concept_id": "x", "relation": "references", "kind": "extracted"}]
        assert extract_relationships({"supersedes": "one-id"}) == [
            {"concept_id": "one-id", "relation": "supersedes", "kind": "extracted"}
        ]

    def test_entries_without_concept_id_are_dropped(self):
        assert (
            extract_relationships({"relates_to": [{"relation": "references"}]}) == []
        )

    def test_duplicate_entries_dedupe(self):
        entries = extract_relationships(
            {
                "relates_to": [{"concept_id": "x", "relation": "supersedes"}],
                "supersedes": ["x"],
            }
        )
        assert entries == [
            {"concept_id": "x", "relation": "supersedes", "kind": "extracted"}
        ]

    def test_declared_relation_kind_and_extra_keys_pass_through(self):
        entries = extract_relationships(
            {
                "relates_to": [
                    {
                        "concept_id": "y",
                        "relation": "replaces",
                        "kind": "inferred",
                        "note": "kept",
                    }
                ]
            }
        )
        assert entries == [
            {
                "concept_id": "y",
                "relation": "replaces",
                "kind": "inferred",
                "note": "kept",
            }
        ]


# --- staging: manifest rows carry relationships BEFORE --ingest ---


class TestStagingCarriesRelationships:
    def test_manifest_row_carries_declared_relationships(self, tmp_path):
        manifest = _stage_one(tmp_path, LINKED_ADR, "docs/adr/0007-use-events.md")
        row = manifest["rows"][0]
        assert row["relationships"] == [
            {
                "concept_id": "knowledge/decision/0006-raw-logs",
                "relation": "relates-to",
                "kind": "extracted",
            }
        ]

    def test_supersede_keys_normalize_into_relationship_entries(self, tmp_path):
        manifest = _stage_one(tmp_path, SUPERSEDE_ADR, "docs/adr/0002-mesh.md")
        assert manifest["rows"][0]["relationships"] == [
            {
                "concept_id": "knowledge/decision/0001-raw-logs",
                "relation": "supersedes",
                "kind": "extracted",
            },
            {
                "concept_id": "knowledge/decision/0003-mesh-v2",
                "relation": "superseded-by",
                "kind": "extracted",
            },
        ]

    def test_missing_kind_defaults_to_extracted_in_row(self, tmp_path):
        manifest = _stage_one(tmp_path, KINDLESS_LINK, "docs/spec/kindless.md")
        assert manifest["rows"][0]["relationships"] == [
            {
                "concept_id": "knowledge/spec/other",
                "relation": "references",
                "kind": "extracted",
            }
        ]

    def test_plain_doc_row_has_no_relationships_key(self, tmp_path):
        manifest = _stage_one(tmp_path, PLAIN_DOC, "docs/plain.md")
        row = manifest["rows"][0]
        assert "relationships" not in row
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

    def test_staged_file_frontmatter_carries_relates_to(self, tmp_path):
        manifest = _stage_one(tmp_path, LINKED_ADR, "docs/adr/0007-use-events.md")
        row = manifest["rows"][0]
        staged = OKFConcept.from_file(str(tmp_path / "outbox" / row["staged_path"]))
        assert staged.extensions["relates_to"] == row["relationships"]

    def test_dry_run_never_touches_a_store(self, tmp_path):
        docs = tmp_path / "docs"
        docs.mkdir()
        (docs / "adr.md").write_text(LINKED_ADR, encoding="utf-8")
        manifest = run_ingest(
            files=[], dirs=[docs], outbox=tmp_path / "outbox"
        )
        assert manifest["rows"][0]["relationships"]
        assert list(tmp_path.rglob("*.sqlite*")) == []
        assert [p.name for p in tmp_path.rglob(".knowledge")] == []


# --- execute path: promoted frontmatter carries the links ---


class TestPromotedFrontmatter:
    def test_relates_to_survives_verbatim(self, workspace):
        _ingest(workspace, LINKED_ADR)
        doc = list_documents(_bundle())[0]
        assert doc.extensions["relates_to"] == [
            {
                "concept_id": "knowledge/decision/0006-raw-logs",
                "relation": "relates-to",
                "kind": "extracted",
            }
        ]

    def test_supersede_entries_reach_promoted_frontmatter(self, workspace):
        _ingest(workspace, SUPERSEDE_ADR)
        rels = list_documents(_bundle())[0].extensions["relates_to"]
        assert {
            "concept_id": "knowledge/decision/0001-raw-logs",
            "relation": "supersedes",
            "kind": "extracted",
        } in rels
        assert {
            "concept_id": "knowledge/decision/0003-mesh-v2",
            "relation": "superseded-by",
            "kind": "extracted",
        } in rels

    def test_missing_kind_promotes_as_extracted(self, workspace):
        _ingest(workspace, KINDLESS_LINK)
        doc = list_documents(_bundle())[0]
        assert doc.extensions["relates_to"][0]["kind"] == "extracted"

    def test_plain_doc_promotes_unchanged(self, workspace):
        _ingest(workspace, PLAIN_DOC)
        doc = list_documents(_bundle())[0]
        assert "relates_to" not in doc.extensions
        assert set(doc.extensions) == {
            "tier",
            "doc_status",
            "doc_owner",
            "doc_source",
            "epic_link",
            "affects_modules",
            "affects_repos",
        }


# --- store: add_document relationships parameter ---


class TestAddDocumentRelationships:
    def test_relationships_parameter_writes_relates_to_extension(self, workspace):
        bundle = _bundle()
        cid = add_document(
            bundle,
            title="New decision",
            body="Supersedes the old one.",
            doc_type="decision",
            relationships=[
                {
                    "concept_id": "knowledge/decision/0001-old",
                    "relation": "supersedes",
                }
            ],
        )
        doc = bundle.read_concept(cid)
        assert doc.extensions["relates_to"] == [
            {
                "concept_id": "knowledge/decision/0001-old",
                "relation": "supersedes",
                "kind": "extracted",
            }
        ]

    def test_no_relationships_parameter_keeps_frontmatter_unchanged(self, workspace):
        bundle = _bundle()
        cid = add_document(bundle, title="Bare", body=".", doc_type="spec")
        doc = bundle.read_concept(cid)
        assert "relates_to" not in doc.extensions


# --- CLI: dry-run transparency + unchanged flag surface ---


class TestIngestCliTransparency:
    def test_dry_run_stdout_surfaces_relationships(self, workspace):
        docs = workspace / "docs"
        docs.mkdir()
        (docs / "adr.md").write_text(SUPERSEDE_ADR, encoding="utf-8")
        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        result = CliRunner().invoke(
            knowledge,
            ["ingest", "--dir", str(docs), "--outbox", str(workspace / "outbox")],
        )
        assert result.exit_code == 0, result.output
        assert (
            "supersedes: knowledge/decision/0001-raw-logs (extracted)"
            in result.output
        )
        assert (
            "superseded-by: knowledge/decision/0003-mesh-v2 (extracted)"
            in result.output
        )
        # Dry run: no store write happened.
        assert not resolve_store().knowledge.exists()

    def test_ingest_flags_unchanged(self):
        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        result = CliRunner().invoke(knowledge, ["ingest", "--help"])
        assert result.exit_code == 0
        for flag in (
            "--file",
            "--dir",
            "--repo",
            "--ingest",
            "--include-drafts",
            "--outbox",
        ):
            assert flag in result.output
