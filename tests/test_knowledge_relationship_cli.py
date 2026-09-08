"""CLI relationship query surface: `cairn knowledge related` + `chain`.

Covers the validation-contract behaviors: VAL-INGEST-006 (the supersede
chain prints in causal order, every member exactly once, from any entry
point), VAL-INGEST-010 (related lists each neighbor with relation and
kind) and VAL-INGEST-011 (unknown doc ids exit non-zero with a clear
error, no traceback, no partial output, no store writes), across the
store API (``supersede_chain``, ``related_docs``) and the Click layer.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cairn.knowledge.index import rebuild_knowledge_index, related_docs
from cairn.knowledge.store import add_document
from cairn.okf.bundle import OKFBundle
from cairn.okf.concept import OKFConcept
from cairn.paths import resolve_store

GHOST = "knowledge/spec/no-such-doc"

RELATED_KEY = {"doc_id", "relation", "kind", "title", "direction"}
CHAIN_KEY = {"concept_id", "title", "doc_status", "relation"}


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


def _conn():
    from cairn.cli.main import get_db

    resolve_store().ensure()
    return get_db()


def _rebuild():
    """One rebuild on a fresh connection (committed)."""
    conn = _conn()
    try:
        report = rebuild_knowledge_index(conn, _bundle())
        conn.commit()
        return report
    finally:
        conn.close()


def _db_arg(workspace):
    return str(workspace / "cairn-home" / ".kg")


def _add_doc(bundle, title, *, tags=None, modules=None, relationships=None):
    return add_document(
        bundle,
        title=title,
        body=f"Body of {title}.",
        doc_type="spec",
        tags=tags or [],
        affects_modules=modules or [],
        relationships=relationships,
    )


def _declare(bundle, cid, other, relation):
    """Append one relationship entry to a doc's frontmatter half."""
    concept = bundle.read_concept(cid)
    concept.extensions.setdefault("relates_to", []).append(
        {"concept_id": other, "relation": relation, "kind": "extracted"}
    )
    bundle.write_concept(concept)


def _supersede_corpus(bundle):
    """ADR 0001 -> 0002 -> 0003, both frontmatter halves per pair (the
    memory ``_mark_superseded`` pattern). Returns bare ids oldest-first."""
    titles = ["Use Postgres", "Use CockroachDB", "Use Distributed SQL"]
    ids = []
    for i, title in enumerate(titles):
        rels = (
            [{"concept_id": ids[-1], "relation": "supersedes", "kind": "extracted"}]
            if ids
            else None
        )
        ids.append(_add_doc(bundle, title, tags=["adr"], relationships=rels))
    for older, newer in zip(ids, ids[1:]):
        _declare(bundle, older, newer, "superseded-by")
        _declare(bundle, newer, older, "supersedes")
    return ids


def _supersede_corpus_with_neighbor(bundle):
    """The 3-doc chain plus a doc sharing the chain tag, so `related` sees
    a kind=derived neighbor next to the extracted supersede rows."""
    ids = _supersede_corpus(bundle)
    neighbor = _add_doc(bundle, "Related runbook", tags=["adr"], modules=["src/ops"])
    return ids, neighbor


