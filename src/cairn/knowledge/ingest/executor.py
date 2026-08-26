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
    """Post-write checks: store count vs manifest accepted, smoke search.

    Safe to run before any write: an absent store reports zeros and no
    smoke hit rather than creating anything.
    """
    from cairn.knowledge.store import list_documents
    from cairn.okf.bundle import OKFBundle
    from cairn.paths import resolve_store

    accepted = [row for row in manifest.get("rows", []) if "skip" not in row]
    store = resolve_store()
    if not store.knowledge.exists():
        return {
            "store_count": 0,
            "expected_count": len(accepted),
            "count_ok": False,
            "smoke_search_hit": False,
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
        "count_ok": len(docs) >= len(accepted),
        "smoke_search_hit": smoke_hit,
    }
