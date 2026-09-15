"""Settings, embedding-status, and database routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette.requests import Request
    from starlette.responses import Response

from . import DashboardContext


def _settings_context(
    store_key: str, saved: bool = False, error: str = "", parity=None
) -> dict:
    """Settings-page context: per-knob file/effective state.

    ``prefill`` is the file value when one exists — the form edits the
    file layer — else the effective value; ``pinned`` marks an env var
    shadowing whatever the file holds, rendered as an "overridden by
    environment" marker so a save that "does nothing" is explainable.
    """
    import os

    from ..app import SETTINGS_BACKENDS, SETTINGS_KEYS
    from ... import paths

    cfg = {}
    for key in SETTINGS_KEYS:
        env_value = (os.environ.get(key) or "").strip()
        file_raw = paths.get_config_value(key)
        file_value = "" if file_raw is None else str(file_raw).strip()
        # The API key is write-only: its prefill stays "" unconditionally
        # (the form's key input never renders a secret back). The
        # set/not-set badges read ``effective``, so they still work.
        cfg[key] = {
            "prefill": (
                "" if key == "CAIRN_EMBED_API_KEY" else file_value or env_value
            ),
            "effective": env_value or file_value,
            "pinned": bool(env_value),
        }
    return {
        "cfg": cfg,
        "backends": SETTINGS_BACKENDS,
        "backend_value": cfg["CAIRN_EMBED_BACKEND"]["prefill"] or "local",
        "saved": saved,
        "error": error,
        "parity": parity,
        "store_key": store_key,
    }


def _embeddings_rows(conn) -> list:
    """Per-corpus ``{corpus, model, count, last}`` rows for the status
    view: the code corpus reads the embeddings table directly,
    knowledge/memory ride embed_knowledge_count/embed_memory_count.
    A corpus whose count cannot be read (store predating the table,
    unresolvable or malformed backend config) reports unknown instead
    of failing the page.
    """
    from ...graph import embeddings

    rows = []
    for corpus, table in (
        ("code", "embeddings"),
        ("knowledge", "knowledge_embeddings"),
        ("memory", "memory_embeddings"),
    ):
        model, count, last = None, None, None
        try:
            if corpus == "knowledge":
                model = embeddings.current_model(corpus="knowledge")
                count = embeddings.embed_knowledge_count(conn)
            elif corpus == "memory":
                model = embeddings.current_model(corpus="memory")
                count = embeddings.embed_memory_count(conn)
            else:
                model = embeddings.current_model()
                count = conn.execute(
                    "SELECT COUNT(*) FROM embeddings WHERE model = ?",
                    (model,),
                ).fetchone()[0]
            last = conn.execute(
                f"SELECT MAX(embedded_at) FROM {table} WHERE model = ?",
                (model,),
            ).fetchone()[0]
        except Exception:
            # Unknown, rendered as an em-dash — never a 500. Broad on
            # purpose: this is a status page, and current_model()'s
            # URL resolution can raise ValueError on a malformed
            # CAIRN_EMBED_BASE_URL, not just RuntimeError/sqlite3
            # errors.
            pass
        rows.append(
            {"corpus": corpus, "model": model, "count": count, "last": last}
        )
    return rows


def _embeddings_status(request: Request, context: DashboardContext) -> Response:
    """Settings status view: effective backend + precedence, resolved
    stamp, per-corpus counts, probe health, and the active fallback
    rung — the rung rows read ladder_state(), the same accessor the
    degradation banner builds from (one degradation source)."""
    from ..data import get_read_only_db
    from ...graph import embed_ladder, embeddings

    selected_db, _, store_key = context.resolve_selection(
        request, context.db_path, context.knowledge_dir
    )
    probe_ok = context.server_probe_once()  # first: rung adoption may retarget
    backend = embeddings._backend_name()
    try:
        stamp = embeddings.current_model()
    except Exception as exc:  # unresolvable backend is status, not a 500
        stamp = f"unresolved ({type(exc).__name__}: {exc})"
    state = embed_ladder.ladder_state()
    conn = get_read_only_db(selected_db)
    try:
        corpora = _embeddings_rows(conn)
    finally:
        conn.close()
    return context.render(
        request,
        "embeddings.html",
        {
            "backend": backend,
            "stamp": stamp,
            "is_server": backend in embeddings._SERVER_FAMILY,
            "probe_ok": probe_ok,
            "rung": state if state is not None and state.active else None,
            "corpora": corpora,
            "cfg": _settings_context(store_key)["cfg"],
            "store_key": store_key,
        },
    )


def _database(request: Request, context: DashboardContext) -> Response:
    from ..data import get_database_schema, get_read_only_db

    selected_db, _, store_key = context.resolve_selection(
        request, context.db_path, context.knowledge_dir
    )
    conn = get_read_only_db(selected_db)
    try:
        schema = get_database_schema(conn)
    finally:
        conn.close()
    return context.render(
        request,
        "database.html",
        {"schema": schema, "store_key": store_key},
    )


def _settings_page(request: Request, context: DashboardContext) -> Response:
    _, _, store_key = context.resolve_selection(
        request, context.db_path, context.knowledge_dir
    )
    return context.render(
        request, "settings.html", _settings_context(store_key)
    )


def _numeric_settings_error(submitted: dict[str, str]) -> str | None:
    from ..app import SETTINGS_NUMERIC

    for key, (cast, what) in SETTINGS_NUMERIC.items():
        value = submitted.get(key, "")
        if not value:
            continue
        try:
            cast(value)
        except ValueError:
            return f"Refused: {key} must be {what}."
    return None


def _base_url_needs_confirmation(submitted: dict[str, str], form) -> bool:
    from ... import paths

    current_base = str(
        paths.get_config_value("CAIRN_EMBED_BASE_URL") or ""
    ).strip()
    return (
        "CAIRN_EMBED_BASE_URL" in submitted
        and submitted["CAIRN_EMBED_BASE_URL"] != current_base
        and "confirm_base_url" not in form
    )


def _save_values(submitted: dict[str, str]) -> dict[str, str]:
    from ..app import SETTINGS_BACKENDS

    values = {}
    for key, value in submitted.items():
        if key == "CAIRN_EMBED_API_KEY":
            if value:  # write-only: blank submit leaves the key untouched
                values[key] = value
        elif key == "CAIRN_EMBED_BACKEND":
            if value in SETTINGS_BACKENDS:  # never write an unknown arm
                values[key] = value
        elif value:
            # A blank submit means "no change", never a write of "":
            # treating it as one silently CLEARED the stored value. A
            # knob is cleared by editing config.json, not from the form.
            values[key] = value
    return values


async def _settings_save(
    request: Request, context: DashboardContext, form
) -> Response:
    from ..app import SETTINGS_KEYS
    from ...graph import embeddings
    from ... import paths

    _, _, store_key = context.resolve_selection(
        request, context.db_path, context.knowledge_dir, form=form
    )
    submitted = {
        key: str(form.get(key) or "").strip()
        for key in SETTINGS_KEYS
        if key in form
    }
    numeric_error = _numeric_settings_error(submitted)
    if numeric_error is not None:
        return context.render(
            request,
            "settings.html",
            _settings_context(store_key, error=numeric_error),
            status_code=400,
        )
    if _base_url_needs_confirmation(submitted, form):
        return context.render(
            request,
            "settings.html",
            _settings_context(
                store_key,
                error="Refused: a base-URL change requires the "
                "'Confirm base-URL change' checkbox.",
            ),
            status_code=400,
        )
    values = _save_values(submitted)
    if not paths.set_config_values(values):
        return context.render(
            request,
            "settings.html",
            _settings_context(
                store_key,
                error=(
                    f"Could not write {paths.CONFIG_FILE}; "
                    "nothing was saved."
                ),
            ),
            status_code=500,
        )
    paths.reset_config_cache()
    embeddings.reset_backend_cache()  # this process re-resolves on next use
    return context.render(
        request, "settings.html", _settings_context(store_key, saved=True)
    )


def _settings_parity_check(
    request: Request, context: DashboardContext
) -> Response:
    from ..data import get_read_only_db
    from ...graph import embed_ladder, embeddings

    selected_db, _, store_key = context.resolve_selection(
        request, context.db_path, context.knowledge_dir
    )
    conn = get_read_only_db(selected_db)
    try:
        try:
            result = embed_ladder.check_parity(
                conn, embeddings.current_model()
            )
            parity = {
                "passed": bool(result.passed),
                "mean": (
                    "—"
                    if result.mean_cosine is None
                    else f"{result.mean_cosine:.4f}"
                ),
                "sampled": result.sampled,
                "reason": result.reason,
            }
        except Exception as exc:  # embed failures ARE the verdict text
            parity = {
                "passed": False,
                "mean": "—",
                "sampled": None,
                "reason": f"{type(exc).__name__}: {exc}",
            }
    finally:
        conn.close()
    return context.render(
        request,
        "settings.html",
        _settings_context(store_key, parity=parity),
    )


def register(routes: list[Any], context: DashboardContext) -> None:
    from starlette.routing import Route

    def embeddings_status(request: Request) -> Response:
        return _embeddings_status(request, context)

    def database(request: Request) -> Response:
        return _database(request, context)

    def settings(request: Request) -> Response:
        return _settings_page(request, context)

    async def settings_save(request: Request) -> Response:
        form = await request.form()
        return await _settings_save(request, context, form)

    def settings_parity_check(request: Request) -> Response:
        return _settings_parity_check(request, context)

    routes.extend(
        [
            Route("/embeddings", embeddings_status, name="embeddings"),
            Route("/database", database, name="database"),
            Route("/settings", settings, name="settings"),
            # The app's first POST routes — loopback-only by the CLI's
            # DEFAULT_HOST + _require_loopback; the GET views stay untouched.
            Route(
                "/settings/save",
                settings_save,
                methods=["POST"],
                name="settings_save",
            ),
            Route(
                "/settings/parity-check",
                settings_parity_check,
                methods=["POST"],
                name="settings_parity_check",
            ),
        ]
    )
