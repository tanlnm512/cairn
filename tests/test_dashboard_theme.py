"""Theme token contract tests (D2 design-token system, M2 UI foundation).

The stylesheet ships a dark-first token ladder: the first variable block
holds the theme tokens with their dark values and nothing else, and BOTH
theme blocks that follow redefine every one of those tokens plus the
color scheme — the dark block restating the defaults, the light block
carrying the light ladder. Theme resolution lives in base.html's inline
head script (stored choice, then prefers-color-scheme, then dark) so a
stored choice wins before first paint; a system-color-scheme media query
in CSS could not honor that order and is therefore banned.

Manual procedure (the browser half of the theme checks — run by a human
or a browser validator):

1. Start the dashboard (``cairn dashboard``) with an empty localStorage —
   every view renders on the dark ladder (--bg-0 near-black) before
   first paint, whatever the OS preference.
2. Click ``#theme-toggle``: the page flips to the light ladder without a
   reload; reload and navigate — light persists (localStorage key
   ``cairn-theme``), with no flash of the wrong theme before paint.
3. Clear the stored key and flip the OS preference — a fresh load
   follows the OS; toggling again stores an explicit choice that beats
   the OS on the next load.
"""
from __future__ import annotations

import pytest

_THEME_STORAGE_KEY = "cairn-theme"

# Every theme-varying token: the surface ladder, the hairlines, the text
# ramp, the accent pair, and the status colors. Each must appear in the
# first variable block (dark values) and in BOTH theme blocks.
_THEME_TOKENS = (
    "--bg-0",
    "--bg-1",
    "--bg-2",
    "--bg-3",
    "--line-1",
    "--line-2",
    "--text-1",
    "--text-2",
    "--text-3",
    "--text-4",
    "--accent",
    "--accent-hover",
    "--ok",
    "--warn",
    "--err",
)

_DARK_BG_0 = "#08090a"


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


def _stylesheet(tmp_path) -> str:
    resp = _client(tmp_path).get("/static/app.css")
    assert resp.status_code == 200
    return resp.text


def test_rendered_page_carries_theme_toggle(tmp_path):
    """The base template renders the toggle button with its stable id,
    and the script wires itself to that id."""
    html = _rendered(tmp_path)
    assert 'id="theme-toggle"' in html
    assert 'class="theme-toggle"' in html
    assert 'getElementById("theme-toggle")' in html


def test_theme_script_is_inline_in_head_and_applies_before_paint(tmp_path):
    """The apply/persist script is inline in <head> (before <body> can
    paint) and runs at parse time -- its first ``applyTheme();`` call
    precedes any DOMContentLoaded deferral."""
    html = _rendered(tmp_path)
    head_end = html.index("</head>")
    body_start = html.index("<body")
    assert html.index("window.__cairnApplyTheme") < head_end < body_start

    script = _head_script(html)
    assert script.index("applyTheme();") < script.index(
        'document.addEventListener("DOMContentLoaded"'
    )


def test_theme_script_carries_apply_and_persist_contract(tmp_path):
    """The script's contract markers -- the localStorage key, both theme
    values, the prefers-color-scheme default via matchMedia, persistence
    through setItem, application on the document element's data-theme,
    and the global apply function."""
    script = _head_script(_rendered(tmp_path))
    assert f'"{_THEME_STORAGE_KEY}"' in script
    assert '"dark"' in script and '"light"' in script
    assert "matchMedia" in script
    assert "(prefers-color-scheme: dark)" in script
    assert "localStorage.getItem" in script
    assert "localStorage.setItem" in script
    assert "window.__cairnApplyTheme = applyTheme" in script
    assert "documentElement.dataset.theme" in script


def test_first_root_block_holds_only_the_dark_theme_tokens(tmp_path):
    """The first variable block holds every theme token with its dark
    value and NOTHING else -- no constants, no component declarations.
    Dark is the default the pre-paint script falls back to, so the block
    must carry the dark ladder (bg-0 near-black) and the dark color
    scheme."""
    css = _stylesheet(tmp_path)
    root = _declarations(_rule_block(css, ":root"))

    assert set(root) == set(_THEME_TOKENS) | {"color-scheme"}
    assert root["--bg-0"] == _DARK_BG_0
    assert root["color-scheme"] == "dark"


def test_both_theme_blocks_redefine_every_token(tmp_path):
    """Both theme blocks redefine every theme token and set the color
    scheme: the dark block restates the first block's dark values
    verbatim (equal specificity, later block wins, so a stray first-block
    edit cannot leave the dark block stale); the light block carries
    theme-appropriate values -- every token differs from its dark
    value."""
    css = _stylesheet(tmp_path)
    root = _declarations(_rule_block(css, ":root"))
    dark = _declarations(_rule_block(css, '[data-theme="dark"]'))
    light = _declarations(_rule_block(css, '[data-theme="light"]'))

    for token in _THEME_TOKENS:
        assert token in dark, token
        assert dark[token] == root[token], token
        assert token in light, token
        assert light[token] != root[token], token

    assert dark.get("color-scheme") == "dark"
    assert light.get("color-scheme") == "light"


def test_no_system_color_scheme_media_query_in_css(tmp_path):
    """Theme resolution lives in the head script (only the script can
    let a stored choice win before paint), so no system-color-scheme
    media query may appear in the stylesheet."""
    assert "@media (prefers-color-scheme" not in _stylesheet(tmp_path)
