"""htmx list views + live refresh (M2): the filter inputs and the polled
#refresh-region speak fragments.

Two contracts, one route each:

- Filtering is a fragment: the list-view filter forms carry hx-get with
  ``input changed delay:300ms`` (selects: ``change delay:300ms``) and swap
  only the results region, so a keystroke never re-renders the page and
  never navigates — the HX-Request branch of the same route serves the
  region alone.
- Live refresh is htmx polling: the traffic views' #refresh-region carries
  ``hx-trigger="every 5s"`` + ``hx-swap="morph"`` and re-renders itself as
  a cheap fragment, while the filter form lives OUTSIDE the region — so a
  poll can never wipe in-progress filter input (the old full-page
  DOMParse swap needed harvest/restore for the same guarantee; here the
  fields are outside the swap target by construction).

The tests pin the server half (fragment-vs-full-page per route, the
region/form attribute contract, filtered fragments) and the app.js half
of the loop (htmx-event-driven chrome; the module itself never fetches).
The interactive halves — a keystroke's fragment in the network log,
scroll/typing surviving a real poll cycle — are browser checks
(VAL-UI-007 / VAL-UI-008).
"""

from __future__ import annotations

import re
from pathlib import Path

HX = {"HX-Request": "true"}


def _live_loop_js() -> str:
    """The live-refresh module's source in app.js, identified by its
    getElementById("refresh-region") CODE call inside a zero-arg IIFE
    (the opener pattern excludes .catch(function () {...}) handlers)."""
    import cairn.dashboard

    source = (
        Path(cairn.dashboard.__file__)
        .resolve()
        .parent.joinpath("static", "app.js")
        .read_text(encoding="utf-8")
    )
    opener = re.compile(r"(?<![.\w])\(\s*function\s*\(\s*\)\s*\{")
    region_call = re.compile(r'getElementById\(\s*["\']refresh-region["\']')
    for segment in opener.split(source)[1:]:
        if region_call.search(segment):
            return segment
    raise AssertionError("app.js carries no #refresh-region module")


# ---------------------------------------------------------------------------
# Fragment branches (the HX-Request seam on the list routes)
# ---------------------------------------------------------------------------


def test_history_fragment_serves_region_without_page_chrome(tmp_path):
    """An HX-Request /history renders the polled region alone: no html
    shell, no sidebar, no filter form, no live chrome — one cheap region
    render, never a full page. The full page still renders the shell."""
    from tests.test_dashboard_app import _history_client, _refresh_region

    client = _history_client(tmp_path, seed=True)

    fragment = client.get("/history", headers=HX)
    assert fragment.status_code == 200
    assert "<html" not in fragment.text
    assert "<aside" not in fragment.text  # the sidebar stays on the page
    assert "filter-bar" not in fragment.text  # the form stays on the page
    assert "live-controls" not in fragment.text  # chrome stays on the page
    assert fragment.text.count('id="refresh-region"') == 1
    region = _refresh_region(fragment.text)
    assert region is not None
    assert '<table class="data-table">' in region
    assert "ask_compass" in region  # a seeded row renders in the fragment

    page = client.get("/history")
    assert page.status_code == 200
    assert "<aside" in page.text
    assert 'id="refresh-region"' in page.text


def test_history_fragment_honors_filters(tmp_path):
    """The fragment branch reads the same query params as the page: a
    tool-filtered fragment request narrows to that tool's rows only —
    what the live keystroke fragment must serve."""
    from tests.test_dashboard_app import _history_client, _refresh_region

    client = _history_client(tmp_path, seed=True)
    fragment = client.get(
        "/history", params={"tool": "explore"}, headers=HX
    )
    assert fragment.status_code == 200
    region = _refresh_region(fragment.text)
    assert region is not None
    assert "2025-08-20 00:20:00 UTC" in region
    assert "2025-08-20 00:00:00 UTC" in region
    assert "ask_compass" not in region
    assert "legacy_tool" not in region


def test_tokens_and_chains_fragments_serve_their_regions(tmp_path):
    """Both remaining traffic views fragment the same way: an HX-Request
    renders #refresh-region with the view's data and none of the shell."""
    from tests.test_dashboard_app import (
        _refresh_region,
        _tokens_chains_client,
    )

    client = _tokens_chains_client(tmp_path, seed=True)
    for path, marker, seeded in (
        ("/tokens", '<table class="data-table">', "tool_heavy"),
        ("/chains", '<div class="chain-list">', "sess-multi"),
    ):
        fragment = client.get(path, headers=HX)
        assert fragment.status_code == 200, path
        assert "<aside" not in fragment.text, path
        assert "live-controls" not in fragment.text, path
        assert fragment.text.count('id="refresh-region"') == 1, path
        region = _refresh_region(fragment.text)
        assert region is not None, path
        assert marker in region, path
        assert seeded in region, path


