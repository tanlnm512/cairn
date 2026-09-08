"""Pointer resolution: the basename suffix fallback resolves only a
UNIQUE basename match.

Pinned behaviors:

1. A pointer that matches exactly one resource — directly, or after the
   resource-prefix extension for fed documents — resolves and indexes as
   before.
2. When several resources share the pointer's basename, nothing is
   picked: the pointer indexes nothing and surfaces through the
   dangling-warning channel as an ``ambiguous pointer`` naming the
   candidate resource paths — in the rebuild report, the dry-run
   manifest check, the executor report, and the CLI output.
3. Exact resolution is unaffected: a full concept id or an exact
   resource path resolves even when other resources share its basename.
"""
from __future__ import annotations

import pytest

from cairn.knowledge.index import (
    dangling_manifest_pointers,
    rebuild_knowledge_index,
)
from cairn.knowledge.ingest import run_ingest
from cairn.knowledge.ingest.executor import execute_manifest
from cairn.knowledge.store import add_document
from cairn.okf.bundle import OKFBundle
from cairn.paths import resolve_store

pytestmark = pytest.mark.usefixtures("hash_backend")

TARGET_ALPHA = "---\ntitle: Alpha target\nstatus: accepted\n---\nAlpha body.\n"
TARGET_BETA = "---\ntitle: Beta target\nstatus: accepted\n---\nBeta body.\n"
LINKER_BASENAME = (
    "---\ntitle: Basename linker\nstatus: accepted\n"
    "relates_to: [target.md]\n---\nNames a target by bare basename.\n"
)
LINKER_EXPLICIT = (
    "---\ntitle: Explicit linker\nstatus: accepted\n"
    "relates_to: [beta/target.md]\n---\nNames one target by its fuller path.\n"
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


def _conn():
    from cairn.cli.main import get_db

    resolve_store().ensure()
    return get_db()


def _rebuild():
    """One rebuild on a fresh connection (committed); returns the report."""
    conn = _conn()
    try:
        report = rebuild_knowledge_index(conn, _bundle())
        conn.commit()
        return report
    finally:
        conn.close()


def _edges(kind, relation):
    """Sorted (doc_id, related_id, relation, kind) tuples."""
    conn = _conn()
    try:
        return sorted(
            (r[0], r[1], r[2], r[3])
            for r in conn.execute(
                "SELECT doc_id, related_id, relation, kind FROM knowledge_edges "
                "WHERE kind = ? AND relation = ?",
                (kind, relation),
            ).fetchall()
        )
    finally:
        conn.close()


def _write_docs(directory, files):
    directory.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return directory


def _stage(workspace, files, subdir="docs", outbox="outbox"):
    docs = _write_docs(workspace / subdir, files)
    return run_ingest(files=[], dirs=[docs], outbox=workspace / outbox)


def _execute(manifest):
    conn = _conn()
    try:
        return execute_manifest(manifest, conn)
    finally:
        conn.close()


def _cli(*args):
    from click.testing import CliRunner
    from cairn.cli.knowledge import knowledge

    return CliRunner().invoke(knowledge, list(args))


def _add_doc(bundle, title, *, resource=None, relationships=None):
    return add_document(
        bundle,
        title=title,
        body=f"Body of {title}.",
        doc_type="spec",
        resource=resource,
        relationships=relationships,
    )


# --- 1. unique basename matches still resolve -------------------------------


class TestUniqueSuffixStillResolves:
    def test_unique_basename_pointer_indexes_and_warns_nothing(self, workspace):
        bundle = _bundle()
        cid_b = _add_doc(bundle, "Only target", resource="docs/target.md")
        cid_a = _add_doc(
            bundle,
            "Basename source",
            relationships=[
                {"concept_id": "target.md", "relation": "relates-to",
                 "kind": "extracted"}
            ],
        )
        report = _rebuild()
        assert report["dangling"] == []
        rows = _edges("extracted", "relates-to")
        assert rows == [(cid_a, cid_b, "relates-to", "extracted")]

    def test_unique_nested_basename_ingest_indexes_and_warns_nothing(
        self, workspace
    ):
        docs = _write_docs(workspace / "docs", {
            "alpha/target.md": TARGET_ALPHA,
            "linker.md": LINKER_BASENAME,
        })
        result = _cli("ingest", "--dir", str(docs),
                      "--outbox", str(workspace / "ob"), "--ingest")
        assert result.exit_code == 0, result.output
        assert "warning" not in result.output.lower()
        rows = _edges("extracted", "relates-to")
        assert len(rows) == 1


# --- 2. ambiguous basename matches index nothing and name candidates --------


class TestAmbiguousSuffixIndexesNothing:
    def test_rebuild_indexes_nothing_and_reports_candidates(self, workspace):
        bundle = _bundle()
        _add_doc(bundle, "Docs target", resource="docs/target.md")
        _add_doc(bundle, "Archive target", resource="archive/target.md")
        cid_a = _add_doc(
            bundle,
            "Basename source",
            relationships=[
                {"concept_id": "target.md", "relation": "relates-to",
                 "kind": "extracted"}
            ],
        )
        report = _rebuild()
        assert report["dangling"] == [
            {
                "doc_id": cid_a,
                "concept_id": "target.md",
                "reason": "ambiguous pointer",
                "candidates": ["docs/target.md", "archive/target.md"],
            }
        ]
        assert _edges("extracted", "relates-to") == []

    def test_dry_run_manifest_pointers_report_candidates(self, workspace):
        manifest = _stage(workspace, {
            "alpha/target.md": TARGET_ALPHA,
            "beta/target.md": TARGET_BETA,
            "linker.md": LINKER_BASENAME,
        }, outbox="ob")
        dangling = dangling_manifest_pointers(_bundle(), manifest["rows"])
        assert dangling == [
            {
                "doc_id": "workspace/linker.md",
                "concept_id": "target.md",
                "reason": "ambiguous pointer",
                "candidates": [
                    "workspace/beta/target.md",
                    "workspace/alpha/target.md",
                ],
            }
        ]

    def test_execute_report_carries_ambiguous_pointers(self, workspace):
        manifest = _stage(workspace, {
            "alpha/target.md": TARGET_ALPHA,
            "beta/target.md": TARGET_BETA,
            "linker.md": LINKER_BASENAME,
        })
        report = _execute(manifest)
        assert report["count_ok"] is True
        (linker,) = (
            row for row in manifest["rows"]
            if row.get("source_path", "").endswith("linker.md")
        )
        assert report["dangling_pointers"] == [
            {
                "doc_id": linker["concept_id"],
                "concept_id": "target.md",
                "reason": "ambiguous pointer",
                "candidates": [
                    "workspace/beta/target.md",
                    "workspace/alpha/target.md",
                ],
            }
        ]
        assert _edges("extracted", "relates-to") == []

    def test_exact_resource_path_beats_basename_ambiguity(self, workspace):
        bundle = _bundle()
        _add_doc(bundle, "Docs target", resource="docs/target.md")
        cid_b = _add_doc(bundle, "Beta target", resource="beta/target.md")
        cid_a = _add_doc(
            bundle,
            "Explicit source",
            relationships=[
                {"concept_id": "beta/target.md", "relation": "relates-to",
                 "kind": "extracted"}
            ],
        )
        report = _rebuild()
        assert report["dangling"] == []
        rows = _edges("extracted", "relates-to")
        assert rows == [(cid_a, cid_b, "relates-to", "extracted")]


# --- 3. exact concept-id and raw-path resolution unaffected -----------------


class TestExactResolutionUnaffected:
    def test_concept_id_pointer_resolves_despite_basename_ambiguity(
        self, workspace
    ):
        bundle = _bundle()
        cid_b = _add_doc(bundle, "Docs target", resource="docs/target.md")
        _add_doc(bundle, "Archive target", resource="archive/target.md")
        cid_a = _add_doc(
            bundle,
            "Concept-id source",
            relationships=[
                {"concept_id": cid_b, "relation": "relates-to",
                 "kind": "extracted"}
            ],
        )
        report = _rebuild()
        assert report["dangling"] == []
        rows = _edges("extracted", "relates-to")
        assert rows == [(cid_a, cid_b, "relates-to", "extracted")]


# --- 4. the warnings reach the CLI output ------------------------------------


class TestAmbiguousIngestWarnings:
    def _ambiguity_docs(self, workspace, subdir):
        return _write_docs(workspace / subdir, {
            "alpha/target.md": TARGET_ALPHA,
            "beta/target.md": TARGET_BETA,
            "linker.md": LINKER_BASENAME,
        })

    def test_dry_run_warns_naming_candidates(self, workspace):
        docs = self._ambiguity_docs(workspace, "docs")
        result = _cli("ingest", "--dir", str(docs),
                      "--outbox", str(workspace / "ob"))
        assert result.exit_code == 0, result.output
        assert "ambiguous pointer" in result.output
        assert "workspace/alpha/target.md" in result.output
        assert "workspace/beta/target.md" in result.output

    def test_executed_ingest_indexes_nothing_and_warns(self, workspace):
        docs = self._ambiguity_docs(workspace, "docs")
        result = _cli("ingest", "--dir", str(docs),
                      "--outbox", str(workspace / "ob"), "--ingest")
        assert result.exit_code == 0, result.output
        assert "ambiguous pointer" in result.output
        assert "workspace/alpha/target.md" in result.output
        assert "workspace/beta/target.md" in result.output
        assert _edges("extracted", "relates-to") == []
