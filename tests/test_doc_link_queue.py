"""Island detection + LLM doc linking behind the task queue (D1).

Covers the validation-contract behaviors: VAL-INGEST-013 (an isolated doc
pair queues exactly one pending doc-link task carrying the member
concept_ids; connected docs queue none), VAL-INGEST-014 (a completion
proposing edges between EXISTING concept_ids passes the deterministic
critic and writes kind=inferred relates_to entries to both docs'
frontmatter plus the index) and VAL-INGEST-015 (a completion referencing
a nonexistent concept_id is rejected with no writes, the task left
in-progress and re-completable, the rejection naming the invalid
reference).
"""
from __future__ import annotations

import pytest

from cairn.knowledge.store import add_document
from cairn.llm.tasks import get_task
from cairn.okf.bundle import OKFBundle
from cairn.paths import resolve_store

GHOST = "knowledge/spec/no-such-doc"


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
    from cairn.knowledge.index import rebuild_knowledge_index

    conn = _conn()
    try:
        report = rebuild_knowledge_index(conn, _bundle())
        conn.commit()
        return report
    finally:
        conn.close()


def _add_doc(bundle, title, *, tags=None, modules=None):
    return add_document(
        bundle,
        title=title,
        body=f"Body of {title}.",
        doc_type="spec",
        tags=tags or [],
        affects_modules=modules or [],
    )


def _corpus(bundle):
    """Three docs linked by a shared tag: the connected mainland (a
    2-doc corpus would itself qualify as an island pair)."""
    return [
        _add_doc(bundle, "Corpus one", tags=["corp"]),
        _add_doc(bundle, "Corpus two", tags=["corp"]),
        _add_doc(bundle, "Corpus three", tags=["corp"]),
    ]


def _doc_link_tasks(bundle, status=None):
    from cairn.llm.tasks import list_tasks
    from cairn.knowledge.islands import DOC_LINK_KIND

    return [
        t
        for t in list_tasks(bundle, status=status, kind_prefix=DOC_LINK_KIND)
        if t.task_kind == DOC_LINK_KIND
    ]


def _queue():
    conn = _conn()
    try:
        from cairn.knowledge.islands import queue_doc_link_tasks

        return queue_doc_link_tasks(conn, _bundle())
    finally:
        conn.close()


def _islands():
    conn = _conn()
    try:
        from cairn.knowledge.islands import doc_islands

        return doc_islands(conn, _bundle())
    finally:
        conn.close()


def _claim_and_complete(bundle, task_id, result_text):
    from cairn.llm.tasks import claim_task

    claimed = claim_task(bundle, task_id)
    assert claimed is not None, "task must be claimable"
    return _complete(bundle, task_id, result_text)


def _complete(bundle, task_id, result_text):
    """Complete an already-claimed (in-progress) task."""
    from cairn.llm.tasks import complete_task

    conn = _conn()
    try:
        return complete_task(bundle, task_id, result_text, conn=conn)
    finally:
        conn.close()


def _relates_to(bundle, cid):
    concept = bundle.read_concept(cid)
    return concept.extensions.get("relates_to") or []


def _inferred_edges(conn):
    return sorted(
        tuple(r)
        for r in conn.execute(
            "SELECT doc_id, related_id, relation, kind, provenance "
            "FROM knowledge_edges WHERE kind = 'inferred'"
        ).fetchall()
    )


def _db_arg(workspace):
    return str(workspace / "cairn-home" / ".kg")


def _kn_arg(workspace):
    return str(workspace / "cairn-home" / ".knowledge")


# --- VAL-INGEST-013: island detection queues doc-link tasks ---


