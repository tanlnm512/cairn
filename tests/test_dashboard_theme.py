"""Theme apply/persist tests (TC-008 / FR-006, spec ui-dashboard-polish).

TC-008's pass condition is split: an automated half (the apply/persist
machinery's unit test -- the toggle, the pre-paint script, the dark
palette) and a manual half, recorded below.

Manual procedure (TC-008's manual half -- run by a human in a browser):

1. Start the dashboard (``cairn dashboard``) and click the theme toggle
   until dark is active.
2. Visit every view -- ``/``, ``/workspaces``, ``/projects``, ``/graph``,
   ``/history``, ``/tokens``, ``/chains``, ``/health``, ``/memory``,
   ``/tasks`` -- each must render dark, with no light flash on arrival.
3. Reload each view -- the dark choice persists (localStorage key
   ``cairn-theme``), applied before first paint.
4. Close the browser entirely, reopen it, and visit the dashboard again
   -- dark is still the applied theme.

The automated half pins what that procedure exercises: every rendered
page (base template) carries the toggle control and the inline head
script that applies the theme synchronously -- before the body can
paint -- reading the stored choice first and prefers-color-scheme only
as the default; the stylesheet's ``[data-theme="dark"]`` block overrides
every themed CSS variable the light ``:root`` defines. The
prefers-color-scheme resolution deliberately lives in the script, not a
CSS media query: only the script can let a stored choice win before
paint.
"""
from __future__ import annotations

import pytest

_THEME_STORAGE_KEY = "cairn-theme"
_THEMED_VARS = ("--bg", "--surface", "--text", "--muted", "--border", "--accent")


def _client(tmp_path):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app

    return TestClient(
        create_app(
            db_path=str(tmp_path / "dash.db"),
            knowledge_dir=str(tmp_path / "missing"),
        )
    )


def _rendered(tmp_path) -> str:
    """A rendered page (the landing route extends the base template and
    opens no database)."""
    resp = _client(tmp_path).get("/")
    assert resp.status_code == 200
    return resp.text


def _head_script(html: str) -> str:
    """The first inline <script> block (the theme script in <head>)."""
    start = html.index("<script>")
    return html[start : html.index("</script>", start)]


def _declarations(block: str) -> dict:
    """A CSS rule body's ``name -> value`` declarations."""
    decls = {}
    for chunk in block.split(";"):
        name, sep, value = chunk.partition(":")
        if sep and name.strip():
            decls[name.strip()] = value.strip()
    return decls


def _rule_block(css: str, selector: str) -> str:
    idx = css.index(selector)
    brace = css.index("{", idx)
    return css[brace + 1 : css.index("}", brace)]


def test_rendered_page_carries_theme_toggle(tmp_path):
    """TC-008 / FR-006: the base template renders the toggle button with
    its stable id, and the script wires itself to that id."""
    html = _rendered(tmp_path)
    assert 'id="theme-toggle"' in html
    assert 'class="theme-toggle"' in html
    assert 'getElementById("theme-toggle")' in html


def test_theme_script_is_inline_in_head_and_applies_before_paint(tmp_path):
    """TC-008 / FR-006: the apply/persist script is inline in <head>
    (before <body> can paint) and runs at parse time -- its first
    ``applyTheme();`` call precedes any DOMContentLoaded deferral."""
    html = _rendered(tmp_path)
    head_end = html.index("</head>")
    body_start = html.index("<body")
    assert html.index("window.__cairnApplyTheme") < head_end < body_start

    script = _head_script(html)
    assert script.index("applyTheme();") < script.index(
        'document.addEventListener("DOMContentLoaded"'
    )


def test_theme_script_carries_apply_and_persist_contract(tmp_path):
    """TC-008 / FR-006: the script's contract markers -- the localStorage
    key, both theme values, the prefers-color-scheme default via
    matchMedia, persistence through setItem, application on the document
    element's data-theme, and the global apply function."""
    script = _head_script(_rendered(tmp_path))
    assert f'"{_THEME_STORAGE_KEY}"' in script
    assert '"dark"' in script and '"light"' in script
    assert "matchMedia" in script
    assert "(prefers-color-scheme: dark)" in script
    assert "localStorage.getItem" in script
    assert "localStorage.setItem" in script
    assert "window.__cairnApplyTheme = applyTheme" in script
    assert "documentElement.dataset.theme" in script


def test_dark_palette_overrides_every_themed_variable(tmp_path):
    """TC-008 / FR-006: the served stylesheet's ``[data-theme="dark"]``
    block redefines every CSS variable the light ``:root`` defines (all
    six themed variables, each to a different value) plus
    ``color-scheme: dark``; no prefers-color-scheme media query exists in
    CSS (the resolution lives in the head script so a stored choice wins
    before paint)."""
    css = _client(tmp_path).get("/static/app.css")
    assert css.status_code == 200

    root = _declarations(_rule_block(css.text, ":root"))
    dark = _declarations(_rule_block(css.text, '[data-theme="dark"]'))

    for var in _THEMED_VARS:
        assert var in root  # a rename cannot silently shrink dark coverage
    for var, light_value in root.items():
        assert var in dark, var
        assert dark[var] != light_value, var
    assert dark.get("color-scheme") == "dark"
    assert "@media (prefers-color-scheme" not in css.text
