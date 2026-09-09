"""Approved-run execution: manifest rows -> store -> embeddings (D-003)."""
from __future__ import annotations

from cairn.knowledge.ingest.refs import resolve_doc_refs
from cairn.knowledge.store import add_document
from cairn.okf.bundle import OKFBundle
from cairn.paths import resolve_store


def execute_manifest(manifest: dict, conn) -> dict:
    """Write every accepted manifest row via add_document, then embed.

    The approval gate lives with the caller (the ``--ingest`` CLI flag);
    this function executes an already-approved manifest. Rows are written
    in sorted (repo, relpath) order so re-runs are deterministic.
    ``conn`` is an open graph database connection (the caller owns it);
    embedding runs only when the semantic backend is installed.

    Doc-to-code refs (D1.3) resolve here rather than at staging: the
    body's backticked tokens are verified against this connection (the
    wiki verified-sources pattern), and only resolvable ones reach the
    promoted concept's ``verified`` family -- and from there the
    ``knowledge_doc_refs`` index via the post-write rebuild.
    """
    from cairn.graph import embeddings as emb

    store = resolve_store()
    store.ensure()
    bundle = OKFBundle(str(store.knowledge))

    accepted = [row for row in manifest.get("rows", []) if "skip" not in row]
    # Pre-write state for the count verify leg and the inferred-link merge:
    # add_document overwrites in place, so the post-write store holds the
    # pre-existing docs not rewritten by this batch plus the accepted rows.
    pre_ids = _pre_existing_docs(bundle)
    written: list[str] = []
    overwritten = 0
    verified_refs_total = 0
    for row in sorted(accepted, key=lambda r: (r.get("repo", ""), r.get("source_path", ""))):
        if row["concept_id"] in pre_ids:
            overwritten += 1
        verified_refs = resolve_doc_refs(conn, row["body"])
        verified_refs_total += len(verified_refs)
        written.append(
            add_document(
                bundle,
                title=row["title"],
                body=row["body"],
                doc_type=row["doc_type"],
                tags=list(row.get("tags") or []),
                affects_modules=list(row.get("affects_modules") or []),
                affects_repos=list(row.get("affects_repos") or []),
                resource=row.get("resource") or None,
                description=row.get("description") or None,
                doc_source="imported",
                relationships=_merge_existing_inferred(
                    bundle, row["concept_id"], row.get("relationships")
                ),
                verified_refs=verified_refs,
            )
        )

    embedded: int | None = None
    if emb.embeddings_available():
        summary = emb.embed_knowledge(conn, bundle, batch_size=32)
        embedded = summary["embedded"]

    # Derived-index refresh (D1): after every approved write the
    # knowledge_edges / knowledge_doc_refs tables are rebuilt from the
    # bundle so declared and overlap edges are queryable immediately. The
    # rebuild leaves its rows uncommitted (caller-owned boundary); this
    # connection is closed by the caller right after, which would roll the
    # rows back, so commit here.
    from cairn.knowledge.index import rebuild_knowledge_index

    index = rebuild_knowledge_index(conn, bundle)
    conn.commit()

    # Island detection (D1): doc components detached from the corpus queue
    # doc-link tasks for critic-gated LLM linking. Idempotent per member
    # set, so re-ingests never duplicate tasks.
    from cairn.knowledge.islands import queue_doc_link_tasks

    doc_link_tasks = queue_doc_link_tasks(conn, bundle)

    report = {
        "written": written,
        "embedded": embedded,
        "accepted": len(accepted),
        "skipped": manifest.get("counts", {}).get("skipped", 0),
        "index_edges": index["edges"],
        "index_doc_refs": index["doc_refs"],
        "verified_refs": verified_refs_total,
        "doc_link_tasks": doc_link_tasks,
        "dangling_pointers": index.get("dangling", []),
    }
    report.update(
        verify_manifest(
            manifest, conn,
            pre_existing=len(pre_ids), overwritten=overwritten,
        )
    )
    return report


def _pre_existing_docs(bundle: OKFBundle) -> set:
    """Bare concept_ids in the bundle before this run writes (empty when
    the store is fresh)."""
    from cairn.knowledge.store import normalize_doc_id

    if not bundle.root.exists():
        return set()
    return {
        normalize_doc_id(bundle, cid)
        for cid in bundle.list_concepts(prefix="knowledge/")
    }