class TestIslandDetection:
    def test_isolated_pair_sharing_a_tag_queues_exactly_one_task(
        self, workspace
    ):
        bundle = _bundle()
        _corpus(bundle)
        # The pair overlaps with each other (shared tag) but with nothing
        # else: one two-doc component, detached from the corpus.
        a = _add_doc(bundle, "Island alpha", tags=["pair-island"])
        b = _add_doc(bundle, "Island beta", tags=["pair-island"])
        _rebuild()

        assert _queue() == 1
        pending = _doc_link_tasks(bundle, status="pending")
        assert len(pending) == 1
        task = pending[0]
        assert task.facts["members"] == sorted([a, b])
        assert set(task.facts["member_titles"]) == {a, b}

    def test_fully_disconnected_pair_queues_one_task_with_both_ids(
        self, workspace
    ):
        bundle = _bundle()
        _corpus(bundle)
        # No shared tags anywhere: two singletons, paired by detection.
        a = _add_doc(bundle, "Lone alpha", tags=["solox"])
        b = _add_doc(bundle, "Lone beta", tags=["soloy"])
        _rebuild()

        assert _queue() == 1
        pending = _doc_link_tasks(bundle, status="pending")
        assert len(pending) == 1
        assert pending[0].facts["members"] == sorted([a, b])

    def test_connected_corpus_queues_nothing(self, workspace):
        bundle = _bundle()
        _corpus(bundle)
        _rebuild()
        assert _queue() == 0
        assert _doc_link_tasks(bundle) == []

    def test_queueing_is_idempotent_across_runs(self, workspace):
        bundle = _bundle()
        _corpus(bundle)
        _add_doc(bundle, "Island alpha", tags=["pair-island"])
        _add_doc(bundle, "Island beta", tags=["pair-island"])
        _rebuild()
        assert _queue() == 1
        assert _queue() == 0
        assert len(_doc_link_tasks(bundle, status="pending")) == 1

    def test_completed_task_still_blocks_requeueing(self, workspace):
        bundle = _bundle()
        _corpus(bundle)
        a = _add_doc(bundle, "Island alpha", tags=["pair-island"])
        b = _add_doc(bundle, "Island beta", tags=["pair-island"])
        _rebuild()
        _queue()
        task = _doc_link_tasks(bundle, status="pending")[0]
        outcome = _claim_and_complete(
            bundle, task.id, f"{a} relates-to {b}\n"
        )
        assert outcome["promoted"] is True
        # The island still exists (the docs are still detached from the
        # corpus), but its member set already has a doc-link task.
        assert _queue() == 0
        assert len(_doc_link_tasks(bundle)) == 1

    def test_island_shapes_two_doc_component_plus_paired_singletons(
        self, workspace
    ):
        bundle = _bundle()
        a = _add_doc(bundle, "Comp alpha", tags=["comp"])
        b = _add_doc(bundle, "Comp beta", tags=["comp"])
        s1 = _add_doc(bundle, "Solo alpha", tags=["u1"])
        s2 = _add_doc(bundle, "Solo beta", tags=["u2"])
        _add_doc(bundle, "Solo gamma", tags=["u3"])  # odd one: unpaired
        _rebuild()
        islands = _islands()
        assert sorted(sorted(members) for members in islands) == sorted(
            [sorted([a, b]), sorted([s1, s2])]
        )

    def test_execute_manifest_queues_island_tasks(self, workspace):
        from cairn.knowledge.ingest import run_ingest
        from cairn.knowledge.ingest.executor import execute_manifest

        # The mainland: seeded directly (scanned docs would share the
        # scan-root auto-tag, which would connect the whole store).
        _corpus(_bundle())
        _rebuild()

        # The island pair: staged via ingest, in separate directories so
        # their affects_modules never overlap the corpus.
        docs = workspace / "staged"
        (docs / "iso-a").mkdir(parents=True)
        (docs / "iso-b").mkdir(parents=True)
        (docs / "iso-a" / "iso-x.md").write_text(
            "---\ntitle: Iso x\nstatus: accepted\ntags: [isox]\n---\nBody.\n",
            encoding="utf-8",
        )
        (docs / "iso-b" / "iso-y.md").write_text(
            "---\ntitle: Iso y\nstatus: accepted\ntags: [isoy]\n---\nBody.\n",
            encoding="utf-8",
        )
        manifest = run_ingest(files=[], dirs=[str(docs)], outbox="outbox")
        conn = _conn()
        try:
            report = execute_manifest(manifest, conn)
        finally:
            conn.close()

        written = set(report["written"])
        iso = {w for w in written if "iso" in w}
        assert len(iso) == 2
        assert report["doc_link_tasks"] == 1
        pending = _doc_link_tasks(_bundle(), status="pending")
        assert len(pending) == 1
        assert sorted(pending[0].facts["members"]) == sorted(iso)


