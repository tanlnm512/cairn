"""Live soak harness for /history under sustained refresh (TC-007 / FR-006 /
SC-2, spec ui-dashboard-live-updates).

The poll loop's contract (tech-spec D-002): re-fetch the same URL, swap the
#refresh-region whole, ordering owned by the server's ORDER BY -- so no
duplicate row can render by construction. The AUTO half of this harness
drives the SERVER side at tick cadence: over a seeded 300-row store it
performs 30 simulated cycles -- each landing 1-5 newer rows via direct
committed INSERTs (the recorder's flush made visible; no sleeps, per the
plan's no-real-timers risk mitigation) and re-fetching /history -- and
asserts that every fetch's rendered row identity set (tool +
rendered-timestamp pairs, extending the content-identity approach of the
landed live-refresh tests in tests/test_dashboard_app.py) contains no
duplicates and equals the stored first-page slice exactly, computed via
the data layer on the same store. The page stays at exactly
HISTORY_PAGE_SIZE throughout, and after the final cycle every row landed
newer than the first page's newest-at-start must appear in the final
fetch -- SC-2's no-missed-batch half.

The MANUAL half -- a real browser left on /history for an hour of live
traffic -- is LIVE_SOAK_MANUAL_PROCEDURE at the bottom of this file (the
browser-side complement this harness cannot drive).

Row extraction is template-agnostic on purpose: rows are parsed as generic
<tr>/<td> table data and only /history (landed) is asserted -- the chains/
tokens templates are wave-mate T008's files.
"""
from __future__ import annotations

import sqlite3
from html.parser import HTMLParser

import pytest

# Fixed era (2025-08-20 00:00:00 UTC -- the suite's seeded-fixture era):
# deterministic timestamps, one row per second, so every stored row owns a
# distinct rendered timestamp and a (tool, timestamp) pair identifies it.
_BASE_TS = 1755648000.0

_SEED_ROWS = 300  # moderate store: page bounds exceeded from the start
_SOAK_CYCLES = 30

# Per-cycle landed-row counts (1-5, the full range exercised across the
# first ten cycles). The total (44) is deliberately <= HISTORY_PAGE_SIZE
# (50): every row landed mid-soak stays among the store's newest, so the
# final fetch's first page must contain every one of them -- which is
# exactly what the missed-batch check asserts.
_INSERT_SCHEDULE: tuple[int, ...] = (4, 1, 3, 2, 5, 1, 2, 3, 1, 2) + (1,) * 20

_SOAK_TOOLS = (
    "explore",
    "ask_compass",
    "get_callers",
    "search_symbols",
    "semantic_search",
    "impact_analysis",
    "recall_memory",
    "record_memory",
)

