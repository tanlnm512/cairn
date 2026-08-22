"""Workspaces-overview render-budget tests over a synthesized 220-store
machine (TC-006 / FR-005 / SC-1, spec ui-dashboard-workspace-launcher).

FR-005's 200+ store scenario is grounded in a proven leak shape, not the
current registry — no 200-store machine exists locally — so the home is
synthesized here: 210 populated stores, 6 empty key dirs, 4 registered-
missing tails, registry mapping a subset (60 populated + the missing 4).

Synthesis honesty (sanctioned by the task): ONE template store is built
through the real ``get_db`` (full schema apply + ``tool_metrics`` rows),
then byte-copied into the other store dirs. The probe never writes and
only ``os.stat``s each ``.kg`` plus one read-only ``COUNT(*)`` open for a
bounded prefix — to that probe a byte-identical copy is indistinguishable
from a freshly built store. Measured cost: template build ~0.01s, 210
copies ~0.13s, full 210-store probe ~0.08s; the shape test still opens
EVERY populated store read-only and asserts real rows in each.

Two assertion layers, mirroring tests/test_dashboard_scale.py (timing on
CI runners is noisy):

* Structural bounds (always run, CI-safe): /workspaces renders 200 with
  all 220 keys present as rows; past PROBE_MAX_OPENS the muted cap line
  appears, probed populated stores render numeric call counts, capped
  ones render the em-dash (state still "populated" — budgeted, not
  broken).
* First-render budget: the FIRST GET of /workspaces on a freshly started
  TestClient must render under a wall-clock ceiling. The strict 2.0s
  ceiling (SC-1 / FR-005) runs only when ``CAIRN_WORKSPACES_STRICT=1``::

      CAIRN_WORKSPACES_STRICT=1 uv run pytest tests/test_dashboard_workspaces.py

  The repo registers no timing/slow pytest marker, so this module-local
  env gate is the fallback. It is read at IMPORT time because the
  suite-wide ``_hermetic_env`` autouse fixture (tests/conftest.py)
  deletes every ``CAIRN_*`` env var per test -- a test-body read would
  always see it unset. Ungated runs (CI) still assert a generous 20s
  ceiling plus every structural bound, so real regressions (per-store
  unbudgeted opens, whole-directory scans gone wrong) stay visible.

No sleeps anywhere; module runtime is dominated by fixture synthesis
(well under a second) plus two overview renders.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import pytest

# Read at module import -- see module docstring (_hermetic_env clears
# CAIRN_* per test, so this cannot live inside a test body).
_STRICT_BUDGET = os.environ.get("CAIRN_WORKSPACES_STRICT", "") == "1"

_STRICT_CEILING_S = 2.0  # SC-1 / FR-005: full overview render under 2s
_CI_CEILING_S = 20.0  # generous ungated ceiling so breakage stays visible

# Machine shape: 220 stores > FR-005's 200 threshold; populated stores
# outnumber PROBE_MAX_OPENS so the probe cap is genuinely exercised.
_POPULATED = 210  # real schema .kg stores, _CALLS_PER_STORE rows each
_EMPTY = 6  # key dirs with no .kg
_MISSING = 4  # registered keys whose store dir does not exist
_TOTAL = _POPULATED + _EMPTY + _MISSING  # 220
_CALLS_PER_STORE = 5
_REGISTERED_POPULATED = 60  # the registry maps a subset of populated keys


# 16-hex keys (paths.store_key's layout convention). Zero-padded ints
# sort lexicographically == numerically, so the probe's populated-first /
# by-key order is computable from the constants alone.
def _populated_key(i: int) -> str:
    return f"{i:016x}"


def _empty_key(i: int) -> str:
    return f"e{i:015x}"


def _missing_key(i: int) -> str:
    return f"f{i:015x}"


@pytest.fixture(scope="module")
def scale_home(tmp_path_factory) -> dict:
    """A CAIRN_HOME fixture with 220 synthesized stores (module-scoped:
    every test in this file only reads it -- the route and the probe are
    read-only by design, FR-004).

    One template store is built for real (``get_db`` applies the full
    schema, then ``tool_metrics`` rows are inserted and the WAL is
    checkpointed so the ``.kg`` is self-contained); the other 209
    populated stores are byte-copies of it. Empty key dirs and the
    registered-missing tail need no DB. The registry maps a subset of
    the populated stores plus the missing ones; the rest are orphans.
    """
    root = tmp_path_factory.mktemp("workspaces-scale")
    home = root / "cairn-home"
    home.mkdir()

    from cairn.graph.schema import get_db

    template = root / "template.kg"
    conn = get_db(str(template))
    try:
        conn.executemany(
            "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
            "duration_ms, status) VALUES (?, ?, ?, ?, ?)",
            [
                ("explore", "ws-scale", 1755648000.0 + i, 50.0, "ok")
                for i in range(_CALLS_PER_STORE)
            ],
        )
        conn.commit()
        # Leave no -wal/-shm beside the template: the copies must be
        # complete standalone stores a mode=ro open can read anywhere.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()

    for i in range(_POPULATED):
        store_dir = home / _populated_key(i)
        store_dir.mkdir()
        shutil.copy(template, store_dir / ".kg")
    for i in range(_EMPTY):
        (home / _empty_key(i)).mkdir()

    registry = {}
    for i in range(_REGISTERED_POPULATED):
        registry[str(root / "ws" / f"proj-{i:03d}")] = _populated_key(i)
    for i in range(_MISSING):
        registry[str(root / "ws" / f"gone-{i}")] = _missing_key(i)
    (home / "workspaces.json").write_text(json.dumps(registry), encoding="utf-8")

    return {"home": home, "root": root}


def _workspaces_client(tmp_path, monkeypatch, home: Path):
    """A TestClient whose /workspaces probes ``home``: the handler
    resolves paths.CAIRN_HOME per request, so patching the module
    attribute is the seam (the launch db_path is never opened by this
    route) -- the app suite's workspaces-fixture convention."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn import paths
    from cairn.dashboard.app import create_app

    monkeypatch.setattr(paths, "CAIRN_HOME", home)
    return TestClient(create_app(db_path=str(tmp_path / "dash.db")))