# --- VAL-INGEST-014: valid completion writes inferred edges ---


class TestDocLinkCompletion:
    def _island_task(self, bundle, *, shared_tag=True):
        _corpus(bundle)
        tag = ["pair-island"] if shared_tag else None
        a = _add_doc(
            bundle, "Island alpha", tags=tag if shared_tag else ["solox"]
        )
        b = _add_doc(
            bundle, "Island beta", tags=tag if shared_tag else ["soloy"]
        )
        _rebuild()
        _queue()
        return a, b, _doc_link_tasks(bundle, status="pending")[0]

    def test_valid_completion_writes_inferred_edges_to_both_docs_and_index(
        self, workspace
    ):
        bundle = _bundle()
        a, b, task = self._island_task(bundle)

        outcome = _claim_and_complete(bundle, task.id, f"{a} relates-to {b}\n")

        assert outcome["promoted"] is True
        assert outcome["dropped"] is False
        assert outcome["revised"] is False
        assert outcome["errors"] == []
        # Task is terminal-done.
        assert get_task(bundle, task.id).status == "done"

        # Both docs' frontmatter gained kind=inferred relates_to entries.
        assert {"concept_id": b, "relation": "relates-to", "kind": "inferred"} in _relates_to(
            bundle, a
        )
        assert {"concept_id": a, "relation": "relates-to", "kind": "inferred"} in _relates_to(
            bundle, b
        )

        # The index was refreshed: the inferred edge is queryable now.
        conn = _conn()
        try:
            assert (a, b, "relates-to", "inferred") in {
                tuple(r)[:4] for r in _inferred_edges(conn)
            }
            assert (b, a, "relates-to", "inferred") in {
                tuple(r)[:4] for r in _inferred_edges(conn)
            }
        finally:
            conn.close()

        # ...and the relationship CLI surface sees it.
        from cairn.knowledge.index import related_docs

        conn = _conn()
        try:
            neighbors = related_docs(conn, bundle, a)
        finally:
            conn.close()
        assert any(
            n["doc_id"] == b and n["relation"] == "relates-to"
            and n["kind"] == "inferred"
            for n in neighbors
        )

    def test_supersedes_proposal_writes_the_mirror_entry(self, workspace):
        bundle = _bundle()
        a, b, task = self._island_task(bundle, shared_tag=False)

        outcome = _claim_and_complete(
            bundle, task.id, f"{b} supersedes {a}\n"
        )

        assert outcome["promoted"] is True
        # New doc's half: supersedes inferred...
        assert {"concept_id": a, "relation": "supersedes", "kind": "inferred"} in _relates_to(
            bundle, b
        )
        # ...old doc's half: the mirrored superseded-by, also inferred.
        assert {"concept_id": b, "relation": "superseded-by", "kind": "inferred"} in _relates_to(
            bundle, a
        )
        conn = _conn()
        try:
            keys = {tuple(r)[:4] for r in _inferred_edges(conn)}
        finally:
            conn.close()
        assert (b, a, "supersedes", "inferred") in keys
        assert (a, b, "superseded-by", "inferred") in keys

    def test_explicit_entry_wins_over_duplicate_inferred(self, workspace):
        bundle = _bundle()
        a, b, task = self._island_task(bundle)

        # An author-declared entry for the same pair already exists.
        concept = bundle.read_concept(a)
        concept.extensions["relates_to"] = [
            {"concept_id": b, "relation": "relates-to", "kind": "extracted"}
        ]
        bundle.write_concept(concept)

        outcome = _claim_and_complete(bundle, task.id, f"{a} relates-to {b}\n")
        assert outcome["promoted"] is True
        entries = [
            e
            for e in _relates_to(bundle, a)
            if e["concept_id"] == b and e["relation"] == "relates-to"
        ]
        assert len(entries) == 1
        assert entries[0]["kind"] == "extracted"

    def test_backticked_and_bulleted_result_lines_parse(self, workspace):
        from cairn.knowledge.doc_link import parse_doc_link_result

        edges, errors = parse_doc_link_result(
            "## Proposed edges\n"
            "- `knowledge/spec/a` relates-to `knowledge/spec/b`\n"
            "* knowledge/spec/c supersedes knowledge/spec/a\n"
            "knowledge/spec/b references knowledge/spec/c\n"
        )
        assert errors == []
        assert edges == [
            ("knowledge/spec/a", "relates-to", "knowledge/spec/b"),
            ("knowledge/spec/c", "supersedes", "knowledge/spec/a"),
            ("knowledge/spec/b", "references", "knowledge/spec/c"),
        ]

    def test_output_spec_pins_the_line_format(self):
        from cairn.llm.tasks import _output_spec

        spec = _output_spec("doc-link", {"members": ["knowledge/spec/a"]})
        assert "<concept_id> <relation> <related_id>" in spec
        assert "relates-to | supersedes | superseded-by | references" in spec


