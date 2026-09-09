"""Graph re-theme + shell icon contract (M2 UI foundation closer).

Four pinned invariants:

- Canvases re-theme through the shell's single ``cairn:theme-changed``
  broadcast: shell.js owns the only ``data-theme`` observer, and the
  graph/database canvas scripts listen for the event instead of each
  observing on their own.
- The canvas palettes are theme tokens, not JS constants: the per-kind
  node colors live in app.css's theme blocks as ``--kind-*`` and reach
  the canvas through the getComputedStyle proxy — no hex color literal
  remains in the hand-written scripts.
- The /graph physics simulation is JS-driven motion the reduced-motion
  media query cannot reach, so the graph setup disables it under
  ``prefers-reduced-motion: reduce``.
- The conventional ``/favicon.ico`` path serves the icon — a browser's
  automatic probe must land a 200, never a 404 in the network log — and
  the shell links the same icon for browsers that honor
  ``<link rel="icon">``.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest


def _dashboard_dir() -> Path:
    import cairn.dashboard

    return Path(cairn.dashboard.__file__).resolve().parent


def _script(name: str) -> str:
    return (_dashboard_dir() / "static" / name).read_text(encoding="utf-8")


def _client(tmp_path):
    """A client over a schema-complete, empty store: every main view
    renders the real shell (no missing-DB fallback page)."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app
    from cairn.graph.schema import _apply_schema

    db_path = str(tmp_path / "retheme.db")
    conn = sqlite3.connect(db_path)
    try:
        _apply_schema(conn)
        conn.commit()
    finally:
        conn.close()
    return TestClient(
        create_app(db_path=db_path, knowledge_dir=str(tmp_path / "missing"))
    )


# ---------------------------------------------------------------------------
# Theme broadcast: one observer, event-driven consumers
# ---------------------------------------------------------------------------


def test_shell_broadcasts_theme_changes_from_a_single_observer():
    """shell.js is the theme event's only source: it observes data-theme
    on the root and dispatches cairn:theme-changed on document."""
    shell = _script("shell.js")
    assert "MutationObserver" in shell
    assert "cairn:theme-changed" in shell
    assert 'attributeFilter: ["data-theme"]' in shell


def test_canvas_scripts_listen_instead_of_observing():
    """The graph and database canvas scripts carry no data-theme observer
    of their own — they subscribe to the shell's broadcast, so a theme
    flip has exactly one observer app-wide."""
    for name in ("app.js", "db-graph.js"):
        text = _script(name)
        assert "MutationObserver" not in text, name
        assert 'addEventListener("cairn:theme-changed"' in text, name


# ---------------------------------------------------------------------------
# Canvas palettes are theme tokens, not JS constants
# ---------------------------------------------------------------------------

_KINDS = (
    "function",
    "method",
    "class",
    "interface",
    "enum",
    "module",
    "external",
)


def test_kind_colors_resolve_through_token_names():
    """The graph script maps each node kind to a --kind-* theme token and
    reads it through the getComputedStyle proxy — the palette ships with
    the stylesheet, never with the script."""
    app = _script("app.js")
    for kind in _KINDS:
        assert f'"--kind-{kind}"' in app, kind
    assert "getComputedStyle" in app


def test_hand_written_scripts_carry_no_hex_colors():
    """Every canvas color arrives via a CSS variable — a hex literal in a
    hand-written script is a hardcoded palette that a theme flip cannot
    reach (vendored bundles are excluded from this sweep)."""
    for name in ("app.js", "db-graph.js", "shell.js"):
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", _script(name)), name


def test_kind_tokens_declared_in_both_theme_blocks(tmp_path):
    """Each --kind-* token is declared in the dark and the light theme
    blocks (the theme contract test pins the first :root block)."""
    css = _client(tmp_path).get("/static/app.css").text
    for selector in ('[data-theme="dark"]', '[data-theme="light"]'):
        block = css[css.index(selector) :]
        block = block[: block.index("}")]
        for kind in _KINDS:
            assert f"--kind-{kind}:" in block, (selector, kind)


# ---------------------------------------------------------------------------
# Reduced motion reaches the JS-driven simulation
# ---------------------------------------------------------------------------


def test_graph_setup_reads_reduced_motion_and_drops_physics():
    """The graph setup consults matchMedia('(prefers-reduced-motion:
    reduce)') and gates the physics block behind it — CSS collapse cannot
    reach a canvas simulation, so the setup must."""
    app = _script("app.js")
    assert 'matchMedia("(prefers-reduced-motion: reduce)")' in app
    assert "prefersReducedMotion" in app


# ---------------------------------------------------------------------------
# Favicon: no 404 in the network log
# ---------------------------------------------------------------------------


def test_favicon_route_serves_the_svg(tmp_path):
    """The conventional /favicon.ico probe — every browser fires it when
    no link tag matches — lands a 200 with the SVG icon, not a 404."""
    resp = _client(tmp_path).get("/favicon.ico")
    assert resp.status_code == 200
    assert "svg" in resp.headers["content-type"]
    assert b"<svg" in resp.content


def test_favicon_route_404s_when_the_icon_is_missing(tmp_path, monkeypatch):
    """A missing vendored icon is a 404, not a 500: the route serves
    whatever ships beside the app, and nothing shipped is not-found —
    the browser's icon probe degrades, the page never errors."""
    import cairn.dashboard.app as dashboard_app

    bare = tmp_path / "bare"
    (bare / "static").mkdir(parents=True)
    (bare / "templates").mkdir()
    monkeypatch.setattr(dashboard_app, "_PACKAGE_DIR", bare)

    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    client = TestClient(
        dashboard_app.create_app(
            db_path=str(tmp_path / "missing.db"),
            knowledge_dir=str(tmp_path / "missing"),
        )
    )
    resp = client.get("/favicon.ico")
    assert resp.status_code == 404


def test_static_favicon_serves_from_assets(tmp_path):
    """The icon ships as a static asset next to the scripts it sits
    beside, served as SVG."""
    resp = _client(tmp_path).get("/static/favicon.svg")
    assert resp.status_code == 200
    assert "svg" in resp.headers["content-type"]
    assert b"<svg" in resp.content


def test_shell_links_the_icon(tmp_path):
    """The shell head carries a <link rel="icon"> pointing at the static
    SVG, so honoring browsers fetch the icon directly."""
    html = _client(tmp_path).get("/").text
    match = re.search(r'<link rel="icon"[^>]*href="([^"]+)"', html)
    assert match, "no icon link in the shell head"
    assert "/static/favicon.svg" in match.group(1)
