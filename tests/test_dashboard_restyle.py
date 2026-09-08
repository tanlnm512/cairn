"""Restyle contract for the M2 design system (m2-restyle-all-views).

Three pinned invariants:

- Shared macro libraries (_table/_filters/_cards) render the repeated
  table/filter/card markup, so a view cannot drift back to a hand-rolled
  form or table; the legacy graph-controls form class is retired.
- The stylesheet, templates, and scripts speak only the ladder tokens:
  the pre-M2 alias names (--bg, --surface, --border, --muted, --canvas,
  --accent-2, ...) are deleted, so an alias that sneaks back resolves to
  nothing in the browser.
- Controls are at final density: --control-h 30px with the paddings and
  line-heights matched to it, so 13px control text renders unclipped.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

# The fourteen main views (same inventory the asset tests crawl).
_MAIN_VIEWS = (
    "/",
    "/workspaces",
    "/projects",
    "/graph",
    "/history",
    "/tokens",
    "/chains",
    "/health",
    "/memory",
    "/tasks",
    "/wiki",
    "/embeddings",
    "/database",
    "/settings",
)

# Sidebar-rendered views (the landing "/" is the brand link, not a nav
# item); each must flag exactly its own entry active.
_NAV_VIEWS = tuple(p for p in _MAIN_VIEWS if p != "/")

# Pre-M2 alias names the constants block used to define; every one is
# deleted — neither a declaration (--name:) nor a usage (var(--name))
# may remain anywhere in the shipped assets.
_ALIAS_TOKENS = (
    "bg",
    "surface",
    "surface-2",
    "sidebar",
    "canvas",
    "text",
    "muted",
    "border",
    "accent-2",
)


def _dashboard_dir() -> Path:
    import cairn.dashboard

    return Path(cairn.dashboard.__file__).resolve().parent


def _client(tmp_path):
    """A client over a schema-complete, empty store: every main view
    renders the real shell (no missing-DB fallback page)."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app
    from cairn.graph.schema import _apply_schema

    db_path = str(tmp_path / "restyle.db")
    conn = sqlite3.connect(db_path)
    try:
        _apply_schema(conn)
        conn.commit()
    finally:
        conn.close()
    return TestClient(
        create_app(db_path=db_path, knowledge_dir=str(tmp_path / "missing"))
    )


