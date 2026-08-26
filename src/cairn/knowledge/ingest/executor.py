"""Approved-run execution: manifest rows -> store -> embeddings (D-003)."""
from __future__ import annotations

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
    """
    from cairn.graph import embeddings as emb

    store = resolve_store()
    store.ensure()
    bundle = OKFBundle(str(store.knowledge))

    accepted = [row for row in manifest.get("rows", []) if "skip" not in row]
    written: list[str] = []
    for row in sorted(accepted, key=lambda r: (r.get("repo", ""), r.get("source_path", ""))):
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
            )
        )

    embedded: int | None = None
    if emb.embeddings_available():
        summary = emb.embed_knowledge(conn, bundle, batch_size=32)
        embedded = summary["embedded"]

    report = {
        "written": written,
        "embedded": embedded,
        "accepted": len(accepted),
        "skipped": manifest.get("counts", {}).get("skipped", 0),
    }
    report.update(verify_manifest(manifest, conn))
    return report


def verify_manifest(manifest: dict, conn=None) -> dict:
    """Post-write checks: count vs manifest, OKF validation, smoke search.

    Three verify legs (FR-008/TC-024): the store's document count must
    EQUAL the manifest's accepted count, the ``cairn validate`` OKF-
    conformance check must pass (run in-process, no subprocess), and a
    smoke search must hit. Safe to run before any write: an absent store
    reports zeros, a failed validation, and no smoke hit rather than
    creating anything.
    """
    from cairn.knowledge.store import list_documents
    from cairn.okf.bundle import OKFBundle
    from cairn.paths import resolve_store

    accepted = [row for row in manifest.get("rows", []) if "skip" not in row]
    store = resolve_store()
    validated = _validate_leg(store)
    if not store.knowledge.exists():
        return {
            "store_count": 0,
            "expected_count": len(accepted),
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
        "expected_count": len(accepted),
        # Strict equality (US5-AC1/TC-024): `>=` would mask an under-write
        # on a pre-populated store.
        "count_ok": len(docs) == len(accepted),
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