def _store_row(html: str, key: str) -> str:
    """The rendered <tr> of the store keyed ``key`` -- assertions scoped
    to the one store they are about (the app suite's helper, duplicated
    here because test modules are separately owned)."""
    for row in html.split("<tr>")[1:]:
        if f"<code>{key}</code>" in row:
            return row
    raise AssertionError(f"workspaces row for key {key!r} missing from page")


def _expected_probe_split(home: Path) -> tuple:
    """(probed_keys, capped_keys): probe_stores walks enumerate order
    (populated-first, then by key) opening at most PROBE_MAX_OPENS DBs,
    so the split is exactly the first PROBE_MAX_OPENS populated keys vs
    the rest."""
    from cairn.dashboard.workspaces import PROBE_MAX_OPENS, enumerate_stores

    populated = [
        row["key"] for row in enumerate_stores(home) if row["state"] == "populated"
    ]
    return populated[:PROBE_MAX_OPENS], populated[PROBE_MAX_OPENS:]


# ---------------------------------------------------------------------------
# Fixture shape (the synthesis is what it claims to be)
# ---------------------------------------------------------------------------


def test_scale_home_shape(scale_home):
    """The synthesized machine matches the shape FR-005's budget assumes:
    220 stores (>= 200), 210 populated -- more than PROBE_MAX_OPENS, so
    the cap is genuinely exercised -- 6 empty, 4 registered-missing, and
    the registry mapping only a subset. Every populated store is a real
    schema DB: a read-only open of EACH finds _CALLS_PER_STORE recorded
    calls (the honesty check on the copy trick)."""
    from cairn.dashboard.workspaces import PROBE_MAX_OPENS, enumerate_stores

    home = scale_home["home"]
    rows = enumerate_stores(home)
    assert len(rows) == _TOTAL  # 220 keys: union of registry and dirs
    states = [row["state"] for row in rows]
    assert states.count("populated") == _POPULATED
    assert states.count("empty") == _EMPTY
    assert states.count("missing") == _MISSING
    assert _POPULATED > PROBE_MAX_OPENS  # the cap is not vacuous (110 over)
    assert _TOTAL >= 200  # FR-005's threshold

    # The registry maps a subset: only _REGISTERED_POPULATED populated
    # keys are registered (plus the missing tail); the rest are orphans.
    registry = json.loads((home / "workspaces.json").read_text(encoding="utf-8"))
    assert len(registry) == _REGISTERED_POPULATED + _MISSING

    # Every populated store carries real rows -- not just the template.
    from cairn.graph.schema import get_db

    for row in rows:
        if row["state"] != "populated":
            continue
        conn = get_db(str(home / row["key"] / ".kg"), read_only=True)
        try:
            count = conn.execute("SELECT COUNT(*) FROM tool_metrics").fetchone()[0]
        finally:
            conn.close()
        assert count == _CALLS_PER_STORE, row["key"]


# ---------------------------------------------------------------------------
# Structural bounds (always run, CI-safe)
# ---------------------------------------------------------------------------