def _seeded_client(tmp_path):
    """A client over a store seeded with one project/files/symbols, so
    the data-bearing tables have rows to render."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app
    from cairn.graph.schema import _apply_schema

    db_path = str(tmp_path / "restyle-seeded.db")
    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        _apply_schema(conn)
        conn.execute(
            "INSERT INTO repos (id, name, path, language, indexed_at) "
            "VALUES ('demo', 'demo', 'clients/demo', 'python', "
            "'2026-08-20T08:00:00')"
        )
        conn.execute(
            "INSERT INTO files (id, repo_id, path, language, indexed_at) "
            "VALUES ('f1', 'demo', 'src/demo/core.py', 'python', "
            "'2026-08-20T10:00:00')"
        )
        conn.execute(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind) "
            "VALUES ('s1', 'f1', 'demo_main', 'demo.core.demo_main', "
            "'function')"
        )
        conn.commit()
    finally:
        conn.close()
    return TestClient(
        create_app(db_path=db_path, knowledge_dir=str(tmp_path / "missing"))
    )


# ---------------------------------------------------------------------------
# Alias-token purge
# ---------------------------------------------------------------------------


def test_stylesheet_defines_no_alias_tokens(tmp_path):
    """The constants block's alias layer is deleted: neither declarations
    nor var() usages of the pre-M2 names remain in the stylesheet."""
    css = _client(tmp_path).get("/static/app.css").text
    for alias in _ALIAS_TOKENS:
        assert f"--{alias}:" not in css, alias
        assert f"var(--{alias})" not in css, alias


def test_templates_and_scripts_use_no_alias_tokens(tmp_path):
    """Templates and the hand-written scripts resolve colors through the
    ladder names directly — a quoted alias (e.g. cssVar("--muted"))
    resolves to nothing once the alias block is gone, so its presence
    fails here rather than silently breaking the canvas in the browser."""
    aliases = tuple(f'--{a}"' for a in _ALIAS_TOKENS)
    paths = sorted((_dashboard_dir() / "templates").glob("*.html")) + [
        _dashboard_dir() / "static" / name
        for name in ("app.js", "db-graph.js", "shell.js")
    ]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for alias in aliases:
            assert alias not in text, (path.name, alias)


# ---------------------------------------------------------------------------
# Final control density
# ---------------------------------------------------------------------------


def test_control_height_is_the_dense_target(tmp_path):
    """--control-h is pinned at the dense target so the coordinated
    paddings/line-heights stay matched to it — drifting the constant
    alone would re-clip 13px control text or bloat the density."""
    css = _client(tmp_path).get("/static/app.css").text
    assert "--control-h: 30px" in css


def test_filter_bar_controls_carry_matched_line_height(tmp_path):
    """Inputs, selects, and the submit button in the shared filter bar
    set line-height 1.2 inside the fixed control height — at 13px that
    leaves headroom in a 30px control instead of clipping."""
    css = _client(tmp_path).get("/static/app.css").text
    bar = css.index(".filter-bar")
    section = css[bar : css.index("/* ----", bar + 10)]
    for needed in (
        "height: var(--control-h)",
        "line-height: 1.2",
        'button[type="submit"]',
    ):
        assert needed in section, needed


# ---------------------------------------------------------------------------
# Shared macro rendering
# ---------------------------------------------------------------------------


def test_filter_forms_render_the_shared_filter_bar(tmp_path):
    """The hand-rolled filter forms are gone: history, tasks, and wiki
    render their controls from the shared filter bar class, and the
    legacy graph-controls class no longer appears on any main view."""
    client = _seeded_client(tmp_path)
    for path in ("/history", "/tasks", "/wiki"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert 'class="filter-bar"' in resp.text, path
        assert "graph-controls" not in resp.text, path


def test_tasks_filter_select_renders_options(tmp_path):
    """The shared field macro renders the status select with its options
    and honors the active one — the route-visible half of the tasks
    filter (the filter semantics stay covered by the route tests)."""
    resp = _client(tmp_path).get("/tasks")
    assert resp.status_code == 200
    assert '<select name="status">' in resp.text
    assert 'value="all" selected' in resp.text


def test_history_filter_inputs_keep_their_values(tmp_path):
    """The shared text-field macro carries the submitted value back into
    the input so a filter never visually resets after Apply."""
    resp = _seeded_client(tmp_path).get("/history", params={"tool": "demo_main"})
    assert resp.status_code == 200
    assert 'value="demo_main"' in resp.text


def test_data_tables_render_through_the_shared_table_macro(tmp_path):
    """The projects table renders via the shared macro's exact
    data-table markup; an empty store swaps the table for the shared
    empty state."""
    seeded = _seeded_client(tmp_path).get("/projects")
    assert seeded.status_code == 200
    assert '<table class="data-table">' in seeded.text

    empty = _client(tmp_path).get("/projects")
    assert empty.status_code == 200
    assert '<table class="data-table">' not in empty.text
    assert 'class="empty-state"' in empty.text


def test_stat_rows_render_through_the_cards_macro(tmp_path):
    """The summary tiles render through the shared stat-row macro: the
    history row is unconditional (zero counts still summarize), the
    projects row rides its view's has-projects branch."""
    empty = _client(tmp_path).get("/history")
    assert empty.status_code == 200
    assert 'class="stat-row"' in empty.text
    assert 'class="stat-tile"' in empty.text

    seeded = _seeded_client(tmp_path).get("/projects")
    assert seeded.status_code == 200
    assert 'class="stat-row"' in seeded.text
    assert 'class="stat-tile"' in seeded.text


def test_main_views_carry_no_inline_style_blocks(tmp_path):
    """View-scoped CSS lives in app.css under the token system — a
    per-view <style> block cannot see the alias purge or theme blocks,
    so the shared chain styles (the last inline block) moved into the
    stylesheet."""
    client = _client(tmp_path)
    for path in _MAIN_VIEWS:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "<style>" not in resp.text, path


# ---------------------------------------------------------------------------
# Restyled shell on every main view
# ---------------------------------------------------------------------------


def test_every_main_view_renders_the_restyled_shell(tmp_path):
    """All fourteen main views return 200 and render the shared shell —
    sidebar, topbar, and a non-empty main region — on the token-driven
    stylesheet (no blank or unstyled views)."""
    client = _client(tmp_path)
    for path in _MAIN_VIEWS:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert '<aside class="sidebar">' in resp.text, path
        assert '<header class="topbar">' in resp.text, path
        main = resp.text.split('<main class="site-main">', 1)[1]
        assert len(main.strip()) > 200, f"{path}: main region empty"
        assert "Traceback" not in main, path


def test_sidebar_flags_exactly_one_active_nav_item(tmp_path):
    """The view's own sidebar entry — and only it — carries the active
    state on every nav view; the landing page flags none."""
    client = _client(tmp_path)
    for path in _NAV_VIEWS:
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert resp.text.count('aria-current="page"') == 1, path
    assert _client(tmp_path).get("/").text.count('aria-current="page"') == 0


def test_active_nav_link_targets_the_current_view(tmp_path):
    """The active anchor is the current view's link (href == path), not
    a neighboring entry — active state must track the route."""
    resp = _client(tmp_path).get("/history")
    active = re.search(
        r'<a href="(/history)"[^>]*class="active" aria-current="page"', resp.text
    )
    assert active, "history link not flagged active on /history"
