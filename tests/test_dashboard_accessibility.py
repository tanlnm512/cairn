"""Accessibility contract for the M2 dashboard (m2-accessibility).

Pinned invariants:

- Keyboard focus is always visible: the stylesheet keeps a global
  ``:focus-visible`` ring (2px accent outline) and no rule anywhere
  removes the focus outline (``outline: none`` is banned, so a
  component style cannot silently delete the indication).
- Icon-only controls carry non-empty accessible names on every main
  view; no button renders as an unlabeled glyph.
- ``prefers-reduced-motion: reduce`` collapses all motion to ~zero —
  transitions and animations run at 0s and the looping pulse stops —
  while leaving the interactive behavior itself untouched.
- The shared table macro emits real table semantics: one
  ``<table>`` with ``<thead>`` / ``<tbody>`` and column-scoped header
  cells (``<th scope="col">``), never a div-grid.
"""
from __future__ import annotations

import re
import sqlite3
from html.parser import HTMLParser
from pathlib import Path

import pytest

# The sixteen main views (same inventory the asset tests crawl).
_MAIN_VIEWS = (
    "/",
    "/workspaces",
    "/projects",
    "/graph",
    "/history",
    "/tokens",
    "/chains",
    "/health",
    "/knowledge",
    "/knowledge/graph",
    "/memory",
    "/tasks",
    "/wiki",
    "/embeddings",
    "/database",
    "/settings",
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

    db_path = str(tmp_path / "a11y.db")
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
    """A client over a store seeded with one project/file/symbol so the
    data-bearing views render their tables and controls."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app
    from cairn.graph.schema import _apply_schema

    db_path = str(tmp_path / "a11y-seeded.db")
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


def _stylesheet(tmp_path) -> str:
    resp = _client(tmp_path).get("/static/app.css")
    assert resp.status_code == 200
    return resp.text


def _balanced_block(css: str, open_brace_idx: int) -> str:
    """The body of the brace-balanced block opening at (or containing)
    ``open_brace_idx`` — media queries nest rules, so a naive
    first-``}`` slice would truncate them."""
    depth = 0
    for i in range(open_brace_idx, len(css)):
        if css[i] == "{":
            depth += 1
        elif css[i] == "}":
            depth -= 1
            if depth == 0:
                return css[open_brace_idx + 1 : i]
    raise AssertionError("unbalanced braces in stylesheet")


# ---------------------------------------------------------------------------
# Visible keyboard focus
# ---------------------------------------------------------------------------


def test_global_focus_visible_ring_is_the_accent_outline(tmp_path):
    """The stylesheet keeps a global ``:focus-visible`` rule: a 2px
    accent outline with an offset, so every interactive control shows a
    ring in both themes without needing its own rule."""
    css = _stylesheet(tmp_path)
    match = re.search(r":focus-visible\s*\{([^}]*)\}", css)
    assert match, "no global :focus-visible rule"
    block = match.group(1)
    assert "outline: 2px solid var(--accent)" in block
    assert "outline-offset" in block
    # Focus must not mutate the control's own shape (the ring follows
    # the element's real radius; overriding it snaps corners on focus).
    assert "border-radius" not in block


def test_no_rule_removes_the_focus_outline(tmp_path):
    """``outline: none`` is banned outright: focus indication is the
    keyboard user's only location feedback, so no component style may
    delete it (a component needing a different ring restyles the
    outline, it never removes it)."""
    css = _stylesheet(tmp_path)
    for spelling in ("outline: none", "outline:none", "outline: 0", "outline:0"):
        assert spelling not in css, spelling


def test_filter_inputs_and_palette_input_keep_visible_rings(tmp_path):
    """The two historically outline-killed controls — the shared filter
    bar's inputs/selects and the palette search input — carry visible
    focus indication: the filter rule keeps its border/shadow tint
    without an outline kill, and the palette input states its own inset
    ring (the card's overflow clips an outward one)."""
    css = _stylesheet(tmp_path)

    bar_idx = css.index(".filter-bar input:focus")
    bar_rule = _balanced_block(css, bar_idx)
    assert "outline" not in bar_rule
    assert "border-color: var(--accent)" in bar_rule

    palette_idx = css.index(".palette-card input:focus")
    palette_rule = _balanced_block(css, palette_idx)
    assert "outline: 2px solid var(--accent)" in palette_rule
    assert "outline-offset: -2px" in palette_rule


# ---------------------------------------------------------------------------
# Accessible names on icon-only controls
# ---------------------------------------------------------------------------


class _ButtonNameAudit(HTMLParser):
    """Minimal DOM walk for accessible-name checks: every id'd
    element's inner text (for aria-labelledby resolution) and every
    ``<button>`` with its naming attributes. ``aria-hidden`` subtrees
    contribute no button text (an icon glyph must not become a name).
    """

    _VOID = frozenset(
        "area base br col embed hr img input link meta param source "
        "track wbr".split()
    )

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.id_text: dict[str, list[str]] = {}
        self.buttons: list[dict] = []
        self._stack: list[tuple[str, str | None]] = []
        self._hidden_at: list[int] = []
        self._button: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if a.get("aria-hidden") == "true" and tag not in self._VOID:
            self._hidden_at.append(len(self._stack))
        if tag in self._VOID:
            return
        self._stack.append((tag, a.get("id")))
        if a.get("id"):
            self.id_text.setdefault(a["id"], [])
        if tag == "button":
            self._button = {
                "id": a.get("id", ""),
                "class": a.get("class", ""),
                "aria-label": a.get("aria-label", ""),
                "aria-labelledby": a.get("aria-labelledby", ""),
                "title": a.get("title", ""),
                "value": a.get("value", ""),
                "text": [],
            }
            self.buttons.append(self._button)

    def handle_endtag(self, tag):
        if not self._stack:
            return
        popped_tag, _element_id = self._stack.pop()
        if self._hidden_at and len(self._stack) == self._hidden_at[-1]:
            self._hidden_at.pop()
        if popped_tag == "button":
            self._button = None

    def handle_startendtag(self, tag, attrs):
        # Self-closing svg shapes balance via start+end; nothing to do
        # beyond the default, but the default double-calls handle_data
        # paths we do not need, so keep it explicit and inert.
        return

    def handle_data(self, data):
        if self._hidden_at:
            return
        for _tag, element_id in self._stack:
            if element_id:
                self.id_text[element_id].append(data)
        if self._button is not None:
            self._button["text"].append(data)


def _audit_html(html: str) -> _ButtonNameAudit:
    audit = _ButtonNameAudit()
    audit.feed(html)
    return audit


def _accessible_name(audit: _ButtonNameAudit, button: dict) -> str:
    """The button's accessible name per the accname precedence used
    here: aria-labelledby, aria-label, then visible text, then title,
    then value."""
    if button["aria-labelledby"].strip():
        parts = []
        for ref in button["aria-labelledby"].split():
            parts.append("".join(audit.id_text.get(ref, [])))
        name = " ".join(p.strip() for p in parts if p.strip())
        if name:
            return name
    if button["aria-label"].strip():
        return button["aria-label"].strip()
    text = "".join(button["text"]).strip()
    if text:
        return text
    if button["value"].strip():
        return button["value"].strip()
    if button["title"].strip():
        return button["title"].strip()
    return ""


@pytest.mark.parametrize("path", _MAIN_VIEWS)
def test_every_button_has_a_non_empty_accessible_name(tmp_path, path):
    """On every main view, each rendered ``<button>`` — icon-only ones
    included — resolves a non-empty accessible name; an unlabeled glyph
    button is invisible to assistive tech."""
    resp = _seeded_client(tmp_path).get(path)
    assert resp.status_code == 200, path
    audit = _audit_html(resp.text)
    assert audit.buttons, f"{path}: no buttons rendered"
    unnamed = [
        b["id"] or b["class"] or f"button[{i}]"
        for i, b in enumerate(audit.buttons)
        if not _accessible_name(audit, b)
    ]
    assert unnamed == [], f"{path}: buttons without accessible names"


def test_icon_only_shell_buttons_render_state_labels(tmp_path):
    """The shell's icon-only controls render their names server-side
    (theme toggle, sidebar collapse, palette open), and the collapse
    button's script flips the label with the state (Expand/Collapse)."""
    html = _client(tmp_path).get("/").text
    assert 'id="theme-toggle"' in html
    assert "aria-label=" in html.split('id="theme-toggle"', 1)[1][:200]
    collapse = html.split('id="sidebar-collapse"', 1)[1][:200]
    assert 'aria-label="Collapse sidebar"' in collapse

    shell_js = (_dashboard_dir() / "static" / "shell.js").read_text(
        encoding="utf-8"
    )
    assert '"Expand sidebar"' in shell_js
    assert '"Collapse sidebar"' in shell_js


def test_palette_search_input_is_labeled(tmp_path):
    """The palette's search input carries its own accessible name (it
    has no visible <label>; the placeholder does not name it)."""
    html = _client(tmp_path).get("/").text
    match = re.search(r'<input id="palette-input"[^>]*>', html)
    assert match, "palette input not rendered"
    assert "aria-label=" in match.group(0)


# ---------------------------------------------------------------------------
# Reduced motion
# ---------------------------------------------------------------------------


def _reduced_motion_block(tmp_path) -> str:
    css = _stylesheet(tmp_path)
    marker = "@media (prefers-reduced-motion: reduce)"
    assert marker in css, "no reduced-motion media query"
    return _balanced_block(css, css.index(marker))


def test_reduced_motion_collapses_transitions_and_animations(tmp_path):
    """Under prefers-reduced-motion the stylesheet zeroes motion:
    scroll behavior stops smoothing, every transition/animation runs at
    0s duration with no delay, looping animations do not repeat, and
    the collapse outranks component rules (!important — a bare
    universal rule loses to any single-class transition)."""
    block = _reduced_motion_block(tmp_path)
    assert "scroll-behavior: auto" in block
    assert "transition-duration: 0s" in block
    assert "animation-duration: 0s" in block
    assert "transition-delay: 0s" in block
    assert "animation-iteration-count: 1" in block
    assert block.count("!important") >= 4


def test_reduced_motion_stops_the_looping_indicator(tmp_path):
    """The live-refresh pulse loops forever by design, so reduced
    motion stops it outright (``animation: none``) instead of merely
    running a zero-length loop once."""
    block = _reduced_motion_block(tmp_path)
    assert "animation: none" in block


# ---------------------------------------------------------------------------
# Real table semantics
# ---------------------------------------------------------------------------


def test_table_macro_emits_column_scoped_headers():
    """The shared table macro's header cells are column-scoped ``th``s
    inside a real thead — the macro is the one place table markup is
    produced, so the semantics are pinned at the source."""
    src = (_dashboard_dir() / "templates" / "_table.html").read_text(
        encoding="utf-8"
    )
    assert "<table" in src
    assert "<thead>" in src
    assert "<tbody>" in src
    assert '<th scope="col">' in src


def test_rendered_data_tables_keep_real_semantics(tmp_path):
    """A data-bearing view renders the macro's exact semantics: one
    table element with thead/tbody and column-scoped header cells —
    not a div grid, not a borderless header-less grid."""
    resp = _seeded_client(tmp_path).get("/projects")
    assert resp.status_code == 200
    html = resp.text
    assert '<table class="data-table">' in html
    assert "<thead>" in html
    assert '<th scope="col">' in html
    assert "<tbody>" in html
