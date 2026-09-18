"""Editor JSON API routes — the frozen contract IDE extensions consume.

GET-only JSON on the dashboard app's loopback defaults:
- ``/editor/symbol`` — identity, calls-only caller/callee counts, and the
  precise depth-limited blast radius in one response.
- ``/editor/file`` — the containing module's compass excerpt and the file's
  relevant memories.
- ``/editor/status`` — indexed-store and freshness state.

Unknown or blank input answers the populated zero shape at 200 — never an
error status. Endpoint paths and JSON shapes are frozen; changes require a
versioned contract change consumed by every platform consumer.
"""

from __future__ import annotations

import sqlite3
from posixpath import dirname
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

from . import DashboardContext

# ``depth`` counts caller hops: absent or non-numeric falls back to the
# default, and a present value clamps into [1, MAX_DEPTH].
DEFAULT_DEPTH = 2
MAX_DEPTH = 10

# The traversal layer's default caller lookup caps at 200 rows, which would
# silently undercount hub symbols; the count queries over-fetch instead.
COUNT_LIMIT = 100_000


def _parse_depth(raw: str | None) -> int:
    """Effective blast-radius depth for the ``depth`` query param."""
    if raw is None or not raw.strip().isdecimal():
        return DEFAULT_DEPTH
    return min(MAX_DEPTH, max(1, int(raw.strip())))


def _symbol_zero(name: str, depth: int) -> dict:
    """The populated zero shape for an unknown or blank symbol name."""
    return {
        "found": False,
        "symbol": name,
        "kind": "",
        "qualified_name": "",
        "file": "",
        "callers": {"count": 0},
        "callees": {"count": 0},
        "blast_radius": {
            "depth": depth,
            "total": 0,
            "truncated": False,
            "symbols": [],
        },
    }


def _symbol_payload(
    conn: sqlite3.Connection, name: str, depth: int
) -> dict:
    """Identity, calls-only counts, and the depth-limited blast radius for
    one symbol name. Same-name definitions count together; the identity row
    is the first by (file path, id)."""
    stripped = name.strip()
    if not stripped:
        return _symbol_zero(name, depth)
    from ...graph.traversal import get_callers, get_callees, impact_analysis

    row = conn.execute(
        "SELECT s.name, s.kind, s.qualified_name, f.path AS file "
        "FROM symbols s JOIN files f ON s.file_id = f.id "
        "WHERE s.name = ? ORDER BY f.path ASC, s.id ASC LIMIT 1",
        (stripped,),
    ).fetchone()
    if row is None:
        return _symbol_zero(name, depth)
    callers = get_callers(conn, stripped, limit=COUNT_LIMIT, kind="calls")
    callees = get_callees(conn, stripped, limit=COUNT_LIMIT, kind="calls")
    # ``depth`` counts total caller hops; the traversal's max_depth bounds
    # hops beyond the direct callers.
    impact = impact_analysis(conn, stripped, max_depth=depth - 1)
    return {
        "found": True,
        "symbol": row["name"],
        "kind": row["kind"] or "",
        "qualified_name": row["qualified_name"] or "",
        "file": row["file"] or "",
        "callers": {"count": len(callers)},
        "callees": {"count": len(callees)},
        "blast_radius": {
            "depth": depth,
            "total": impact["total"],
            "truncated": impact["truncated"],
            "symbols": [
                {
                    "symbol": r["symbol"],
                    "file": r["file"],
                    "repo": r["repo"],
                    "depth": r["depth"],
                }
                for r in impact["impacted"]
            ],
        },
    }


def _compass(bundle: Any, module: str) -> dict:
    """The module's compass excerpt; zero shape when no compass covers it.

    A compass concept covers the module when its non-empty resource and the
    module overlap as substrings (the bundle's established compass-matching
    convention); the first match in the bundle's sorted concept order wins.
    """
    if not module:
        return {"found": False, "title": "", "body": ""}
    for cid in bundle.list_concepts(prefix="compass/"):
        try:
            concept = bundle.read_concept(cid)
        except Exception:
            continue
        resource = concept.resource or ""
        if resource and (resource in module or module in resource):
            return {
                "found": True,
                "title": concept.title or "",
                "body": concept.body or "",
            }
    return {"found": False, "title": "", "body": ""}


def _relevant_memories(bundle: Any, path: str, module: str) -> list:
    """The file's relevant memories, newest-first.

    A memory is relevant when the file's path or module appears in the
    memory's resource, title, or body; blank keys never match.
    """
    from ..data import _EPOCH, _parse_ts

    needles = sorted({key for key in (path, module) if key})
    if not needles:
        return []
    scored = []
    for cid in bundle.list_concepts(prefix="memory/"):
        try:
            concept = bundle.read_concept(cid)
        except Exception:
            continue
        haystacks = (concept.resource or "", concept.title or "", concept.body or "")
        if not any(needle in hay for needle in needles for hay in haystacks):
            continue
        scored.append((_parse_ts(concept.timestamp) or _EPOCH, cid, concept))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {
            "id": cid,
            "type": concept.extensions.get("memory_type") or concept.type or "",
            "title": concept.title or "",
            "body": concept.body or "",
        }
        for _, cid, concept in scored
    ]


def _file_payload(bundle: Any, path: str) -> dict:
    """The file's module, compass excerpt, and relevant memories."""
    module = dirname(path)
    return {
        "path": path,
        "module": module,
        "compass": _compass(bundle, module),
        "memories": _relevant_memories(bundle, path, module),
    }


def _status_payload(conn: sqlite3.Connection) -> dict:
    """Indexed-store and freshness state; the zero shape on an unindexed
    store or one missing the graph tables."""
    try:
        files = conn.execute(
            "SELECT COUNT(*) AS n, MAX(indexed_at) AS latest FROM files"
        ).fetchone()
        symbols = conn.execute("SELECT COUNT(*) AS n FROM symbols").fetchone()
    except sqlite3.Error:
        return {"indexed": False, "files": 0, "symbols": 0, "indexed_at": ""}
    return {
        "indexed": files["n"] > 0,
        "files": files["n"],
        "symbols": symbols["n"],
        "indexed_at": files["latest"] or "",
    }


def register(routes: list[Any], context: DashboardContext) -> None:
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    from ..data import get_read_only_db
    from ...okf.bundle import OKFBundle

    db_path = context.db_path
    knowledge_dir = context.knowledge_dir
    resolve_selection = context.resolve_selection

    def editor_symbol(request: Request) -> Response:
        depth = _parse_depth(request.query_params.get("depth"))
        selected_db, _, _ = resolve_selection(request, db_path, knowledge_dir)
        conn = get_read_only_db(selected_db)
        try:
            payload = _symbol_payload(
                conn, request.query_params.get("name", ""), depth
            )
        finally:
            conn.close()
        return JSONResponse(payload)

    def editor_file(request: Request) -> Response:
        path = request.query_params.get("path", "")
        _, selected_knowledge, _ = resolve_selection(
            request, db_path, knowledge_dir
        )
        return JSONResponse(_file_payload(OKFBundle(selected_knowledge), path))

    def editor_status(request: Request) -> Response:
        selected_db, _, _ = resolve_selection(request, db_path, knowledge_dir)
        conn = get_read_only_db(selected_db)
        try:
            payload = _status_payload(conn)
        finally:
            conn.close()
        return JSONResponse(payload)

    routes.extend(
        [
            Route("/editor/symbol", editor_symbol, name="editor_symbol"),
            Route("/editor/file", editor_file, name="editor_file"),
            Route("/editor/status", editor_status, name="editor_status"),
        ]
    )
