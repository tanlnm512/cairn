"""Doc-to-code reference verification at ingest (architecture D1.3).

Covers the validation-contract behavior VAL-INGEST-007: backticked file
paths and symbols in an ingested doc body resolve against the L1 graph
via ``cairn.refs`` (the wiki verified-sources pattern); only resolvable
refs land in the promoted OKF ``verified`` family and in the
``knowledge_doc_refs`` index, and a bogus ref appears nowhere.
"""
from __future__ import annotations

import pytest

from cairn.knowledge.ingest import run_ingest
from cairn.knowledge.ingest.executor import execute_manifest
from cairn.knowledge.store import add_document
from cairn.okf.bundle import OKFBundle
from cairn.paths import resolve_store

EXISTING_FILE = "src/demo_pkg/store.py"
EXISTING_SYMBOL = "resolve_refs_target"
QUALIFIED_SYMBOL = "demo_pkg.load"
BOGUS_FILE = "src/no/such/file.py"

REFS_DOC = (
    "---\n"
    "title: Refs doc\n"
    "status: accepted\n"
    "---\n"
    f"Stores state in `{EXISTING_FILE}` and calls `{EXISTING_SYMBOL}()`;\n"
    f"entries load through `{QUALIFIED_SYMBOL}`. The path `{BOGUS_FILE}`\n"
    "does not exist.\n"
)

PLAIN_DOC = (
    "---\n"
    "title: Plain doc\n"
    "status: accepted\n"
    "tags: [ops]\n"
    "---\n"
    "Ordinary notes with no backticked references.\n"
)

ALL_BOGUS_DOC = (
    "---\n"
    "title: All bogus doc\n"
    "status: accepted\n"
    "---\n"
    f"Points at `{BOGUS_FILE}` and `missing_symbol_fn` only.\n"
)

EXPECTED_VERIFIED = [
    {"ref": EXISTING_FILE, "kind": "file", "verified": True},
    {"ref": f"{EXISTING_SYMBOL}()", "kind": "symbol", "verified": True},
    {"ref": QUALIFIED_SYMBOL, "kind": "symbol", "verified": True},
]


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


def _seed_graph(workspace):
    """Seed the store DB's L1 tables with one file + its symbols.

    Paths are stored absolute (as ``cairn build`` does), so suffix-style
    refs (``src/demo_pkg/store.py``) resolve through ``file_exists``'s
    file-path-suffix arm, and ``fixture/...`` refs resolve through the
    repo-qualification bridge.
    """
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO repos (id, name, path) "
            "VALUES ('fixture', 'fixture', ?)",
            (str(workspace),),
        )
        conn.execute(
            "INSERT INTO files (id, repo_id, path, language) "
            "VALUES (1, 'fixture', ?, 'python')",
            (str(workspace / "src" / "demo_pkg" / "store.py"),),
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, qualified_name, "
            "line_start, line_end) VALUES "
            "(1, 1, 'resolve_refs_target', 'function', "
            "'demo_pkg.store.resolve_refs_target', 1, 5)",
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, kind, qualified_name, "
            "line_start, line_end) VALUES "
            "(2, 1, 'load', 'function', 'demo_pkg.load', 7, 12)",
        )
        conn.commit()
    finally:
        conn.close()


def _write_doc(workspace, name, text):
    docs = workspace / "docs"
    docs.mkdir(exist_ok=True)
    (docs / name).write_text(text, encoding="utf-8")
    return docs


def _ingest_dir(workspace, docs_dir, outbox="outbox"):
    """Stage + execute one ingest run; returns (manifest, report)."""
    manifest = run_ingest(files=[], dirs=[docs_dir], outbox=workspace / outbox)
    conn = _conn()
    try:
        report = execute_manifest(manifest, conn)
    finally:
        conn.close()
    return manifest, report


def _doc_refs(workspace, doc_id):
    """knowledge_doc_refs rows for one doc, as (ref, ref_kind, verified)."""
    conn = _conn()
    try:
        return [
            (r[0], r[1], r[2])
            for r in conn.execute(
                "SELECT ref, ref_kind, verified FROM knowledge_doc_refs "
                "WHERE doc_id = ?",
                (doc_id,),
            ).fetchall()
        ]
    finally:
        conn.close()


# --- VAL-INGEST-007: verified refs stored, bogus refs dropped ---