def _merge_existing_inferred(
    bundle: OKFBundle, concept_id: str, declared
) -> list:
    """Declared relationships plus the concept's existing ``kind: inferred``
    entries.

    ``add_document`` rewrites the promoted concept from source, so a
    re-ingest without this merge would wipe critic-approved inferred
    links -- frontmatter is the durable record, and the merge keeps it
    intact across re-scans (the post-write rebuild re-indexes the entries
    from the merged frontmatter). Declared entries come first, so a pair
    the source declares keeps its source kind; dedupe is on
    ``(concept_id, relation, kind)``. A concept the store does not have
    yet merges nothing.
    """
    from cairn.knowledge.relationships import INFERRED, normalize_relationships

    merged = normalize_relationships(list(declared or []))
    if not bundle.root.exists():
        return merged
    try:
        existing = bundle.read_concept(concept_id)
    except Exception:
        return merged
    inferred = [
        entry
        for entry in normalize_relationships(
            existing.extensions.get("relates_to")
        )
        if entry.get("kind") == INFERRED
    ]
    return normalize_relationships(merged + inferred)


def verify_manifest(
    manifest: dict,
    conn=None,
    pre_existing: int | None = None,
    overwritten: int = 0,
) -> dict:
    """Post-write checks: count vs manifest, OKF validation, smoke search.

    Three verify legs (FR-008/TC-024): the store's document count must
    EQUAL the expected population -- ``pre_existing + accepted -
    overwritten`` when the caller supplies the executor's pre-write state
    (``add_document`` overwrites in place, so a fully-successful run
    leaves the store holding exactly that many docs, incremental or not),
    or the manifest's accepted count alone when they are absent
    (standalone, pre-write use: a store not holding exactly the batch is
    a mismatch). The ``cairn validate`` OKF-conformance check must pass
    (run in-process, no subprocess), and a smoke search must hit. Safe to
    run before any write: an absent store reports zeros, a failed
    validation, and no smoke hit rather than creating anything.
    """
    from cairn.knowledge.store import list_documents
    from cairn.okf.bundle import OKFBundle
    from cairn.paths import resolve_store

    accepted = [row for row in manifest.get("rows", []) if "skip" not in row]
    expected = (
        len(accepted)
        if pre_existing is None
        else pre_existing + len(accepted) - overwritten
    )
    store = resolve_store()
    validated = _validate_leg(store)
    if not store.knowledge.exists():
        return {
            "store_count": 0,
            "expected_count": expected,
            # Equality contract (TC-024): an absent store under-wrote.
            "count_ok": False,
            "smoke_search_hit": False,
            **validated,
        }

    bundle = OKFBundle(str(store.knowledge))
    docs = list_documents(bundle)
    smoke_hit = False
    if accepted and conn is not None:
        from cairn.knowledge.search import search_knowledge

        probe = accepted[0]["title"].split(" — ")[-1]
        smoke_hit = bool(search_knowledge(conn, bundle, probe, limit=5))

    return {
        "store_count": len(docs),
        "expected_count": expected,
        # Strict equality against the whole-store expectation: `>=` would
        # mask an under-write, and a batch-only figure would fail every
        # fully-successful run into a store that already holds documents.
        "count_ok": len(docs) == expected,
        "smoke_search_hit": smoke_hit,
        **validated,
    }


def _validate_leg(store) -> dict:
    """Run the ``cairn validate`` conformance check in-process (TC-024).

    Calls the same :func:`cairn.okf.conformance.check_bundle` the CLI
    wraps -- no subprocess -- against the knowledge bundle path the
    executor already resolved. An absent bundle fails the leg (check_bundle
    reports the missing root). Defensive by contract: any raise degrades
    to ``validate_ok=False`` with the message, never a crashed run.
    """
    try:
        from cairn.okf.conformance import check_bundle

        errors = check_bundle(str(store.knowledge))
    except Exception as e:  # a verify leg must never crash the run
        return {
            "validate_ok": False,
            "validate_errors": None,
            "validate_message": str(e),
        }
    return {
        "validate_ok": not errors,
        "validate_errors": len(errors),
        "validate_message": errors[0] if errors else "",
    }
