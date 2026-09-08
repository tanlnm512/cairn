"""ADR supersede-chain detection at ingest (architecture D1.2).

Covers the validation-contract behaviors: VAL-INGEST-004 (a "supersedes
ADR-NNNN" marker detects the relationship with no explicit frontmatter)
and VAL-INGEST-005 (the inverse ``superseded-by`` pointer is written on
the old doc, both directions durable), across the detection module,
staging manifest, promoted frontmatter, and dry-run CLI output.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cairn.knowledge.ingest import run_ingest
from cairn.knowledge.ingest.adr import detect_supersede_relationships
from cairn.knowledge.ingest.classifier import classify_doc
from cairn.knowledge.ingest.executor import execute_manifest
from cairn.knowledge.ingest.identity import build_identity
from cairn.knowledge.ingest.parser import parse_source_doc
from cairn.knowledge.ingest.staging import StagedEntry, stage_outbox
from cairn.knowledge.store import list_documents
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept
from cairn.paths import resolve_store

ADR_0001 = (
    "---\n"
    "title: Use Postgres for billing\n"
    "status: accepted\n"
    "---\n"
    "# ADR-0001: Use Postgres for billing\n\n"
    "Billing keeps its ledger in Postgres.\n"
)

# Marker in the body; no relationship frontmatter anywhere.
ADR_0002_SUPERSEDES = (
    "---\n"
    "title: Use CockroachDB for billing\n"
    "status: accepted\n"
    "---\n"
    "# ADR-0002: Use CockroachDB for billing\n\n"
    "Supersedes ADR-0001.\n"
)

# Marker on the OLD doc only ("superseded by" form).
ADR_0001_SUPERSEDED = (
    "---\n"
    "title: Use Postgres for billing\n"
    "status: accepted\n"
    "---\n"
    "# ADR-0001: Use Postgres for billing\n\n"
    "Superseded by ADR-0002.\n"
)

# Marker in the frontmatter status line of the OLD doc.
ADR_0001_STATUS_MARKER = (
    "---\n"
    "title: Use Postgres for billing\n"
    "status: superseded by ADR-0002\n"
    "---\n"
    "Billing keeps its ledger in Postgres.\n"
)

ADR_0002_PLAIN = (
    "---\n"
    "title: Use CockroachDB for billing\n"
    "status: accepted\n"
    "---\n"
    "Billing moves to CockroachDB.\n"
)

# Old doc blocked by the FR-005 draft-status gate unless --include-drafts.
ADR_0001_SUPERSEDED_STATUS = (
    "---\n"
    "title: Use Postgres for billing\n"
    "status: superseded\n"
    "---\n"
    "Billing keeps its ledger in Postgres.\n"
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Hermetic store: every write lands under tmp_path (see
    test_ingest_relationships.py for the rationale behind each env patch)."""
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


def _entry(repo, relpath, text, include_drafts=False):
    parsed = parse_source_doc(text)
    return StagedEntry(
        repo=repo,
        relpath=relpath,
        origin="repo-scan",
        parsed=parsed,
        classification=classify_doc(parsed, relpath, include_drafts),
        identity=build_identity(repo, relpath, parsed),
    )


def _expected_cid(repo, relpath, text):
    parsed = parse_source_doc(text)
    identity = build_identity(repo, relpath, parsed)
    return f"knowledge/decision/{identity.slug}"


def _write_pair(workspace, old_text, new_text):
    decisions = workspace / "decisions"
    decisions.mkdir(exist_ok=True)
    (decisions / "0001-use-postgres.md").write_text(old_text, encoding="utf-8")
    (decisions / "0002-use-cockroachdb.md").write_text(new_text, encoding="utf-8")
    return decisions


def _stage_pair(
    workspace,
    old_text=ADR_0001,
    new_text=ADR_0002_SUPERSEDES,
    outbox="outbox",
    include_drafts=False,
):
    """Stage the pair through the repo-scan layout (relpaths keep the
    ``decisions/`` prefix, like ``cairn knowledge ingest --repo .``)."""
    _write_pair(workspace, old_text, new_text)
    return run_ingest(
        files=[],
        dirs=[],
        repos=[workspace],
        outbox=workspace / outbox,
        include_drafts=include_drafts,
    )


