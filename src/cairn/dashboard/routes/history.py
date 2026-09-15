"""History, token, and session-chain routes."""

from __future__ import annotations

import csv
import io
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

from . import DashboardContext


def register(routes: list[Any], context: DashboardContext) -> None:
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    from ..app import _EXPORT_ROW_LIMIT
    from ..data import (
        SESSION_GAP_S,
        get_read_only_db,
        get_session_chains,
        get_tool_tokens,
        list_history,
    )

    db_path = context.db_path
    knowledge_dir = context.knowledge_dir
    render = context.render
    resolve_selection = context.resolve_selection
    templates = context.templates
    is_hx_request = context.is_hx_request
    _resolve_window = context.resolve_window

    # Content-Disposition filename alphabet: anything else collapses to ``_``.
    _FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")

    def _export_filename(view: str, ext: str, hints) -> str:
        """Attachment filename: view name plus the active filter hints
        (``history-tool-x-window-7d.csv``), header-safe by construction."""
        parts = [view, *(f"{key}-{value}" for key, value in hints if value)]
        return _FILENAME_UNSAFE.sub("_", "-".join(parts))[:120] + f".{ext}"

    def _csv_text(rows) -> str:
        """Row dicts as RFC 4180 CSV text (the csv module quotes as needed);
        None renders as an empty field, the CSV reading of JSON's null."""
        buf = io.StringIO()
        writer = csv.writer(buf)
        if rows:
            writer.writerow(rows[0].keys())
            writer.writerows(
                ["" if value is None else value for value in row.values()]
                for row in rows
            )
        return buf.getvalue()

    # Exports ride the same seams the views ride: resolve_selection
    # for the store, _resolve_window for the window, the view's filter
    # params, and the very data functions the views render from — parity
    # by construction. The cursor params (before/after) page the HTML view
    # and are not filters, so the unpaginated export drops them.

    def _history_export_rows(request: Request):
        tool = request.query_params.get("tool", "").strip() or None
        session = request.query_params.get("session", "").strip() or None
        source = request.query_params.get("source", "").strip() or None
        window, since = _resolve_window(request.query_params.get("window"))
        selected_db, _, _ = resolve_selection(request, db_path, knowledge_dir)
        conn = get_read_only_db(selected_db)
        try:
            result = list_history(
                conn,
                tool_name=tool,
                session_id=session,
                source=source,
                since=since,
                limit=_EXPORT_ROW_LIMIT,
            )
        finally:
            conn.close()
        return result["rows"], [
            ("tool", tool),
            ("session", session),
            ("source", source),
            ("window", None if window == "all" else window),
        ]

    def _tokens_export_rows(request: Request):
        window, since = _resolve_window(request.query_params.get("window"))
        selected_db, _, _ = resolve_selection(request, db_path, knowledge_dir)
        conn = get_read_only_db(selected_db)
        try:
            rows = get_tool_tokens(conn, since=since)
        finally:
            conn.close()
        return rows, [("window", None if window == "all" else window)]

    def _attached(response: Response, filename: str) -> Response:
        response.headers["Content-Disposition"] = (
            f'attachment; filename="{filename}"'
        )
        return response

    def history_csv(request: Request) -> Response:
        rows, hints = _history_export_rows(request)
        return _attached(
            Response(_csv_text(rows), media_type="text/csv"),
            _export_filename("history", "csv", hints),
        )

    def history_json(request: Request) -> Response:
        rows, hints = _history_export_rows(request)
        return _attached(
            JSONResponse(rows), _export_filename("history", "json", hints)
        )

    def tokens_csv(request: Request) -> Response:
        rows, hints = _tokens_export_rows(request)
        return _attached(
            Response(_csv_text(rows), media_type="text/csv"),
            _export_filename("tokens", "csv", hints),
        )

    def tokens_json(request: Request) -> Response:
        rows, hints = _tokens_export_rows(request)
        return _attached(
            JSONResponse(rows), _export_filename("tokens", "json", hints)
        )

    def history(request: Request) -> Response:
        tool = request.query_params.get("tool", "").strip() or None
        session = request.query_params.get("session", "").strip() or None
        source = request.query_params.get("source", "").strip() or None
        before = request.query_params.get("before", "").strip() or None
        after = request.query_params.get("after", "").strip() or None
        window, since = _resolve_window(request.query_params.get("window"))
        selected_db, _, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        conn = get_read_only_db(selected_db)
        try:
            result = list_history(
                conn,
                tool_name=tool,
                session_id=session,
                source=source,
                before=before,
                after=after,
                since=since,
            )
        finally:
            conn.close()
        context = {
            "calls": result["rows"],
            "tool": tool or "",
            "session": session or "",
            "source": source or "",
            "before": before or "",
            "after": after or "",
            "window": window,
            "next_cursor": result["next"],
            "prev_cursor": result["prev"],
            "store_key": store_key,
        }
        if is_hx_request(request):
            # htmx fragment: the polled region only — one cheap region
            # render, never the full page (the shell rides the page load).
            # The filter form's live fields ride the request and the page
            # cursor rides the region's hx-get URL, so the fragment
            # re-renders exactly the slice the page shows.
            return templates.TemplateResponse(
                request, "history_region.html", context
            )
        return render(request, "history.html", context)

    def tokens(request: Request) -> Response:
        window, since = _resolve_window(request.query_params.get("window"))
        selected_db, _, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        conn = get_read_only_db(selected_db)
        try:
            rows = get_tool_tokens(conn, since=since)
        finally:
            conn.close()
        context = {"tools": rows, "window": window, "store_key": store_key}
        if is_hx_request(request):
            # htmx fragment: the polled region only (see history).
            return templates.TemplateResponse(
                request, "tokens_region.html", context
            )
        return render(request, "tokens.html", context)

    def chains(request: Request) -> Response:
        window, since = _resolve_window(request.query_params.get("window"))
        expand = request.query_params.get("expand", "").strip() or None
        # Session filter, read like history's tool/session params:
        # absent or blank means no filter.
        session = request.query_params.get("session", "").strip() or None
        selected_db, _, store_key = resolve_selection(
            request, db_path, knowledge_dir
        )
        conn = get_read_only_db(selected_db)
        try:
            result = get_session_chains(
                conn, since=since, session_id=session, expand=expand
            )
        finally:
            conn.close()
        context = {
            "chains": result["chains"],
            "chains_truncated": result["truncated"],
            "total_chains": result["total_chains"],
            "expand": expand or "",
            "gap_minutes": SESSION_GAP_S // 60,
            "session": session or "",
            "window": window,
            "store_key": store_key,
        }
        if is_hx_request(request):
            # htmx fragment: the polled region only (see history).
            return templates.TemplateResponse(
                request, "chains_region.html", context
            )
        return render(request, "chains.html", context)

    routes.extend(
        [
            Route("/history", history, name="history"),
            Route("/history.csv", history_csv, name="history_csv"),
            Route("/history.json", history_json, name="history_json"),
            Route("/tokens", tokens, name="tokens"),
            Route("/tokens.csv", tokens_csv, name="tokens_csv"),
            Route("/tokens.json", tokens_json, name="tokens_json"),
            Route("/chains", chains, name="chains"),
        ]
    )