def _snapshot_tree(root: Path):
    """Every file under root as {relpath: (size, mtime_ns)}."""
    snap = {}
    for p in sorted(Path(root).rglob("*")):
        if p.is_file():
            st = p.stat()
            snap[str(p.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return snap


def _index_counts(conn):
    row = tuple(
        conn.execute(
            "SELECT (SELECT COUNT(*) FROM knowledge_edges), "
            "(SELECT COUNT(*) FROM knowledge_doc_refs)"
        ).fetchone()
    )
    return row


# --- supersede_chain store API: causal order from any entry point ---


class TestSupersedeChain:
    def _chain(self, doc_id):
        conn = _conn()
        from cairn.knowledge.index import supersede_chain

        try:
            return supersede_chain(conn, _bundle(), doc_id)
        finally:
            conn.close()

    def test_chain_from_oldest_is_causal_order(self, workspace):
        ids = _supersede_corpus(_bundle())
        _rebuild()
        assert [m["concept_id"] for m in self._chain(ids[0])] == ids

    def test_chain_from_middle_matches_oldest_entry(self, workspace):
        ids = _supersede_corpus(_bundle())
        _rebuild()
        assert [m["concept_id"] for m in self._chain(ids[1])] == ids

    def test_chain_from_newest_matches_oldest_entry(self, workspace):
        ids = _supersede_corpus(_bundle())
        _rebuild()
        assert [m["concept_id"] for m in self._chain(ids[2])] == ids

    def test_members_carry_title_status_and_relation_to_next(self, workspace):
        ids = _supersede_corpus(_bundle())
        _rebuild()
        chain = self._chain(ids[0])
        assert all(CHAIN_KEY <= set(m) for m in chain)
        assert [m["title"] for m in chain] == [
            "Use Postgres",
            "Use CockroachDB",
            "Use Distributed SQL",
        ]
        assert [m["doc_status"] for m in chain] == ["active"] * 3
        assert [m["relation"] for m in chain] == [
            "superseded-by",
            "superseded-by",
            None,
        ]

    def test_derived_edges_never_enter_the_chain(self, workspace):
        ids, _neighbor = _supersede_corpus_with_neighbor(_bundle())
        _rebuild()
        assert [m["concept_id"] for m in self._chain(ids[0])] == ids

    def test_unlinked_doc_is_a_chain_of_one(self, workspace):
        cid = _add_doc(_bundle(), "Lone doc")
        _rebuild()
        chain = self._chain(cid)
        assert [m["concept_id"] for m in chain] == [cid]
        assert chain[0]["relation"] is None

    def test_cycle_terminates_with_each_member_once(self, workspace):
        bundle = _bundle()
        cid_a = _add_doc(bundle, "Cycle alpha")
        cid_b = _add_doc(bundle, "Cycle beta")
        for older, newer in ((cid_a, cid_b), (cid_b, cid_a)):
            _declare(bundle, older, newer, "superseded-by")
            _declare(bundle, newer, older, "supersedes")
        _rebuild()
        chain = self._chain(cid_a)
        found = [m["concept_id"] for m in chain]
        assert len(found) == 2
        assert set(found) == {cid_a, cid_b}

    def test_dangling_edge_is_skipped_between_rebuilds(self, workspace):
        bundle = _bundle()
        ids = _supersede_corpus(bundle)
        _rebuild()
        # The newest doc is deleted but the index is NOT rebuilt: stale
        # supersede rows still name it, and the walk must skip them.
        (Path(bundle.root) / f"{ids[2]}.md").unlink()
        assert [m["concept_id"] for m in self._chain(ids[0])] == ids[:2]

    def test_unknown_doc_raises_value_error(self, workspace):
        _rebuild()
        with pytest.raises(ValueError, match="Unknown knowledge document"):
            self._chain(GHOST)

    def test_branched_dag_asserts_start_doc_membership(self, workspace):
        """Supersede chains are linear by writer contract (one successor
        per superseded doc). A branched DAG is outside that contract: the
        walk follows the sorted-first successor per fork, so the queried
        doc dropping out of its own chain must fail loudly, not silently
        return a chain without it. Titles pick ids so the non-start branch
        sorts first (the walk would otherwise still pass through start)."""
        bundle = _bundle()
        base = _add_doc(bundle, "Fork base", tags=["adr"])
        start = _add_doc(bundle, "Fork zulu")
        other = _add_doc(bundle, "Fork alpha")
        for newer in (start, other):
            _declare(bundle, newer, base, "supersedes")
            _declare(bundle, base, newer, "superseded-by")
        _rebuild()
        with pytest.raises(AssertionError):
            self._chain(start)


# --- related_docs store API: neighbors with relation, kind, title ---


class TestRelatedDocs:
    def test_both_directions_carry_relation_kind_and_title(self, workspace):
        bundle = _bundle()
        ids, neighbor = _supersede_corpus_with_neighbor(bundle)
        _rebuild()
        conn = _conn()
        try:
            neighbors = related_docs(conn, bundle, ids[0])
        finally:
            conn.close()
        found = {
            (n["doc_id"], n["relation"], n["kind"], n["direction"])
            for n in neighbors
        }
        assert found == {
            (ids[1], "superseded-by", "extracted", "outgoing"),
            (ids[1], "supersedes", "extracted", "incoming"),
            (ids[2], "relates-to", "derived", "outgoing"),
            (ids[2], "relates-to", "derived", "incoming"),
            (neighbor, "relates-to", "derived", "outgoing"),
            (neighbor, "relates-to", "derived", "incoming"),
        }
        titles = {n["doc_id"]: n["title"] for n in neighbors}
        assert titles[ids[1]] == "Use CockroachDB"
        assert titles[neighbor] == "Related runbook"

    def test_unknown_doc_yields_no_neighbors(self, workspace):
        _rebuild()
        conn = _conn()
        try:
            assert related_docs(conn, _bundle(), GHOST) == []
        finally:
            conn.close()


# --- CLI: `cairn knowledge related` (VAL-INGEST-010) ---


class TestRelatedCli:
    def test_every_neighbor_lists_relation_and_kind(self, workspace):
        bundle = _bundle()
        ids, neighbor = _supersede_corpus_with_neighbor(bundle)
        _rebuild()
        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        result = CliRunner().invoke(
            knowledge, ["related", ids[0], "--db", _db_arg(workspace)]
        )
        assert result.exit_code == 0, result.stdout
        # One row per neighbor edge, each carrying relation AND kind.
        assert "supersedes (extracted)" in result.stdout
        assert "superseded-by (extracted)" in result.stdout
        assert "relates-to (derived)" in result.stdout
        for cid in (ids[1], ids[2], neighbor):
            assert cid in result.stdout

    def test_json_rows_carry_the_contract_fields(self, workspace):
        bundle = _bundle()
        ids, _neighbor = _supersede_corpus_with_neighbor(bundle)
        _rebuild()
        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        result = CliRunner().invoke(
            knowledge, ["related", ids[0], "--json", "--db", _db_arg(workspace)]
        )
        assert result.exit_code == 0, result.stdout
        rows = json.loads(result.stdout)
        assert all(RELATED_KEY <= set(row) for row in rows)
        assert any(
            row["doc_id"] == ids[1] and row["relation"] == "supersedes"
            and row["kind"] == "extracted"
            for row in rows
        )

    def test_doc_without_edges_prints_a_clean_empty_result(self, workspace):
        cid = _add_doc(_bundle(), "Lone doc")
        _rebuild()
        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        result = CliRunner().invoke(
            knowledge, ["related", cid, "--db", _db_arg(workspace)]
        )
        assert result.exit_code == 0, result.stdout
        assert "No stored relationships" in result.stdout


# --- CLI: `cairn knowledge chain` (VAL-INGEST-006) ---


class TestChainCli:
    def test_chain_is_ordered_from_any_member_as_argument(self, workspace):
        bundle = _bundle()
        ids = _supersede_corpus(bundle)
        _rebuild()
        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        for entry in ids:
            result = CliRunner().invoke(
                knowledge, ["chain", entry, "--db", _db_arg(workspace)]
            )
            assert result.exit_code == 0, result.stdout
            # Positions and order: every member exactly once, oldest first.
            assert f"1. {ids[0]}" in result.stdout
            assert f"2. {ids[1]}" in result.stdout
            assert f"3. {ids[2]}" in result.stdout
            markers = [result.stdout.index(f"{i}. {cid}") for i, cid in enumerate(ids, 1)]
            assert markers == sorted(markers)
            # Relations between consecutive members are shown.
            assert "-> superseded-by" in result.stdout
            assert result.stdout.count("superseded-by") == 2

    def test_json_chain_is_ordered_with_positions_implied(self, workspace):
        bundle = _bundle()
        ids = _supersede_corpus(bundle)
        _rebuild()
        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        result = CliRunner().invoke(
            knowledge, ["chain", ids[2], "--json", "--db", _db_arg(workspace)]
        )
        assert result.exit_code == 0, result.stdout
        chain = json.loads(result.stdout)
        assert [m["concept_id"] for m in chain] == ids
        assert all(CHAIN_KEY <= set(m) for m in chain)
        assert chain[-1]["relation"] is None

    def test_single_doc_chain_prints_one_position(self, workspace):
        cid = _add_doc(_bundle(), "Lone doc")
        _rebuild()
        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        result = CliRunner().invoke(
            knowledge, ["chain", cid, "--db", _db_arg(workspace)]
        )
        assert result.exit_code == 0, result.stdout
        assert f"1. {cid}" in result.stdout


# --- CLI: unknown ids fail clean with zero writes (VAL-INGEST-011) ---


class TestUnknownDocErrors:
    def test_unknown_id_is_a_clean_error_with_no_store_writes(self, workspace):
        bundle = _bundle()
        _supersede_corpus_with_neighbor(bundle)
        _rebuild()
        bundle_root = Path(bundle.root)
        before = _snapshot_tree(bundle_root)
        conn = _conn()
        try:
            counts_before = _index_counts(conn)
        finally:
            conn.close()

        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        for verb in ("related", "chain"):
            result = CliRunner().invoke(
                knowledge, [verb, GHOST, "--db", _db_arg(workspace)]
            )
            assert result.exit_code == 1, result.stdout
            assert "Error:" in result.stderr
            assert GHOST in result.stderr
            assert "Traceback" not in result.stderr
            assert "Traceback" not in result.stdout
            # No partial output: the error is the whole story.
            assert result.stdout == ""

        assert _snapshot_tree(bundle_root) == before
        conn = _conn()
        try:
            assert _index_counts(conn) == counts_before
        finally:
            conn.close()

    def test_unknown_id_on_uninitialized_store_writes_nothing(self, workspace):
        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        db_path = workspace / "cairn-home" / ".kg"
        for verb in ("related", "chain"):
            result = CliRunner().invoke(
                knowledge, [verb, GHOST, "--db", str(db_path)]
            )
            assert result.exit_code == 1, result.stdout
            assert "Error:" in result.stderr
            assert GHOST in result.stderr
            assert "Traceback" not in result.stderr
        # Neither the bundle nor the DB was created by the failed lookups.
        assert not resolve_store().knowledge.exists()
        assert not db_path.exists()

    def test_out_of_namespace_id_is_refused(self, workspace):
        bundle = _bundle()
        bundle.write_concept(
            OKFConcept(
                type="Wiki",
                title="Wiki page",
                description="wiki",
                tags=[],
                concept_id="wiki/page",
                body="body",
            )
        )
        _rebuild()
        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        for verb in ("related", "chain"):
            result = CliRunner().invoke(
                knowledge, [verb, "wiki/page", "--db", _db_arg(workspace)]
            )
            assert result.exit_code == 1, result.stdout
            assert "outside the knowledge/ namespace" in result.stderr
            assert "Traceback" not in result.stderr