def test_tasks_fragment_honors_status_filter(tmp_path):
    """The /tasks status select swaps only the results region: an
    HX-Request with ?status= renders that region alone, filtered."""
    from tests.test_dashboard_app import (
        _graph_db_file,
        _panel_client,
        _seed_tasks,
    )

    kdir = tmp_path / "knowledge"
    pending, claimed, done = _seed_tasks(kdir)
    client = _panel_client(
        tmp_path, _graph_db_file(tmp_path, seed=False), str(kdir)
    )

    fragment = client.get("/tasks", params={"status": "pending"}, headers=HX)
    assert fragment.status_code == 200
    assert "<aside" not in fragment.text
    assert 'id="tasks-results"' in fragment.text
    assert pending.id in fragment.text  # the pending row renders
    assert claimed.id not in fragment.text  # in-progress: filtered out
    assert done.id not in fragment.text  # done: filtered out

    page = client.get("/tasks", params={"status": "pending"})
    assert page.status_code == 200
    assert "<aside" in page.text
    assert pending.id in page.text


def test_wiki_fragment_honors_search_filter(tmp_path):
    """The /wiki search input swaps only the results region: an
    HX-Request with ?q= renders the filtered grouped list alone."""
    from tests.test_dashboard_app import _wiki_client

    client = _wiki_client(tmp_path)

    fragment = client.get("/wiki", params={"q": "viz"}, headers=HX)
    assert fragment.status_code == 200
    assert "<aside" not in fragment.text
    assert 'id="wiki-results"' in fragment.text
    assert "viz module" in fragment.text
    assert "demo architecture overview" not in fragment.text
    assert "tasks module" not in fragment.text

    page = client.get("/wiki", params={"q": "viz"})
    assert page.status_code == 200
    assert "<aside" in page.text
    assert 'id="wiki-results"' in page.text


# ---------------------------------------------------------------------------
# Page contracts: the region + form attribute wiring the client rides
# ---------------------------------------------------------------------------


def test_history_page_polls_region_and_filter_form_swaps_it(tmp_path):
    """The polling contract lives in the DOM: #refresh-region re-fetches
    itself every 5s and morphs (no rebuild, poller element survives), the
    filter form hx-gets the same route with the delay:300ms input trigger
    and targets the region, and the region includes the form's live fields
    on each poll. The form itself sits OUTSIDE the region: a swap can
    never touch a field, so in-progress filter input survives polls and
    filter swaps by construction."""
    from tests.test_dashboard_app import _history_client, _refresh_region

    resp = _history_client(tmp_path, seed=True).get("/history")
    assert resp.status_code == 200

    region_tag = re.search(r'<div id="refresh-region"[^>]*>', resp.text)
    assert region_tag, "the polled region div is missing"
    tag = region_tag.group(0)
    assert 'hx-get="/history"' in tag
    assert 'hx-trigger="every 5s"' in tag
    assert 'hx-swap="morph"' in tag
    assert 'hx-include="#history-filters"' in tag
    assert "aria-live" in tag

    form_tag = re.search(r'<form class="filter-bar"[^>]*>', resp.text)
    assert form_tag, "the history filter form is missing"
    form = form_tag.group(0)
    assert 'id="history-filters"' in form
    assert 'hx-get="/history"' in form
    assert 'hx-target="#refresh-region"' in form
    assert 'hx-swap="morph"' in form
    assert "delay:300ms" in form

    # The form is not inside the swap target: no poll or filter swap can
    # ever destroy a field the user is typing into.
    region = _refresh_region(resp.text)
    assert region is not None
    assert "<form" not in region
    assert 'name="tool"' not in region
    assert 'name="window"' not in region

    # The window control stays inside the region, so its links re-render
    # with the live filter context each fragment.
    assert "window-control" in region


def test_tokens_and_chains_regions_poll_their_own_url(tmp_path):
    """The formless traffic views bake the view's current params into the
    region's poll URL (no form to include): ?window=24h on /tokens and
    ?expand= on /chains keep polling the same slice they rendered."""
    from tests.test_dashboard_app import _tokens_chains_client

    client = _tokens_chains_client(tmp_path, seed=True)

    windowed = client.get("/tokens", params={"window": "24h"})
    assert windowed.status_code == 200
    tag = re.search(r'<div id="refresh-region"[^>]*>', windowed.text)
    assert tag
    assert 'hx-get="/tokens?window=24h"' in tag.group(0)
    assert 'hx-trigger="every 5s"' in tag.group(0)
    assert 'hx-swap="morph"' in tag.group(0)

    plain = client.get("/tokens")
    tag = re.search(r'<div id="refresh-region"[^>]*>', plain.text)
    assert tag
    assert 'hx-get="/tokens"' in tag.group(0)  # 'all' window: bare URL

    expanded = client.get("/chains", params={"expand": "sess-multi"})
    assert expanded.status_code == 200
    tag = re.search(r'<div id="refresh-region"[^>]*>', expanded.text)
    assert tag
    assert 'hx-get="/chains?expand=sess-multi"' in tag.group(0)