class TestVerifiedRefsStored:
    def test_resolvable_refs_reach_promoted_verified_family(self, workspace):
        _seed_graph(workspace)
        docs = _write_doc(workspace, "refs-doc.md", REFS_DOC)
        manifest, _ = _ingest_dir(workspace, docs)
        cid = manifest["rows"][0]["concept_id"]

        concept = _bundle().read_concept(cid)
        assert concept.verified == EXPECTED_VERIFIED

    def test_bogus_ref_appears_nowhere_in_stored_refs(self, workspace):
        _seed_graph(workspace)
        docs = _write_doc(workspace, "refs-doc.md", REFS_DOC)
        manifest, _ = _ingest_dir(workspace, docs)
        cid = manifest["rows"][0]["concept_id"]

        # The body prose may mention the bogus path (stored verbatim);
        # the stored ref families and the index must not carry it.
        concept = _bundle().read_concept(cid)
        for family in (concept.verified, concept.sources):
            assert family is None or all(
                entry.get("ref") != BOGUS_FILE for entry in family
            )
        assert all(entry[0] != BOGUS_FILE for entry in _doc_refs(workspace, cid))

    def test_index_rows_match_the_resolvable_set(self, workspace):
        _seed_graph(workspace)
        docs = _write_doc(workspace, "refs-doc.md", REFS_DOC)
        manifest, report = _ingest_dir(workspace, docs)
        cid = manifest["rows"][0]["concept_id"]

        assert sorted(_doc_refs(workspace, cid)) == sorted(
            (e["ref"], e["kind"], 1) for e in EXPECTED_VERIFIED
        )
        assert report["index_doc_refs"] >= len(EXPECTED_VERIFIED)

    def test_verified_set_equals_resolvable_set(self, workspace):
        _seed_graph(workspace)
        docs = _write_doc(workspace, "refs-doc.md", REFS_DOC)
        manifest, _ = _ingest_dir(workspace, docs)
        cid = manifest["rows"][0]["concept_id"]

        from cairn.refs import file_exists, symbol_exists

        conn = _conn()
        try:
            verified = {
                (e["ref"], e["kind"])
                for e in _bundle().read_concept(cid).verified or []
            }
            resolvable = set()
            for ref in (EXISTING_FILE, BOGUS_FILE):
                if file_exists(conn, ref):
                    resolvable.add((ref, "file"))
            for ref in (f"{EXISTING_SYMBOL}()", QUALIFIED_SYMBOL, "missing_symbol_fn"):
                if symbol_exists(conn, ref):
                    resolvable.add((ref, "symbol"))
        finally:
            conn.close()
        assert verified == resolvable
        assert verified == {(e["ref"], e["kind"]) for e in EXPECTED_VERIFIED}

    def test_report_counts_verified_refs(self, workspace):
        _seed_graph(workspace)
        docs = _write_doc(workspace, "refs-doc.md", REFS_DOC)
        _, report = _ingest_dir(workspace, docs)
        assert report["verified_refs"] == len(EXPECTED_VERIFIED)


class TestRefShapes:
    def test_repo_qualified_file_ref_resolves(self, workspace):
        _seed_graph(workspace)
        text = REFS_DOC.replace(
            f"`{EXISTING_FILE}`", "`fixture/src/demo_pkg/store.py`"
        )
        docs = _write_doc(workspace, "qualified.md", text)
        manifest, _ = _ingest_dir(workspace, docs)
        cid = manifest["rows"][0]["concept_id"]

        concept = _bundle().read_concept(cid)
        file_entries = [e for e in concept.verified or [] if e["kind"] == "file"]
        assert file_entries == [
            {"ref": "fixture/src/demo_pkg/store.py", "kind": "file", "verified": True}
        ]

    def test_duplicate_refs_collapse_to_one_entry(self, workspace):
        _seed_graph(workspace)
        text = (
            "---\n"
            "title: Repeat doc\n"
            "status: accepted\n"
            "---\n"
            f"See `{EXISTING_FILE}` then `{EXISTING_FILE}` again and\n"
            f"`{EXISTING_SYMBOL}` twice: `{EXISTING_SYMBOL}`.\n"
        )
        docs = _write_doc(workspace, "repeat.md", text)
        manifest, _ = _ingest_dir(workspace, docs)
        cid = manifest["rows"][0]["concept_id"]

        assert _bundle().read_concept(cid).verified == [
            {"ref": EXISTING_FILE, "kind": "file", "verified": True},
            {"ref": EXISTING_SYMBOL, "kind": "symbol", "verified": True},
        ]


class TestNoFalseFamilies:
    def test_plain_doc_gains_no_verified_key(self, workspace):
        docs = _write_doc(workspace, "plain.md", PLAIN_DOC)
        manifest, _ = _ingest_dir(workspace, docs)
        cid = manifest["rows"][0]["concept_id"]

        concept = _bundle().read_concept(cid)
        assert concept.verified is None
        assert "verified" not in (_bundle().root / f"{cid}.md").read_text(
            encoding="utf-8"
        )
        assert _doc_refs(workspace, cid) == []

    def test_all_bogus_refs_gain_no_verified_key(self, workspace):
        _seed_graph(workspace)
        docs = _write_doc(workspace, "bogus.md", ALL_BOGUS_DOC)
        manifest, report = _ingest_dir(workspace, docs)
        cid = manifest["rows"][0]["concept_id"]

        concept = _bundle().read_concept(cid)
        assert concept.verified is None
        assert _doc_refs(workspace, cid) == []
        assert report["verified_refs"] == 0


# --- store plumbing: add_document carries the verified family ---


class TestAddDocumentVerifiedRefs:
    def test_verified_refs_parameter_writes_the_family(self, workspace):
        bundle = _bundle()
        cid = add_document(
            bundle,
            title="Manual refs doc",
            body="Body.",
            doc_type="spec",
            verified_refs=[{"ref": EXISTING_FILE, "kind": "file", "verified": True}],
        )
        assert bundle.read_concept(cid).verified == [
            {"ref": EXISTING_FILE, "kind": "file", "verified": True}
        ]

    def test_without_refs_no_verified_family_is_written(self, workspace):
        bundle = _bundle()
        cid = add_document(bundle, title="Manual plain", body="Body.", doc_type="spec")
        assert bundle.read_concept(cid).verified is None


# --- CLI surface: the ingest verify leg reports ref counts ---


class TestCliSurface:
    def test_ingest_output_reports_verified_refs(self, workspace):
        from click.testing import CliRunner

        from cairn.cli.knowledge import knowledge

        _seed_graph(workspace)
        docs = _write_doc(workspace, "refs-doc.md", REFS_DOC)
        result = CliRunner().invoke(
            knowledge, ["ingest", "--dir", str(docs), "--ingest"]
        )
        assert result.exit_code == 0, result.output
        assert f"verified_refs: {len(EXPECTED_VERIFIED)}" in result.stdout