_INSERT_SQL = (
    "INSERT INTO tool_metrics (tool_name, session_id, invoked_at, "
    "duration_ms, status, error_message, req_chars, resp_chars, "
    "args_summary) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


class _RowTextCollector(HTMLParser):
    """Collects each data row's <td> cell texts as one tuple per <tr> --
    header rows (all <th>) contribute nothing. Any table view's rows have
    this shape, so nothing here depends on a specific template."""

    def __init__(self):
        super().__init__()
        self.rows: list[tuple[str, ...]] = []
        self._cells: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._cells = []
        elif tag == "td" and self._cells is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag == "td" and self._cell is not None:
            self._cells.append("".join(self._cell))
            self._cell = None
        elif tag == "tr" and self._cells:
            self.rows.append(tuple(self._cells))
            self._cells = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _region_row_identities(region_html: str) -> list[tuple[str, str]]:
    """(tool, rendered timestamp) per rendered data row, in render order --
    the content identity of a row: the pair a viewer reads to tell two
    rendered rows apart (deterministic one-row-per-second seeding keeps
    exactly one pair per stored row)."""
    collector = _RowTextCollector()
    collector.feed(region_html)
    collector.close()
    identities = []
    for cells in collector.rows:
        assert len(cells) >= 2, f"malformed rendered data row: {cells!r}"
        identities.append((cells[0], cells[1]))
    return identities


def _row_identity(tool_name: str, invoked_at: float) -> tuple[str, str]:
    """The (tool, timestamp) pair exactly as /history renders it -- expected
    identities are built with the app's own timestamp formatter, so a
    rendered row and its expectation can never drift apart on format."""
    from cairn.dashboard.app import _human_ts

    return (tool_name, _human_ts(invoked_at))


def _seed_soak_store(db_path: str) -> None:
    """A graph-schema DB with _SEED_ROWS direct-inserted rows (the scale
    suite's fast bulk pattern: executemany, no server, no sleeps), written
    oldest-first with one-row-per-second timestamps."""
    from cairn.graph.schema import _apply_schema

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    conn.executemany(
        _INSERT_SQL,
        [
            (
                _SOAK_TOOLS[i % len(_SOAK_TOOLS)],
                f"soak-seed-{i % 12}",
                _BASE_TS + i,
                5.0 + (i % 200) * 1.5,
                "error" if i % 17 == 0 else "ok",
                "soak failure" if i % 17 == 0 else None,
                400 if i % 5 else None,
                1600 if i % 5 else None,
                '{"soak_seed": %d}' % i,
            )
            for i in range(_SEED_ROWS)
        ],
    )
    conn.commit()
    conn.close()


def _land_rows(db_path: str, rows: list[tuple]) -> None:
    """Land a batch the way the recorder's flush would make it visible:
    direct committed INSERTs between two fetches -- the store state a poll
    cycle re-fetches into."""
    conn = sqlite3.connect(db_path)
    try:
        conn.executemany(_INSERT_SQL, rows)
        conn.commit()
    finally:
        conn.close()


def test_soak_insert_schedule_shape():
    """The soak's landed-batch schedule stays inside its design envelope:
    one entry per cycle, each landing 1-5 rows with the full 1-5 range
    exercised, and a total that never exceeds HISTORY_PAGE_SIZE -- so every
    landed row is on the final fetch's first page and the missed-batch
    check targets every landed row, not just the recent ones."""
    from cairn.dashboard.data import HISTORY_PAGE_SIZE

    assert len(_INSERT_SCHEDULE) == _SOAK_CYCLES
    assert all(1 <= n <= 5 for n in _INSERT_SCHEDULE)
    assert set(_INSERT_SCHEDULE) == {1, 2, 3, 4, 5}
    assert sum(_INSERT_SCHEDULE) <= HISTORY_PAGE_SIZE


def test_history_live_soak_matches_stored_slice_every_cycle(tmp_path):
    """TC-007 auto half (FR-006 / SC-2): 30 simulated poll cycles over a
    growing 300+-row store -- every cycle's re-fetch renders exactly the
    stored first-page slice once per row (no duplicates, no missed rows,
    no extras), the page stays bounded at HISTORY_PAGE_SIZE, and after the
    final cycle every row landed newer than the soak's opening newest row
    appears in the final fetch."""
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app
    from cairn.dashboard.data import (
        HISTORY_PAGE_SIZE,
        get_read_only_db,
        list_history,
    )
    from tests.test_dashboard_app import _refresh_region

    db_path = str(tmp_path / "soak.db")
    _seed_soak_store(db_path)
    client = TestClient(
        create_app(db_path=db_path, knowledge_dir=str(tmp_path / "missing"))
    )

    def fetched_identities() -> list[tuple[str, str]]:
        resp = client.get("/history")  # the poll's re-fetch of the same URL
        assert resp.status_code == 200
        region = _refresh_region(resp.text)
        assert region is not None, "no #refresh-region rendered on /history"
        return _region_row_identities(region)

    def stored_identities() -> set[tuple[str, str]]:
        conn = get_read_only_db(db_path)
        try:
            page = list_history(conn)  # the route's own defaults
        finally:
            conn.close()
        return {
            _row_identity(r["tool_name"], r["invoked_at"])
            for r in page["rows"]
        }

    # The page is open before any traffic: one bounded page already (the
    # 300-row seed far exceeds HISTORY_PAGE_SIZE), equal to the stored
    # slice, topped by the newest row the store had at soak start.
    opening = fetched_identities()
    assert len(opening) == HISTORY_PAGE_SIZE
    assert len(set(opening)) == len(opening)
    assert set(opening) == stored_identities()
    seed_newest_ts = _BASE_TS + _SEED_ROWS - 1
    assert opening[0] == _row_identity(
        _SOAK_TOOLS[(_SEED_ROWS - 1) % len(_SOAK_TOOLS)], seed_newest_ts
    )

    next_ts = _BASE_TS + _SEED_ROWS
    seq = 0
    landed: list[tuple[str, float]] = []  # (tool, ts) of every mid-soak row
    for cycle, count in enumerate(_INSERT_SCHEDULE):
        rows = []
        for _ in range(count):
            failed = seq % 11 == 0
            rows.append(
                (
                    _SOAK_TOOLS[seq % len(_SOAK_TOOLS)],
                    f"soak-live-{cycle // 3}",
                    next_ts,
                    5.0 + (seq % 7) * 13.0,
                    "error" if failed else "ok",
                    "soak failure" if failed else None,
                    240 * (seq % 4 + 1),
                    960 * (seq % 4 + 1),
                    '{"soak_cycle": %d, "seq": %d}' % (cycle, seq),
                )
            )
            landed.append((rows[-1][0], next_ts))
            next_ts += 1
            seq += 1
        _land_rows(db_path, rows)

        ids = fetched_identities()
        # FR-006: no duplicate row identity ever renders.
        assert len(set(ids)) == len(ids), f"cycle {cycle}: duplicate rows"
        # Rendered set == stored first-page slice: no missed rows, no extras.
        assert set(ids) == stored_identities(), f"cycle {cycle}: slice drift"
        # SC-2's bounded page: exactly one page, never the growing store.
        assert len(ids) == HISTORY_PAGE_SIZE, f"cycle {cycle}: unbounded page"
        # Correctly ordered: the cycle's newest landed row sits on top.
        assert ids[0] == _row_identity(*landed[-1]), (
            f"cycle {cycle}: newest landed row is not first"
        )

    # Missed-batch check (TC-007: "no landed batch is missing from the
    # final render"): every row landed mid-soak is newer than the first
    # page's newest-at-start, and the final fetch must render every one.
    final_ids = set(fetched_identities())
    assert all(ts > seed_newest_ts for _, ts in landed)
    missing = [row for row in landed if _row_identity(*row) not in final_ids]
    assert not missing, f"rows landed but never rendered: {missing}"


# TC-007's manual half -- one hour of real-interval behavior -- cannot run
# here (no browser, and auto tests forbid real timers per the plan's risk
# mitigation); the procedure below mirrors LIVE_TC005_MANUAL_PROCEDURE /
# LIVE_TC006_MANUAL_PROCEDURE in tests/test_dashboard_app.py.
LIVE_SOAK_MANUAL_PROCEDURE = """\
TC-007 manual half -- no duplicate rows across sustained refresh (FR-006 /
SC-2). Run against a live dashboard (cairn serve) with real traffic landing
(an agent session querying cairn, or any store that keeps growing):

1. Open /history in a real browser with auto-refresh ON (the default
   running state) and leave the page open for one hour of active traffic
   -- or an accelerated equivalent (the recorder's 30s flush plus a
   shorter poll interval configured on the page); what matters is that
   many refresh cycles elapse with rows landing between them.
2. Let the poll loop do every refresh -- no manual reloads. Optionally
   pause/resume a few times; on resume the view must catch up on the
   next cycle.
3. At the end of the hour, on the final refresh cycle, verify WITHOUT
   reloading:
   a. No row appears twice: scan the rendered table for repeated
      Tool + Timestamp pairs (the browser-side complement of this
      harness's id-set equality).
   b. No missed batch: the newest rows stored while the page was open
      are all visible on page one -- e.g. query the store (sqlite) for
      the newest HISTORY_PAGE_SIZE rows by (invoked_at, id) and confirm
      each one renders.
   c. The page stayed responsive the whole hour (SC-2): the table kept
      swapping each cycle and never froze, blanked, or reset its filters.
"""