def test_tasks_page_filter_select_swaps_results_region(tmp_path):
    """The /tasks status select is an htmx trigger: the form hx-gets the
    route on change (delay:300ms) and morphs #tasks-results; the select
    sits outside the region it swaps."""
    from tests.test_dashboard_app import (
        _graph_db_file,
        _panel_client,
        _seed_tasks,
    )

    kdir = tmp_path / "knowledge"
    _seed_tasks(kdir)
    client = _panel_client(
        tmp_path, _graph_db_file(tmp_path, seed=False), str(kdir)
    )

    resp = client.get("/tasks")
    assert resp.status_code == 200
    form_tag = re.search(r'<form class="filter-bar"[^>]*>', resp.text)
    assert form_tag, "the tasks filter form is missing"
    form = form_tag.group(0)
    assert 'hx-get="/tasks"' in form
    assert 'hx-target="#tasks-results"' in form
    assert 'hx-swap="morph"' in form
    assert "delay:300ms" in form

    region_tag = re.search(r'<div id="tasks-results"[^>]*>', resp.text)
    assert region_tag, "the tasks results region is missing"
    assert "aria-live" in region_tag.group(0)
    assert "<form" not in region_tag.group(0)


def test_wiki_page_search_swaps_results_region(tmp_path):
    """The /wiki search input is an htmx trigger: hx-get on input
    (delay:300ms) morphs #wiki-results; the state-filter links and the
    form stay outside the region."""
    from tests.test_dashboard_app import _wiki_client

    resp = _wiki_client(tmp_path).get("/wiki")
    assert resp.status_code == 200

    form_tag = re.search(r'<form class="filter-bar"[^>]*>', resp.text)
    assert form_tag, "the wiki search form is missing"
    form = form_tag.group(0)
    assert 'hx-get="/wiki"' in form
    assert 'hx-target="#wiki-results"' in form
    assert 'hx-swap="morph"' in form
    assert "delay:300ms" in form

    region_tag = re.search(r'<div id="wiki-results"[^>]*>', resp.text)
    assert region_tag, "the wiki results region is missing"
    assert "aria-live" in region_tag.group(0)
    assert "<form" not in region_tag.group(0)
    assert 'name="q"' not in region_tag.group(0)


# ---------------------------------------------------------------------------
# app.js live-refresh module: htmx-event-driven chrome
# (the old structural pins for the DOMParse poll loop lived in
# test_dashboard_app.py; the loop they pinned is replaced by this one)
# ---------------------------------------------------------------------------


def test_live_loop_is_htmx_event_driven_and_never_fetches():
    """The module owns only the chrome around the poll: it listens on the
    htmx event lifecycle and never transports data itself — no fetch, no
    DOMParser, no importNode (the full-page DOMParse hack's tools are
    gone; htmx + the alpine-morph swap do that work)."""
    loop = _live_loop_js()
    assert re.search(r'addEventListener\(\s*["\']htmx:beforeRequest["\']', loop)
    assert re.search(r'addEventListener\(\s*["\']htmx:afterRequest["\']', loop)
    assert not re.search(r"\bfetch\s*\(", loop)
    assert "DOMParser" not in loop
    assert "importNode" not in loop
    assert "XMLHttpRequest" not in loop


def test_live_loop_paused_and_hidden_guards_cancel_the_poll():
    """FR-004 / D-003: inside the beforeRequest listener the paused guard
    leads — ahead of the transport call — so a paused loop issues no
    fetch regardless of tab state, and a hidden tab fetches nothing; both
    refuse the request while htmx's own timer keeps its schedule, so the
    next unpaused/visible tick catches up."""
    loop = _live_loop_js()
    listener = re.search(
        r'addEventListener\(\s*["\']htmx:beforeRequest["\']', loop
    )
    assert listener, "the module never refuses poll requests"
    body = loop[listener.end():]

    target_guard = re.search(r"fromRegion\(", body)
    guard = re.search(r"if\s*\(\s*paused\s*\|\|\s*document\.hidden\s*\)", body)
    cancel = re.search(r"preventDefault\(\s*\)", body)
    assert target_guard, "the listener must scope to the polled region"
    assert guard, "the paused||hidden guard is missing"
    assert cancel, "the guard must refuse the request, not just note it"
    assert target_guard.start() < guard.start() < cancel.start()