def _row_by_source(manifest, suffix):
    for row in manifest["rows"]:
        if row.get("source_path", "").endswith(suffix):
            return row
    raise AssertionError(f"no manifest row for {suffix}")


# --- detection module: markers resolve to staged concept ids ---


class TestDetectSupersedeRelationships:
    def test_body_marker_links_new_doc_to_old(self):
        old_id = _expected_cid("acme", "decisions/0001-use-postgres.md", ADR_0001)
        new_id = _expected_cid(
            "acme", "decisions/0002-use-cockroachdb.md", ADR_0002_SUPERSEDES
        )
        detected = detect_supersede_relationships(
            [
                _entry("acme", "decisions/0001-use-postgres.md", ADR_0001),
                _entry(
                    "acme", "decisions/0002-use-cockroachdb.md", ADR_0002_SUPERSEDES
                ),
            ]
        )
        assert detected[("acme", "decisions/0002-use-cockroachdb.md")] == [
            {"concept_id": old_id, "relation": "supersedes", "kind": "extracted"}
        ]
        assert detected[("acme", "decisions/0001-use-postgres.md")] == [
            {"concept_id": new_id, "relation": "superseded-by", "kind": "extracted"}
        ]

    def test_superseded_by_marker_on_old_doc_writes_both_halves(self):
        old_id = _expected_cid("acme", "decisions/0001-use-postgres.md", ADR_0001)
        new_id = _expected_cid(
            "acme", "decisions/0002-use-cockroachdb.md", ADR_0002_PLAIN
        )
        detected = detect_supersede_relationships(
            [
                _entry("acme", "decisions/0001-use-postgres.md", ADR_0001_SUPERSEDED),
                _entry("acme", "decisions/0002-use-cockroachdb.md", ADR_0002_PLAIN),
            ]
        )
        assert detected[("acme", "decisions/0001-use-postgres.md")] == [
            {"concept_id": new_id, "relation": "superseded-by", "kind": "extracted"}
        ]
        assert detected[("acme", "decisions/0002-use-cockroachdb.md")] == [
            {"concept_id": old_id, "relation": "supersedes", "kind": "extracted"}
        ]

    def test_markers_on_both_docs_collapse_to_one_edge(self):
        detected = detect_supersede_relationships(
            [
                _entry("acme", "decisions/0001-use-postgres.md", ADR_0001_SUPERSEDED),
                _entry("acme", "decisions/0002-use-cockroachdb.md", ADR_0002_SUPERSEDES),
            ]
        )
        assert len(detected[("acme", "decisions/0001-use-postgres.md")]) == 1
        assert len(detected[("acme", "decisions/0002-use-cockroachdb.md")]) == 1

    def test_status_line_marker_is_detected(self):
        new_id = _expected_cid(
            "acme", "decisions/0002-use-cockroachdb.md", ADR_0002_PLAIN
        )
        detected = detect_supersede_relationships(
            [
                _entry(
                    "acme", "decisions/0001-use-postgres.md", ADR_0001_STATUS_MARKER
                ),
                _entry("acme", "decisions/0002-use-cockroachdb.md", ADR_0002_PLAIN),
            ]
        )
        assert detected[("acme", "decisions/0001-use-postgres.md")] == [
            {"concept_id": new_id, "relation": "superseded-by", "kind": "extracted"}
        ]
        assert detected[("acme", "decisions/0002-use-cockroachdb.md")] == [
            {
                "concept_id": _expected_cid(
                    "acme", "decisions/0001-use-postgres.md", ADR_0001_STATUS_MARKER
                ),
                "relation": "supersedes",
                "kind": "extracted",
            }
        ]

    @pytest.mark.parametrize(
        "marker",
        [
            "SUPERSEDES ADR-0001",
            "supersedes ADR-0001.",
            "supersedes: ADR-0001",
            "supersedes adr-0001",
            "supersedes 0001",
            "This decision supersedes ADR-0001 outright.",
            "**Status:** Supersedes ADR-0001",
            "superseded-by ADR-0002",
            "Superseded by ADR-0002; see the migration plan.",
        ],
    )
    def test_marker_wording_variants(self, marker):
        """Each wording produces the same single canonical edge, whichever
        half of the pair carries it: the edge is (0002 supersedes 0001),
        and both frontmatter halves are emitted."""
        old_text = ADR_0001.replace(
            "Billing keeps its ledger in Postgres.", marker
        )
        new_text = ADR_0002_PLAIN.replace(
            "Billing moves to CockroachDB.", marker
        )
        old_id = _expected_cid("acme", "decisions/0001-use-postgres.md", old_text)
        new_id = _expected_cid("acme", "decisions/0002-use-cockroachdb.md", new_text)
        detected = detect_supersede_relationships(
            [
                _entry("acme", "decisions/0001-use-postgres.md", old_text),
                _entry("acme", "decisions/0002-use-cockroachdb.md", new_text),
            ]
        )
        assert detected[("acme", "decisions/0001-use-postgres.md")] == [
            {"concept_id": new_id, "relation": "superseded-by", "kind": "extracted"}
        ]
        assert detected[("acme", "decisions/0002-use-cockroachdb.md")] == [
            {"concept_id": old_id, "relation": "supersedes", "kind": "extracted"}
        ]

    def test_marker_inside_code_fence_is_ignored(self):
        fenced = ADR_0002_PLAIN.replace(
            "Billing moves to CockroachDB.",
            "Example template:\n\n"
            "    ```\n\n    Supersedes ADR-0001.\n\n    ```\n",
        )
        detected = detect_supersede_relationships(
            [
                _entry("acme", "decisions/0001-use-postgres.md", ADR_0001),
                _entry("acme", "decisions/0002-use-cockroachdb.md", fenced),
            ]
        )
        assert detected == {}

    def test_self_reference_is_dropped(self):
        self_ref = ADR_0001.replace(
            "Billing keeps its ledger in Postgres.", "Supersedes ADR-0001."
        )
        detected = detect_supersede_relationships(
            [_entry("acme", "decisions/0001-use-postgres.md", self_ref)]
        )
        assert detected == {}

    def test_unresolvable_number_is_dropped(self):
        orphan = ADR_0002_PLAIN.replace(
            "Billing moves to CockroachDB.", "Supersedes ADR-0009."
        )
        detected = detect_supersede_relationships(
            [
                _entry("acme", "decisions/0001-use-postgres.md", ADR_0001),
                _entry("acme", "decisions/0002-use-cockroachdb.md", orphan),
            ]
        )
        assert detected == {}

    def test_numbered_doc_outside_adr_directory_is_not_a_target(self):
        orphan = ADR_0002_PLAIN.replace(
            "Billing moves to CockroachDB.", "Supersedes ADR-0001."
        )
        detected = detect_supersede_relationships(
            [
                _entry("acme", "notes/0001-use-postgres.md", ADR_0001),
                _entry("acme", "decisions/0002-use-cockroachdb.md", orphan),
            ]
        )
        assert detected == {}

    def test_root_level_numbered_docs_resolve(self):
        """Fed-directory layout: the fed root IS the decisions directory,
        so numbered stems keep their ADR meaning without a dir prefix."""
        old_id = _expected_cid("workspace", "0001-use-postgres.md", ADR_0001)
        new_id = _expected_cid(
            "workspace", "0002-use-cockroachdb.md", ADR_0002_SUPERSEDES
        )
        detected = detect_supersede_relationships(
            [
                _entry("workspace", "0001-use-postgres.md", ADR_0001),
                _entry("workspace", "0002-use-cockroachdb.md", ADR_0002_SUPERSEDES),
            ]
        )
        assert detected[("workspace", "0002-use-cockroachdb.md")] == [
            {"concept_id": old_id, "relation": "supersedes", "kind": "extracted"}
        ]
        assert detected[("workspace", "0001-use-postgres.md")] == [
            {"concept_id": new_id, "relation": "superseded-by", "kind": "extracted"}
        ]

    def test_skipped_entries_neither_emit_nor_resolve(self):
        detected = detect_supersede_relationships(
            [
                # status: superseded is gated out by default -> no target.
                _entry(
                    "acme",
                    "decisions/0001-use-postgres.md",
                    ADR_0001_SUPERSEDED_STATUS,
                ),
                _entry("acme", "decisions/0002-use-cockroachdb.md", ADR_0002_SUPERSEDES),
            ]
        )
        assert detected == {}

    def test_docs_without_markers_are_absent(self):
        detected = detect_supersede_relationships(
            [
                _entry("acme", "decisions/0001-use-postgres.md", ADR_0001),
                _entry("acme", "decisions/0002-use-cockroachdb.md", ADR_0002_PLAIN),
            ]
        )
        assert detected == {}


