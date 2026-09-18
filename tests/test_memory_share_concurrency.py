"""Two concurrent simulated agents sharing into one store.

Each agent runs as its own thread with its own writable ``get_db``
connection to a single file-backed store — the same write chokepoint two
concurrent agent processes hit. Covers:

  1. Concurrent shares on distinct symbols: every row lands, no lock error
     (WAL + busy_timeout absorb the contention).
  2. Concurrent shares on the same symbol: both agents' rows persist —
     no lost update.
  3. Per-agent namespaces: each agent's ``agent_symbols`` rows carry only
     its own (symbol, memory_id) pairs, disjoint rows coexisting in one
     store.
  4. Legacy single-agent memory flow (no agent id anywhere) produces
     identical results while share traffic runs alongside.
"""
from __future__ import annotations

import threading

import pytest

from cairn.graph.schema import get_db
from cairn.memory.store import (
    create_memory,
    get_memory,
    share_memory,
    store_memory,
)
from cairn.okf.bundle import OKFBundle

AGENT_A = "agentA"
AGENT_B = "agentB"


@pytest.fixture
def shared_store(tmp_path):
    """File-backed store, opened writable once so worker threads get pure
    connection setup (schema rides the fixture open)."""
    path = tmp_path / "shared-store.db"
    conn = get_db(str(path))
    conn.close()
    return str(path)


def _share_worker(store_path, barrier, outcomes, agent_id, symbols, memory_id):
    """One simulated agent session: open, wait for the barrier, share, commit.

    Errors (e.g. a lock timeout) are stored in ``outcomes`` so the test
    assertion names them instead of the thread dying silently."""
    conn = get_db(store_path)
    try:
        barrier.wait()
        outcomes[agent_id] = share_memory(agent_id, symbols, memory_id, conn=conn)
        conn.commit()
    except Exception as e:
        outcomes[agent_id] = e
    finally:
        conn.close()


def _all_rows(conn):
    return conn.execute(
        "SELECT agent_id, symbol, memory_id FROM agent_symbols"
        " ORDER BY agent_id, symbol"
    ).fetchall()


