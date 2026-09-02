"""Unit tests for the dashboard shell context (cairn.dashboard.shell).

Pure-function tests, no HTTP: the nav-as-data contract (sidebar and
palette render from one source, hrefs ride the store like the old
hand-written anchors) and the workspace label policy.
"""

from cairn.dashboard.shell import (
    NAV_LABELS,
    NAV_SECTIONS,
    shell_context,
    workspace_label,
)


def _stores():
    return [
        {"key": "aaaaaaaaaaaaaaaa", "path": "/workspaces/alpha", "state": "populated"},
        {"key": "bbbbbbbbbbbbbbbb", "path": None, "state": "populated"},
        {"key": "cccccccccccccccc", "path": "/workspaces/empty-one", "state": "empty"},
        {"key": "dddddddddddddddd", "path": "/workspaces/gone", "state": "missing"},
    ]


def test_workspace_label_prefers_registered_path_basename():
    assert workspace_label("/workspaces/alpha", "aaaaaaaaaaaaaaaa") == "alpha"
    # Trailing slash, deep paths, root-only paths.
    assert workspace_label("/workspaces/alpha/", "k") == "alpha"
    assert workspace_label("/a/b/c/proj", "k") == "proj"
    assert workspace_label("", "bbbbbbbbbbbbbbbb") == "bbbbbbbbbbbbbbbb"
    assert workspace_label(None, "bbbbbbbbbbbbbbbb") == "bbbbbbbbbbbbbbbb"
    # A path that is only slashes has no basename: the key stands in.
    assert workspace_label("/", "k") == "k"


def test_nav_sections_cover_every_view_exactly_once():
    ids = [v for _, views in NAV_SECTIONS for v in views]
    assert sorted(ids) == sorted(NAV_LABELS)
    assert len(ids) == len(set(ids)) == 13


def test_shell_context_groups_nav_and_flags_active_by_path():
    ctx = shell_context(_stores(), "", "/graph")

    labels = [s["label"] for s in ctx["nav"]["sections"]]
    # Workspaces leads ungrouped (FR-001's overview-first contract), then
    # the scoped groups.
    assert labels == [None, "Explore", "Knowledge", "Activity", "System"]

    graph = ctx["nav"]["sections"][1]["items"][1]
    assert graph == {
        "id": "graph",
        "label": "Graph",
        "href": "/graph",
        "active": True,
    }
    # Exactly one active item, and only for the matching path prefix.
    active = [
        item
        for section in ctx["nav"]["sections"]
        for item in section["items"]
        if item["active"]
    ]
    assert [item["id"] for item in active] == ["graph"]

    # A wiki detail path still flags the wiki item (startswith, the old
    # hand-written rule).
    ctx = shell_context(_stores(), "", "/wiki/demo/overview")
    active = [
        item
        for section in ctx["nav"]["sections"]
        for item in section["items"]
        if item["active"]
    ]
    assert [item["id"] for item in active] == ["wiki"]


def test_shell_context_hrefs_ride_the_selected_store():
    ctx = shell_context(_stores(), "aaaaaaaaaaaaaaaa", "/projects")

    graph = ctx["nav"]["sections"][1]["items"][1]
    assert graph["href"] == "/graph?store=aaaaaaaaaaaaaaaa"
    # The palette's view list carries the same store-carrying hrefs.
    palette_graph = next(v for v in ctx["palette"]["views"] if v["label"] == "Graph")
    assert palette_graph["href"] == "/graph?store=aaaaaaaaaaaaaaaa"


def test_shell_context_palette_lists_populated_workspaces_only():
    ctx = shell_context(_stores(), "", "/")

    keys = [w["key"] for w in ctx["palette"]["workspaces"]]
    assert keys == ["aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"]  # populated only
    labels = [w["label"] for w in ctx["palette"]["workspaces"]]
    assert labels == ["alpha", "bbbbbbbbbbbbbbbb"]  # basename, key fallback


def test_shell_context_selector_lists_populated_stores_only():
    ctx = shell_context(_stores(), "bbbbbbbbbbbbbbbb", "/")

    assert ctx["selector"]["selected"] == "bbbbbbbbbbbbbbbb"
    assert [o["key"] for o in ctx["selector"]["options"]] == [
        "aaaaaaaaaaaaaaaa",
        "bbbbbbbbbbbbbbbb",
    ]