# --- staging: manifest rows and staged files carry detected links ---


class TestStagingCarriesDetectedRelationships:
    def test_manifest_rows_carry_both_directions(self, tmp_path):
        manifest = _stage_pair(tmp_path)
        old_row = _row_by_source(manifest, "0001-use-postgres.md")
        new_row = _row_by_source(manifest, "0002-use-cockroachdb.md")
        assert new_row["relationships"] == [
            {
                "concept_id": old_row["concept_id"],
                "relation": "supersedes",
                "kind": "extracted",
            }
        ]
        assert old_row["relationships"] == [
            {
                "concept_id": new_row["concept_id"],
                "relation": "superseded-by",
                "kind": "extracted",
            }
        ]

    def test_staged_file_frontmatter_shows_detected_links(self, tmp_path):
        manifest = _stage_pair(tmp_path)
        row = _row_by_source(manifest, "0002-use-cockroachdb.md")
        staged = OKFConcept.from_file(str(tmp_path / "outbox" / row["staged_path"]))
        assert staged.extensions["relates_to"] == row["relationships"]

    def test_declared_and_detected_relationships_dedupe(self, tmp_path):
        probe = _stage_pair(tmp_path, ADR_0001, ADR_0002_PLAIN, outbox="probe")
        old_id = _row_by_source(probe, "0001-use-postgres.md")["concept_id"]
        declared = (
            "---\n"
            "title: Use CockroachDB for billing\n"
            "status: accepted\n"
            f"supersedes: [{old_id}]\n"
            "---\n"
            "Supersedes ADR-0001.\n"
        )
        _write_pair(tmp_path, ADR_0001, declared)
        manifest = run_ingest(
            files=[], dirs=[], repos=[tmp_path], outbox=tmp_path / "outbox"
        )
        new_row = _row_by_source(manifest, "0002-use-cockroachdb.md")
        assert new_row["relationships"] == [
            {"concept_id": old_id, "relation": "supersedes", "kind": "extracted"}
        ]

    def test_include_drafts_admits_a_superseded_target(self, tmp_path):
        # Default run: the superseded old doc is gated out, no edge.
        blocked = _stage_pair(
            tmp_path,
            ADR_0001_SUPERSEDED_STATUS,
            ADR_0002_SUPERSEDES,
            outbox="blocked",
        )
        assert "relationships" not in _row_by_source(
            blocked, "0002-use-cockroachdb.md"
        )
        # --include-drafts readmits it (tagged draft); the edge resolves.
        readmitted = _stage_pair(
            tmp_path,
            ADR_0001_SUPERSEDED_STATUS,
            ADR_0002_SUPERSEDES,
            include_drafts=True,
        )
        old_row = _row_by_source(readmitted, "0001-use-postgres.md")
        new_row = _row_by_source(readmitted, "0002-use-cockroachdb.md")
        assert new_row["relationships"] == [
            {
                "concept_id": old_row["concept_id"],
                "relation": "supersedes",
                "kind": "extracted",
            }
        ]
        assert old_row["relationships"] == [
            {
                "concept_id": new_row["concept_id"],
                "relation": "superseded-by",
                "kind": "extracted",
            }
        ]

    def test_plain_adr_pair_stages_no_relationships(self, tmp_path):
        manifest = _stage_pair(tmp_path, ADR_0001, ADR_0002_PLAIN)
        for row in manifest["rows"]:
            assert "relationships" not in row

    def test_fed_directory_layout_carries_both_directions(self, tmp_path):
        """Fed layout (--dir decisions): relpaths are root-level numbered
        stems and detection still resolves the pair."""
        decisions = _write_pair(tmp_path, ADR_0001, ADR_0002_SUPERSEDES)
        manifest = run_ingest(
            files=[], dirs=[decisions], outbox=tmp_path / "outbox"
        )
        old_row = _row_by_source(manifest, "0001-use-postgres.md")
        new_row = _row_by_source(manifest, "0002-use-cockroachdb.md")
        assert new_row["relationships"][0]["concept_id"] == old_row["concept_id"]
        assert old_row["relationships"][0]["concept_id"] == new_row["concept_id"]

    def test_stage_outbox_detects_without_run_ingest(self, tmp_path):
        manifest = stage_outbox(
            [
                _entry("acme", "decisions/0001-use-postgres.md", ADR_0001),
                _entry(
                    "acme", "decisions/0002-use-cockroachdb.md", ADR_0002_SUPERSEDES
                ),
            ],
            tmp_path / "outbox",
        )
        old_row = _row_by_source(manifest, "0001-use-postgres.md")
        new_row = _row_by_source(manifest, "0002-use-cockroachdb.md")
        assert new_row["relationships"][0]["concept_id"] == old_row["concept_id"]