def test_concurrent_distinct_symbols_no_lost_write(shared_store):
    """Two agents sharing disjoint symbol sets concurrently land every row;
    no 'database is locked' surfaces (WAL + busy_timeout)."""
    barrier = threading.Barrier(2)
    outcomes = {}
    symbols_a = ["auth", "session", "tokens"]
    symbols_b = ["parser", "wiki", "graph"]
    threads = [
        threading.Thread(
            target=_share_worker,
            args=(shared_store, barrier, outcomes, AGENT_A, symbols_a, "memory/tribal/m-a"),
        ),
        threading.Thread(
            target=_share_worker,
            args=(shared_store, barrier, outcomes, AGENT_B, symbols_b, "memory/tribal/m-b"),
        ),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes == {AGENT_A: len(symbols_a), AGENT_B: len(symbols_b)}

    conn = get_db(shared_store)
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        rows = _all_rows(conn)
    finally:
        conn.close()

    assert len(rows) == len(symbols_a) + len(symbols_b)
    by_agent = {AGENT_A: set(), AGENT_B: set()}
    for agent_id, symbol, memory_id in rows:
        by_agent[agent_id].add((symbol, memory_id))
    assert by_agent[AGENT_A] == {(s, "memory/tribal/m-a") for s in symbols_a}
    assert by_agent[AGENT_B] == {(s, "memory/tribal/m-b") for s in symbols_b}


def test_concurrent_same_symbol_loses_nothing(shared_store):
    """Two agents sharing the same symbol at the same moment both persist:
    two rows for the symbol, one per agent, each with its own memory id."""
    barrier = threading.Barrier(2)
    outcomes = {}
    threads = [
        threading.Thread(
            target=_share_worker,
            args=(shared_store, barrier, outcomes, AGENT_A, ["hot"], "memory/tribal/m-a"),
        ),
        threading.Thread(
            target=_share_worker,
            args=(shared_store, barrier, outcomes, AGENT_B, ["hot"], "memory/tribal/m-b"),
        ),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes == {AGENT_A: 1, AGENT_B: 1}

    conn = get_db(shared_store)
    try:
        rows = conn.execute(
            "SELECT agent_id, memory_id FROM agent_symbols WHERE symbol = 'hot'"
            " ORDER BY agent_id"
        ).fetchall()
    finally:
        conn.close()

    assert [(r["agent_id"], r["memory_id"]) for r in rows] == [
        (AGENT_A, "memory/tribal/m-a"),
        (AGENT_B, "memory/tribal/m-b"),
    ]


def test_per_agent_namespaces_stay_distinct(shared_store):
    """Each agent's namespace holds only its own rows: agentA's symbols never
    appear under agentB and vice versa, while both coexist in one store."""
    barrier = threading.Barrier(2)
    outcomes = {}
    threads = [
        threading.Thread(
            target=_share_worker,
            args=(shared_store, barrier, outcomes, AGENT_A, ["auth"], "memory/tribal/m-a"),
        ),
        threading.Thread(
            target=_share_worker,
            args=(shared_store, barrier, outcomes, AGENT_B, ["parser"], "memory/tribal/m-b"),
        ),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes == {AGENT_A: 1, AGENT_B: 1}

    conn = get_db(shared_store)
    try:
        for agent_id, own in (
            (AGENT_A, ("auth", "memory/tribal/m-a")),
            (AGENT_B, ("parser", "memory/tribal/m-b")),
        ):
            rows = conn.execute(
                "SELECT symbol, memory_id FROM agent_symbols WHERE agent_id = ?",
                (agent_id,),
            ).fetchall()
            assert [(r["symbol"], r["memory_id"]) for r in rows] == [own]
            other = AGENT_B if agent_id == AGENT_A else AGENT_A
            leaked = conn.execute(
                "SELECT COUNT(*) FROM agent_symbols WHERE agent_id = ? AND symbol = ?",
                (agent_id, other),
            ).fetchone()[0]
            assert leaked == 0
    finally:
        conn.close()


def test_legacy_memory_flow_unchanged_alongside_shares(shared_store, tmp_path):
    """A single-agent legacy flow (no agent id anywhere) stores and retrieves
    memories with distinct ids and intact bodies while two agents share
    concurrently; share traffic writes only agent_symbols rows."""
    barrier = threading.Barrier(3)
    outcomes = {}
    legacy_result = {}

    def legacy_worker():
        barrier.wait()
        bundle = OKFBundle(str(tmp_path / "knowledge"))
        mem1 = create_memory(
            type_="pattern",
            title="backoff retry policy",
            body="First memory about backoff",
            confidence=0.4,
        )
        id1 = store_memory(mem1, bundle)
        mem2 = create_memory(
            type_="pattern",
            title="backoff retry policy",
            body="Second memory about backoff",
            confidence=0.4,
        )
        id2 = store_memory(mem2, bundle)
        legacy_result["ids"] = (id1, id2)
        legacy_result["bodies"] = (
            get_memory(bundle, id1).body,
            get_memory(bundle, id2).body,
        )

    workers = [
        threading.Thread(
            target=_share_worker,
            args=(shared_store, barrier, outcomes, AGENT_A, ["auth"], "memory/tribal/m-a"),
        ),
        threading.Thread(
            target=_share_worker,
            args=(shared_store, barrier, outcomes, AGENT_B, ["parser"], "memory/tribal/m-b"),
        ),
        threading.Thread(target=legacy_worker),
    ]
    for t in workers:
        t.start()
    for t in workers:
        t.join()

    assert outcomes == {AGENT_A: 1, AGENT_B: 1}
    id1, id2 = legacy_result["ids"]
    assert id1 != id2
    assert legacy_result["bodies"] == (
        "First memory about backoff",
        "Second memory about backoff",
    )

    conn = get_db(shared_store)
    try:
        assert len(_all_rows(conn)) == 2
        assert conn.execute("SELECT COUNT(*) FROM memory_refs").fetchone()[0] == 0
    finally:
        conn.close()