def test_live_loop_pause_toggles_words_resume_restores_live():
    """FR-004: the pause toggle is the state machine's pause half — it
    lands the 'paused' word and flips the button to Resume; the resume
    half restores 'running' (the 'live' word)."""
    loop = _live_loop_js()

    words = re.search(r"STATE_WORDS\s*=\s*\{(.*?)\}", loop, re.S)
    assert words, "the module's STATE_WORDS table is missing"
    for key, word in (
        ("running", "live"),
        ("disconnected", "disconnected"),
        ("paused", "paused"),
    ):
        assert re.search(rf"{key}\s*:\s*[\"']{word}[\"']", words.group(1)), (
            f"STATE_WORDS lost the {key} -> {word!r} mapping"
        )

    click = re.search(r'addEventListener\(\s*["\']click["\']', loop)
    set_paused = re.search(r'setState\(\s*["\']paused["\']\s*\)', loop)
    assert click and set_paused, "the pause toggle never lands 'paused'"
    assert click.start() < set_paused.start()

    after = loop[set_paused.end():]
    set_running = re.search(r'setState\(\s*["\']running["\']\s*\)', after)
    assert set_running, "the resume path never restores the 'running' state"
    assert "Resume" in after[: set_running.end()], (
        "the pause half never flips the button label to Resume"
    )


def test_live_loop_failure_sets_disconnected_success_restores_running():
    """FR-005: the rejected-then-resolved transitions — a failed poll
    raises the disconnected banner and word (self-healing: htmx's timer
    keeps ticking, so recovery needs no reload), while the next
    successful poll clears the banner and restores 'running'."""
    loop = _live_loop_js()

    after = re.search(r'addEventListener\(\s*["\']htmx:afterRequest["\']', loop)
    errors = re.search(r"htmx:responseError", loop)
    assert after and errors, "the success/failure listeners are missing"

    success_window = loop[after.end():errors.start()]
    assert re.search(r"\bsuccessful\b", success_window)
    assert re.search(r'setState\(\s*["\']running["\']\s*\)', success_window), (
        "a successful poll never restores 'running'"
    )
    assert re.search(r'banner\.textContent\s*=\s*["\']["\']', success_window), (
        "a successful poll never clears the disconnect banner"
    )

    error_window = loop[errors.start():]
    assert "htmx:sendError" in error_window, (
        "a dead server (no response) never reaches the disconnected state"
    )
    assert re.search(
        r'setState\(\s*["\']disconnected["\']\s*\)', error_window
    ), "a failed poll never sets 'disconnected'"
    assert "connection lost — retrying" in error_window


def test_live_loop_restores_window_scroll_around_the_region_swap():
    """#refresh-region semantics: the module anchors the page across each
    region swap — scroll captured in the before-swap listener, restored
    in the after-swap one — so a shrinking fragment cannot pull the page
    out from under the reader (typing is safe by construction: the form
    lives outside the region)."""
    loop = _live_loop_js()

    before = re.search(r'addEventListener\(\s*["\']htmx:beforeSwap["\']', loop)
    after = re.search(r'addEventListener\(\s*["\']htmx:afterSwap["\']', loop)
    assert before and after, "the swap scroll anchors are missing"

    harvest = re.search(r"scrollY\s*=\s*window\.scrollY", loop)
    restore = re.search(r"window\.scrollTo\(", loop)
    assert harvest, "the before-swap listener never captures scroll"
    assert restore, "the after-swap listener never restores scroll"
    assert (
        before.start() < harvest.start() < after.start() < restore.start()
    ), "scroll must be captured before the swap and restored after it"


def test_traffic_views_load_app_js_once_and_others_do_not(tmp_path):
    """The loop module arms only where a region exists: the three traffic
    views load app.js exactly once; the fragment views without polling
    (tasks, wiki) need no module — their swaps are pure htmx attributes."""
    from tests.test_dashboard_app import (
        _graph_db_file,
        _history_client,
        _panel_client,
        _seed_tasks,
        _tokens_chains_client,
        _wiki_client,
    )

    for client, path in (
        (_history_client(tmp_path, seed=True), "/history"),
        (_tokens_chains_client(tmp_path, seed=True), "/tokens"),
        (_tokens_chains_client(tmp_path, seed=True), "/chains"),
    ):
        resp = client.get(path)
        assert resp.status_code == 200, path
        loads = re.findall(r'<script[^>]*\ssrc="[^"]*app\.js[?"]', resp.text)
        assert len(loads) == 1, path

    kdir = tmp_path / "knowledge"
    _seed_tasks(kdir)
    for client, path in (
        (
            _panel_client(
                tmp_path, _graph_db_file(tmp_path, seed=False), str(kdir)
            ),
            "/tasks",
        ),
        (_wiki_client(tmp_path), "/wiki"),
    ):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "app.js" not in resp.text, path