def _assert_overview_structure(html: str, home: Path) -> None:
    """/workspaces at 220 stores: complete (every key rendered with its
    state), honest past the probe cap (numeric counts for probed stores,
    em-dash for capped ones with state still populated, muted cap line),
    and correct about registration (registered rows show the path,
    orphans the unregistered marker)."""
    from cairn.dashboard.workspaces import PROBE_MAX_OPENS

    probed, capped = _expected_probe_split(home)
    assert probed and capped  # both sides of the cap exist

    every_key = (
        [_populated_key(i) for i in range(_POPULATED)]
        + [_empty_key(i) for i in range(_EMPTY)]
        + [_missing_key(i) for i in range(_MISSING)]
    )
    for key in every_key:
        _store_row(html, key)  # raises when a key has no rendered row

    for key in probed:  # inside the open budget: the real count renders
        row = _store_row(html, key)
        assert f'<td class="num">{_CALLS_PER_STORE}</td>' in row, key
        assert "<td>populated</td>" in row, key
    for key in capped:  # past the budget: unknown (em-dash), still populated
        row = _store_row(html, key)
        assert '<td class="num">—</td>' in row, key
        assert "<td>populated</td>" in row, key

    for i in range(_EMPTY):
        assert "<td>empty</td>" in _store_row(html, _empty_key(i))
    for i in range(_MISSING):
        row = _store_row(html, _missing_key(i))
        assert "<td>missing</td>" in row
        assert f"gone-{i}" in row  # the registered path stays verbatim

    # Degradation visible, never silent (FR-005's other half).
    assert "counts unavailable for some stores (probe cap)" in html

    # Registration subset renders as such: a registered populated store
    # shows its workspace path, an orphan shows the unregistered marker.
    registered_row = _store_row(html, _populated_key(0))
    assert "proj-000" in registered_row
    orphan_row = _store_row(html, _populated_key(_REGISTERED_POPULATED))
    assert "— (unregistered)" in orphan_row

    # Cross-check via whole-page counts: exactly PROBE_MAX_OPENS numeric
    # cells, exactly the capped + empty + missing rows as em-dashes.
    assert html.count(f'<td class="num">{_CALLS_PER_STORE}</td>') == len(probed)
    assert html.count('<td class="num">—</td>') == (
        len(capped) + _EMPTY + _MISSING
    )
    assert len(probed) == PROBE_MAX_OPENS


def test_overview_lists_every_store_at_scale(tmp_path, monkeypatch, scale_home):
    """TC-006 structural: with 220 synthesized stores the overview
    renders every single one with its state; the probe-open budget
    degrades exactly the populated stores past PROBE_MAX_OPENS to an
    em-dash count (never a hang, never a silent zero), and the muted cap
    note says so on the page."""
    resp = _workspaces_client(
        tmp_path, monkeypatch, scale_home["home"]
    ).get("/workspaces")
    assert resp.status_code == 200
    _assert_overview_structure(resp.text, scale_home["home"])


# ---------------------------------------------------------------------------
# First-render budget (TC-006 / FR-005 / SC-1)
# ---------------------------------------------------------------------------


def test_overview_first_render_budget(tmp_path, monkeypatch, scale_home):
    """The FIRST GET of /workspaces on a freshly started TestClient
    renders the whole 220-store machine within budget.

    Strict 2.0s wall (SC-1 / FR-005) only under CAIRN_WORKSPACES_STRICT=1
    (module-level gate -- see module docstring); ungated runs keep every
    structural bound plus a generous 20s ceiling so a real regression
    (e.g. an unbudgeted per-store open, 210 of them) still fails. The
    fresh client makes template loading/compilation count as part of
    "first render", as FR-005 intends."""
    home = scale_home["home"]
    client = _workspaces_client(tmp_path, monkeypatch, home)

    start = time.perf_counter()
    resp = client.get("/workspaces")
    elapsed = time.perf_counter() - start
    assert resp.status_code == 200

    ceiling = _STRICT_CEILING_S if _STRICT_BUDGET else _CI_CEILING_S
    assert elapsed < ceiling, (
        f"/workspaces first render took {elapsed:.2f}s over 220 stores "
        f"(budget {ceiling}s)"
    )

    # Structural bounds hold regardless of the gate (CI-safe).
    _assert_overview_structure(resp.text, home)


# ---------------------------------------------------------------------------
# Workspace stickiness (store selection remembered across visits)
# ---------------------------------------------------------------------------


def test_base_template_carries_store_stickiness_script(tmp_path):
    """A URL-carried ?store selection is remembered (localStorage
    "cairn-store") and a bare visit redirects to the remembered store;
    until the user picks a workspace nothing is stored, so bare URLs
    keep the launch store. Pins the script's contract markers, mirroring
    the theme-script tests: the storage key, both directions of the
    localStorage round-trip, the param echo, and the restoring
    redirect."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app

    client = TestClient(
        create_app(
            db_path=str(tmp_path / "dash.db"),
            knowledge_dir=str(tmp_path / "missing"),
        )
    )
    resp = client.get("/")
    assert resp.status_code == 200

    script_start = resp.text.index("cairn-store")
    block = resp.text[script_start : resp.text.index("</script>", script_start)]
    assert '"cairn-store"' in block  # the storage key
    assert "searchParams.get" in block  # a present selection...
    assert "localStorage.setItem" in block  # ...is remembered
    assert "localStorage.getItem" in block  # a bare visit...
    assert "location.replace" in block  # ...restores it