# --- VAL-INGEST-015: bogus completions rejected, no writes ---


class TestDocLinkRejection:
    def _island_task(self, bundle):
        _corpus(bundle)
        a = _add_doc(bundle, "Island alpha", tags=["pair-island"])
        b = _add_doc(bundle, "Island beta", tags=["pair-island"])
        _rebuild()
        _queue()
        return a, b, _doc_link_tasks(bundle, status="pending")[0]

    def _index_has_no_inferred_edges(self):
        conn = _conn()
        try:
            assert _inferred_edges(conn) == []
        finally:
            conn.close()

    def test_unknown_target_rejected_naming_the_reference(self, workspace):
        bundle = _bundle()
        a, b, task = self._island_task(bundle)
        before_a, before_b = _relates_to(bundle, a), _relates_to(bundle, b)

        outcome = _claim_and_complete(
            bundle, task.id, f"{a} relates-to {GHOST}\n"
        )

        assert outcome["promoted"] is False
        assert outcome["revised"] is False
        assert outcome["dropped"] is False
        assert any(GHOST in e for e in outcome["errors"])
        # The task was NOT promoted: still in-progress and re-completable.
        assert get_task(bundle, task.id).status == "in-progress"
        # No frontmatter writes, no index edge.
        assert _relates_to(bundle, a) == before_a
        assert _relates_to(bundle, b) == before_b
        self._index_has_no_inferred_edges()

        # The same task completes successfully once the reference is fixed
        # (no re-claim needed: the refusal left it claimed and in-progress).
        retry = _complete(bundle, task.id, f"{a} relates-to {b}\n")
        assert retry["promoted"] is True

    def test_unknown_source_rejected_too(self, workspace):
        bundle = _bundle()
        a, b, task = self._island_task(bundle)

        outcome = _claim_and_complete(
            bundle, task.id, f"{GHOST} relates-to {b}\n"
        )

        assert outcome["promoted"] is False
        assert any(GHOST in e for e in outcome["errors"])
        assert get_task(bundle, task.id).status == "in-progress"
        self._index_has_no_inferred_edges()

    def test_invalid_relation_rejected_naming_it(self, workspace):
        bundle = _bundle()
        a, b, task = self._island_task(bundle)

        outcome = _claim_and_complete(
            bundle, task.id, f"{a} linked-to {b}\n"
        )

        assert outcome["promoted"] is False
        assert any("linked-to" in e for e in outcome["errors"])
        assert get_task(bundle, task.id).status == "in-progress"
        self._index_has_no_inferred_edges()

    def test_malformed_line_rejected(self, workspace):
        bundle = _bundle()
        a, b, task = self._island_task(bundle)

        outcome = _claim_and_complete(
            bundle, task.id, f"{a} relates-to\n"
        )

        assert outcome["promoted"] is False
        assert outcome["errors"]
        assert get_task(bundle, task.id).status == "in-progress"
        self._index_has_no_inferred_edges()

    def test_self_edge_rejected(self, workspace):
        bundle = _bundle()
        a, b, task = self._island_task(bundle)

        outcome = _claim_and_complete(
            bundle, task.id, f"{a} relates-to {a}\n"
        )

        assert outcome["promoted"] is False
        assert any("self-referential" in e for e in outcome["errors"])
        self._index_has_no_inferred_edges()

    def test_zero_edge_result_rejected_and_task_stays_recompletable(
        self, workspace
    ):
        """An empty result must not promote vacuously: a promoted zero-edge
        completion would mark the task done without writing an edge, and
        the any-status queue dedup would never re-queue the island."""
        bundle = _bundle()
        a, b, task = self._island_task(bundle)

        outcome = _claim_and_complete(bundle, task.id, "")

        assert outcome["promoted"] is False
        assert outcome["revised"] is False
        assert outcome["dropped"] is False
        assert "no proposed edges found" in outcome["errors"]
        assert get_task(bundle, task.id).status == "in-progress"
        assert _relates_to(bundle, a) == []
        assert _relates_to(bundle, b) == []
        self._index_has_no_inferred_edges()

        # The island is recoverable: the same task completes with a real
        # proposal (still claimed, no re-claim needed).
        retry = _complete(bundle, task.id, f"{a} relates-to {b}\n")
        assert retry["promoted"] is True

    def test_heading_only_result_rejected_too(self, workspace):
        bundle = _bundle()
        a, b, task = self._island_task(bundle)

        outcome = _claim_and_complete(
            bundle, task.id, "## Proposed edges\n\n```\n```\n"
        )

        assert outcome["promoted"] is False
        assert "no proposed edges found" in outcome["errors"]
        assert get_task(bundle, task.id).status == "in-progress"
        self._index_has_no_inferred_edges()

    def test_completion_without_connection_refused(self, workspace):
        bundle = _bundle()
        a, b, task = self._island_task(bundle)
        from cairn.llm.tasks import claim_task, complete_task

        assert claim_task(bundle, task.id) is not None
        outcome = complete_task(
            bundle, task.id, f"{a} relates-to {b}\n", conn=None
        )

        assert outcome["promoted"] is False
        assert outcome["dropped"] is False
        assert outcome["errors"]
        assert get_task(bundle, task.id).status == "in-progress"
        assert _relates_to(bundle, a) == []