# --- execute path: promoted frontmatter carries both directions ---


def _promoted_docs_by_id():
    """Promoted docs keyed by bare concept_id.

    Bundle reads hand back path-shaped concept_ids (see the memory
    store's ``relative_to(bundle.root)`` normalization for the same
    workaround); rows and detected pointers use bare ids.
    """
    bundle = _bundle()
    out = {}
    for doc in list_documents(bundle):
        try:
            cid = str(Path(doc.concept_id).relative_to(bundle.root))
        except ValueError:
            cid = doc.concept_id
        out[cid] = doc
    return out


class TestPromotedSupersedeFrontmatter:
    def _ingest_pair(self, workspace, old_text=ADR_0001, new_text=ADR_0002_SUPERSEDES):
        manifest = _stage_pair(workspace, old_text, new_text)
        from cairn.cli.main import get_db

        resolve_store().ensure()
        conn = get_db()
        try:
            execute_manifest(manifest, conn)
        finally:
            conn.close()
        return manifest

    def test_promoted_frontmatter_carries_both_directions(self, workspace):
        manifest = self._ingest_pair(workspace)
        old_id = _row_by_source(manifest, "0001-use-postgres.md")["concept_id"]
        new_id = _row_by_source(manifest, "0002-use-cockroachdb.md")["concept_id"]
        docs = _promoted_docs_by_id()
        # VAL-INGEST-004: the new doc's frontmatter carries supersedes.
        assert docs[new_id].extensions["relates_to"] == [
            {"concept_id": old_id, "relation": "supersedes", "kind": "extracted"}
        ]
        # VAL-INGEST-005: the old doc's frontmatter carries superseded-by.
        assert docs[old_id].extensions["relates_to"] == [
            {"concept_id": new_id, "relation": "superseded-by", "kind": "extracted"}
        ]

    def test_promoted_old_doc_keeps_its_own_extensions(self, workspace):
        manifest = self._ingest_pair(workspace)
        old_id = _row_by_source(manifest, "0001-use-postgres.md")["concept_id"]
        doc = _promoted_docs_by_id()[old_id]
        assert doc.extensions["tier"] == "asserted"
        assert doc.extensions["doc_status"] == "active"
        assert doc.extensions["doc_source"] == "imported"
        assert doc.extensions["affects_modules"] == ["decisions"]


# --- CLI: dry-run output surfaces detected relationships pre-ingest ---


class TestIngestCliSurfacesDetection:
    def test_dry_run_stdout_lists_detected_relationships(self, workspace):
        decisions = _write_pair(workspace, ADR_0001, ADR_0002_SUPERSEDES)
        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        result = CliRunner().invoke(
            knowledge,
            [
                "ingest",
                "--dir",
                str(decisions),
                "--outbox",
                str(workspace / "outbox"),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "supersedes: knowledge/decision/" in result.output
        assert "superseded-by: knowledge/decision/" in result.output
        # Dry run: no store write happened.
        assert not resolve_store().knowledge.exists()
