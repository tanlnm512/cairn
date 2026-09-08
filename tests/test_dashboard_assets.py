"""Vendored interaction stack: htmx/Alpine assets, local-only script
graph, htmx CSP config, and the HX-Request fragment pattern (M2 UI
foundation).

The dashboard is zero-network at runtime: every script and stylesheet the
shell loads is a vendored file served from /static/ with the ?v= mtime
stamp — no CDN, whatever the view. htmx owns server interaction, Alpine
owns client state; htmx runs with allowEval off, and Alpine-managed DOM
stays hidden ([x-cloak]) until Alpine initializes. Routes tell htmx
fragments from full page loads via the HX-Request header (is_hx_request).
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

# The fourteen main views — every page the shell renders must reference
# local assets only.
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

# Vendored library files and the marker that must survive in the shipped
# bytes — the pins the mission vendored, re-checked at render time. The
# @alpinejs/morph build carries no version string, so it is pinned by its
# registration contract instead.
_VENDORED_VERSIONS = {
    "htmx.min.js": 'version:"2.0.10"',
    "alpine.min.js": "3.17.1",
    "alpinejs-morph.min.js": "window.Alpine.plugin",
}

# Files the interaction stack needs on disk (alpine-morph.js is the htmx
# extension source — unminified, no version string to pin).
_VENDORED_FILES = ("alpine-morph.js",) + tuple(_VENDORED_VERSIONS)

_SCRIPT_SRC_RE = re.compile(r"<script[^>]*\ssrc=\"([^\"]+)\"")
_LINK_HREF_RE = re.compile(r"<link[^>]*\shref=\"([^\"]+)\"")


def _static_dir() -> Path:
    import cairn.dashboard

    return (
        Path(cairn.dashboard.__file__).resolve().parent / "static"
    )


def _client(tmp_path):
    """A client over a schema-complete, empty store: every main view
    renders the real shell (no missing-DB fallback page)."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app
    from cairn.graph.schema import _apply_schema

    db_path = str(tmp_path / "assets.db")
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
# Vendored files on disk
# ---------------------------------------------------------------------------


def test_vendored_libraries_ship_pinned_versions():
    """The vendored interaction libraries are present and are the pinned
    builds — a swapped or truncated file fails here, not in the browser."""
    for name in _VENDORED_FILES:
        asset = _static_dir() / name
        assert asset.is_file(), name
        assert asset.stat().st_size > 0, name
    for name, marker in _VENDORED_VERSIONS.items():
        assert marker in (_static_dir() / name).read_text(encoding="utf-8"), name
    morph_ext = (_static_dir() / "alpine-morph.js").read_text(encoding="utf-8")
    assert "defineExtension('alpine-morph'" in morph_ext


def test_static_mount_serves_vendored_assets(tmp_path):
    """The /static mount serves every vendored file as JavaScript."""
    client = _client(tmp_path)
    for name in _VENDORED_FILES:
        resp = client.get(f"/static/{name}")
        assert resp.status_code == 200, name
        assert "javascript" in resp.headers["content-type"], name
        assert len(resp.content) > 0, name


# ---------------------------------------------------------------------------
# Local-only script graph across every main view
# ---------------------------------------------------------------------------


def _asset_urls(html: str) -> list:
    return _SCRIPT_SRC_RE.findall(html) + _LINK_HREF_RE.findall(html)


def _is_local(url: str) -> bool:
    """Same-origin or root-relative — url_for renders absolute URLs off
    the request host, and that host is the loopback dashboard in every
    real render; anything else is a CDN pull."""
    if url.startswith("/"):
        return True
    from urllib.parse import urlparse

    host = urlparse(url).hostname or ""
    return url.startswith(("http://", "https://")) and host in (
        "127.0.0.1",
        "localhost",
        "testserver",  # TestClient's name for the local origin
    )


def test_every_main_view_references_only_local_assets(tmp_path):
    """Zero-network at the source level: every script and stylesheet URL
    on every main view targets the loopback dashboard itself, and no page
    text carries a CDN host — the page source can never pull a remote
    asset."""
    client = _client(tmp_path)
    for path in _MAIN_VIEWS:
        resp = client.get(path)
        assert resp.status_code == 200, path
        for url in _asset_urls(resp.text):
            assert _is_local(url), (path, url)
        for banned in ("jsdelivr", "unpkg", "cdnjs", "googleapis"):
            assert banned not in resp.text, (path, banned)


def test_vendored_stack_referenced_once_with_cache_busting(tmp_path):
    """Each vendored file is wired into the shell exactly once, through
    the ?v= mtime cache-buster the other assets use."""
    html = _client(tmp_path).get("/").text
    for name in _VENDORED_FILES:
        loads = re.findall(
            r'<script defer src="[^"]*/static/' + re.escape(name) + r'\?v=\d+">',
            html,
        )
        assert len(loads) == 1, (name, loads)


def test_alpine_loads_deferred(tmp_path):
    """Alpine's install contract: the core bundle loads with defer, so it
    initializes after the document is parsed (no Alpine-unready flashes)."""
    html = _client(tmp_path).get("/").text
    assert re.search(
        r'<script defer src="[^"]*/static/alpine\.min\.js\?v=\d+"></script>',
        html,
    )


def test_htmx_extension_loads_after_htmx_core(tmp_path):
    """Deferred scripts execute in document order, so the alpine-morph
    extension's defineExtension call sees the htmx global only if its tag
    follows htmx core's."""
    html = _client(tmp_path).get("/").text
    assert html.index("/static/htmx.min.js") < html.index(
        "/static/alpine-morph.js"
    )


# ---------------------------------------------------------------------------
# htmx CSP config + Alpine x-cloak
# ---------------------------------------------------------------------------


def test_htmx_config_meta_disallows_eval(tmp_path):
    """htmx runs CSP-tight: the htmx-config meta turns allowEval off, so
    hx-on:/js: prefixed attributes never evaluate code."""
    html = _client(tmp_path).get("/").text
    # The config JSON carries double quotes, so the attribute is always
    # single-quoted — the class form [^'"] would stop mid-value.
    match = re.search(
        r"<meta name=\"htmx-config\" content='([^']+)'>", html
    )
    assert match, "htmx-config meta missing from the shell head"
    assert '"allowEval":false' in match.group(1).replace(" ", "")


def test_x_cloak_rule_hides_pre_alpine_dom(tmp_path):
    """Alpine-managed DOM marked x-cloak stays hidden until Alpine
    initializes and strips the attribute: the stylesheet carries the
    display:none rule."""
    css = _client(tmp_path).get("/static/app.css").text
    assert re.search(r"\[x-cloak\][^}]*display:\s*none", css)


# ---------------------------------------------------------------------------
# HX-Request fragment pattern
# ---------------------------------------------------------------------------


def _request(headers):
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (key.lower().encode("latin-1"), value.encode("latin-1"))
                for key, value in headers.items()
            ],
            "query_string": b"",
        }
    )


def test_is_hx_request_tells_htmx_calls_from_page_loads():
    """The fragment/full-page branch rides exactly the header htmx sends:
    HX-Request: true is a fragment request, its absence a page load."""
    from cairn.dashboard.app import is_hx_request

    assert is_hx_request(_request({"HX-Request": "true"})) is True
    assert is_hx_request(_request({})) is False