# --- CLI end-to-end: the exact surfaces the validation contract uses ---


class TestDocLinkCli:
    def _setup_island(self, bundle):
        _corpus(bundle)
        a = _add_doc(bundle, "Island alpha", tags=["pair-island"])
        b = _add_doc(bundle, "Island beta", tags=["pair-island"])
        _rebuild()
        _queue()
        return a, b, _doc_link_tasks(bundle, status="pending")[0]

    def _task_cli(self, workspace, args):
        from click.testing import CliRunner
        from cairn.cli.task import task

        full = list(args) + ["--knowledge", _kn_arg(workspace)]
        if args and args[0] == "complete":
            full += ["--db", _db_arg(workspace)]
        return CliRunner().invoke(task, full)

    def test_pending_task_visible_in_task_list_and_show(self, workspace):
        bundle = _bundle()
        a, b, task = self._setup_island(bundle)

        listed = self._task_cli(workspace, ["list", "--status", "pending"])
        assert listed.exit_code == 0, listed.stdout
        assert "doc-link" in listed.stdout
        assert task.id in listed.stdout

        shown = self._task_cli(workspace, ["show", task.id])
        assert shown.exit_code == 0, shown.stdout
        assert "members" in shown.stdout
        assert a in shown.stdout
        assert b in shown.stdout

    def test_full_claim_complete_flow_promotes_the_edge(self, workspace):
        bundle = _bundle()
        a, b, task = self._setup_island(bundle)

        claimed = self._task_cli(workspace, ["claim", task.id])
        assert claimed.exit_code == 0, claimed.stdout

        result_file = workspace / "result.md"
        result_file.write_text(f"{a} relates-to {b}\n", encoding="utf-8")
        completed = self._task_cli(
            workspace,
            ["complete", task.id, "--result-file", str(result_file)],
        )
        assert completed.exit_code == 0, completed.stdout
        assert "promoted" in completed.stdout

        assert {"concept_id": b, "relation": "relates-to", "kind": "inferred"} in _relates_to(
            bundle, a
        )
        assert {"concept_id": a, "relation": "relates-to", "kind": "inferred"} in _relates_to(
            bundle, b
        )

        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        related = CliRunner().invoke(
            knowledge, ["related", a, "--db", _db_arg(workspace)]
        )
        assert related.exit_code == 0, related.stdout
        assert "relates-to (inferred)" in related.stdout
        assert b in related.stdout

    def test_bogus_result_file_rejected_by_cli(self, workspace):
        bundle = _bundle()
        a, b, task = self._setup_island(bundle)

        claimed = self._task_cli(workspace, ["claim", task.id])
        assert claimed.exit_code == 0, claimed.stdout

        result_file = workspace / "bogus.md"
        result_file.write_text(f"{a} relates-to {GHOST}\n", encoding="utf-8")
        completed = self._task_cli(
            workspace,
            ["complete", task.id, "--result-file", str(result_file)],
        )
        assert completed.exit_code == 1
        assert GHOST in completed.stderr
        assert "not completed" in completed.stderr
        assert "Traceback" not in completed.stderr

        # Task state reflects non-promotion...
        shown = self._task_cli(workspace, ["show", task.id])
        assert shown.exit_code == 0, shown.stdout
        assert "status: in-progress" in shown.stdout
        # ...and nothing was written anywhere.
        assert _relates_to(bundle, a) == []
        self._assert_no_inferred_edges()

    def _assert_no_inferred_edges(self):
        conn = _conn()
        try:
            assert _inferred_edges(conn) == []
        finally:
            conn.close()

    def test_islands_cli_lists_islands_and_task_state(self, workspace):
        bundle = _bundle()
        a, b, task = self._setup_island(bundle)

        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        result = CliRunner().invoke(
            knowledge, ["islands", "--db", _db_arg(workspace)]
        )
        assert result.exit_code == 0, result.stdout
        assert a in result.stdout
        assert b in result.stdout
        assert task.id in result.stdout

    def test_rebuild_cli_queues_island_tasks(self, workspace):
        bundle = _bundle()
        a = _add_doc(bundle, "Island alpha", tags=["pair-island"])
        b = _add_doc(bundle, "Island beta", tags=["pair-island"])

        from click.testing import CliRunner
        from cairn.cli.knowledge import knowledge

        result = CliRunner().invoke(
            knowledge, ["rebuild", "--db", _db_arg(workspace)]
        )
        assert result.exit_code == 0, result.stdout
        assert "queued 1 doc-link task(s)" in result.stdout
        pending = _doc_link_tasks(bundle, status="pending")
        assert len(pending) == 1
        assert sorted(pending[0].facts["members"]) == sorted([a, b])

        # Rebuilds are deduped: no second task for the same island.
        again = CliRunner().invoke(
            knowledge, ["rebuild", "--db", _db_arg(workspace)]
        )
        assert again.exit_code == 0, again.stdout
        assert "queued 0 doc-link task(s)" in again.stdout
        assert len(_doc_link_tasks(bundle)) == 1
