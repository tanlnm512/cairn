"""The frozen editor JSON contract (tech-spec D-004): the three endpoints
IDE extensions consume on the local dashboard server (loopback 127.0.0.1:8765,
the app ``create_app`` builds). Endpoint paths and JSON shapes are frozen;
changes require a versioned contract change consumed by every platform.

1. ``GET /editor/symbol?name=<symbol>&depth=<hops>`` — identity, caller and
   callee counts (``calls`` edges only), and the precise depth-limited blast
   radius in one response (one round-trip per hover).
2. ``GET /editor/file?path=<workspace-relative file>`` — the containing
   module's compass excerpt and the file's relevant memories in one response.
3. ``GET /editor/status`` — indexed-store and freshness state for the
   workspace.

Contract invariants:
- GET-only JSON at HTTP 200; POST answers 405 and the store stays
  byte-identical across a full pass.
- Unknown or blank input yields the populated zero shape at 200 — never an
  error status, never a missing key.
- ``depth`` counts caller hops, minimum 1 (1 = direct callers only);
  ``blast_radius.depth`` echoes the effective (clamped) depth, while each
  ``blast_radius.symbols[].depth`` is the 0-based caller distance.
- A memory is relevant when the file's path or module appears in the
  memory's resource, title, or body; memories sort newest-first.
"""
from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

SYMBOL_ENDPOINT = "/editor/symbol"
FILE_ENDPOINT = "/editor/file"
STATUS_ENDPOINT = "/editor/status"


def _seed_store(tmp_path: Path, rows: bool = True) -> str:
    """Graph store with the call chain demo_main -> demo_helper -> demo_util
    across src/demo/{core,util}.py, plus a module symbol whose ``contains``
    edge must never count as a call. The schema-only variant lives at its own
    path so a populated store and a zero store never alias."""
    from cairn.graph.schema import _apply_schema

    db_path = str(tmp_path / ("editor.db" if rows else "editor-empty.db"))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    _apply_schema(conn)
    if rows:
        conn.executemany(
            "INSERT INTO repos (id, name, path, language, indexed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [("demo", "demo", ".", "python", "2026-08-20T08:00:00")],
        )
        conn.executemany(
            "INSERT INTO files (id, repo_id, path, language, indexed_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("f1", "demo", "src/demo/core.py", "python", "2026-08-20T10:00:00"),
                ("f2", "demo", "src/demo/util.py", "python", "2026-08-20T11:00:00"),
            ],
        )
        conn.executemany(
            "INSERT INTO symbols (id, file_id, name, qualified_name, kind) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("s1", "f1", "demo_main", "demo.core.demo_main", "function"),
                ("s2", "f1", "demo_helper", "demo.core.demo_helper", "function"),
                ("s3", "f2", "demo_util", "demo.util.demo_util", "function"),
                ("s4", "f1", "demo_mod", "demo.core", "module"),
            ],
        )
        conn.executemany(
            "INSERT INTO edges (id, source_id, target_id, kind) VALUES (?, ?, ?, ?)",
            [
                ("e1", "s1", "s2", "calls"),
                ("e2", "s2", "s3", "calls"),
                ("e3", "s4", "s2", "contains"),
            ],
        )
    conn.commit()
    conn.close()
    return db_path


def _seed_knowledge(tmp_path: Path) -> str:
    """OKF knowledge dir: compass guides for src/demo and src/other, plus
    memories exercising each relevance arm (resource / body / title) and a
    newest non-relevant one."""
    from cairn.memory.store import create_memory, store_memory
    from cairn.okf.bundle import OKFBundle, OKFConcept

    kdir = tmp_path / "knowledge"
    bundle = OKFBundle(str(kdir))
    for module, cid in (("src/demo", "compass/src-demo"), ("src/other", "compass/src-other")):
        bundle.write_concept(
            OKFConcept(
                type="Compass",
                title="demo core" if module == "src/demo" else "other module",
                description=f"Navigation guide for {module}",
                resource=module,
                tags=[module],
                timestamp="2026-08-20T10:00:00Z",
                concept_id=cid,
                body=(
                    "demo core: entry points and helpers."
                    if module == "src/demo"
                    else "other module internals."
                ),
            )
        )
    seeded = [
        ("2026-08-18T10:00:00Z", "decision", "Freeze before consumers",
         "The demo module ships only after src/demo review.", None),
        ("2026-08-19T11:00:00Z", "mistake", "Skipped the fuzzy retry",
         "Unrelated body text.", "src/demo"),
        ("2026-08-20T09:00:00Z", "pattern", "src/demo retry rule",
         "Body without a path mention.", None),
        ("2026-08-21T09:00:00Z", "workaround", "Other module workaround",
         "Only about src/other internals.", None),
    ]
    for ts, mtype, title, body, resource in seeded:
        concept = create_memory(type_=mtype, title=title, body=body, resource=resource)
        concept.timestamp = ts
        store_memory(concept, bundle, tier="tribal")
    return str(kdir)


def _client(tmp_path: Path, rows: bool = True, knowledge: bool = True):
    pytest.importorskip("httpx")
    from starlette.testclient import TestClient

    from cairn.dashboard.app import create_app

    return TestClient(
        create_app(
            db_path=_seed_store(tmp_path, rows=rows),
            knowledge_dir=_seed_knowledge(tmp_path) if knowledge else None,
        )
    )


def test_contract_base_url_is_loopback_dashboard_default():
    """Extensions address the existing dashboard defaults — no new port,
    never a non-loopback bind."""
    from cairn.dashboard.app import DEFAULT_HOST, DEFAULT_PORT

    assert DEFAULT_HOST == "127.0.0.1"
    assert DEFAULT_PORT == 8765


def test_symbol_endpoint_returns_counts_and_blast_radius(tmp_path):
    """One round-trip: identity, both counts, and the default-depth
    (2-hop) precise blast radius."""
    client = _client(tmp_path)
    resp = client.get(SYMBOL_ENDPOINT, params={"name": "demo_util"})
    assert resp.status_code == 200
    assert resp.json() == {
        "found": True,
        "symbol": "demo_util",
        "kind": "function",
        "qualified_name": "demo.util.demo_util",
        "file": "src/demo/util.py",
        "callers": {"count": 1},
        "callees": {"count": 0},
        "blast_radius": {
            "depth": 2,
            "total": 2,
            "truncated": False,
            "symbols": [
                {
                    "symbol": "demo_helper",
                    "file": "src/demo/core.py",
                    "repo": "demo",
                    "depth": 0,
                },
                {
                    "symbol": "demo_main",
                    "file": "src/demo/core.py",
                    "repo": "demo",
                    "depth": 1,
                },
            ],
        },
    }


def test_symbol_endpoint_depth_bounds_and_clamps(tmp_path):
    """depth=1 stops at direct callers; 0 clamps up to 1; 99 clamps down
    to 10; a non-numeric depth falls back to the 2-hop default."""
    client = _client(tmp_path)
    one_hop = {
        "depth": 1,
        "total": 1,
        "truncated": False,
        "symbols": [
            {
                "symbol": "demo_helper",
                "file": "src/demo/core.py",
                "repo": "demo",
                "depth": 0,
            }
        ],
    }
    for requested, radius in (
        ({"name": "demo_util", "depth": "1"}, one_hop),
        ({"name": "demo_util", "depth": "0"}, one_hop),
        (
            {"name": "demo_util", "depth": "99"},
            {
                "depth": 10,
                "total": 2,
                "truncated": False,
                "symbols": [
                    {
                        "symbol": "demo_helper",
                        "file": "src/demo/core.py",
                        "repo": "demo",
                        "depth": 0,
                    },
                    {
                        "symbol": "demo_main",
                        "file": "src/demo/core.py",
                        "repo": "demo",
                        "depth": 1,
                    },
                ],
            },
        ),
    ):
        resp = client.get(SYMBOL_ENDPOINT, params=requested)
        assert resp.status_code == 200, requested
        assert resp.json()["blast_radius"] == radius, requested
    resp = client.get(SYMBOL_ENDPOINT, params={"name": "demo_util", "depth": "abc"})
    assert resp.status_code == 200
    assert resp.json()["blast_radius"]["depth"] == 2


def test_symbol_endpoint_counts_exclude_non_call_edges(tmp_path):
    """Only ``calls`` edges count: demo_helper's contains edge from the
    module symbol adds neither a caller nor a callee."""
    client = _client(tmp_path)
    resp = client.get(SYMBOL_ENDPOINT, params={"name": "demo_helper"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["callers"] == {"count": 1}
    assert payload["callees"] == {"count": 1}
    resp = client.get(SYMBOL_ENDPOINT, params={"name": "demo_mod"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["found"] is True
    assert payload["callers"] == {"count": 0}
    assert payload["callees"] == {"count": 0}


def test_symbol_endpoint_unknown_and_blank_names_zero_contract(tmp_path):
    """Unknown, blank, and absent names answer the populated zero shape
    at 200 — the extension renders nothing, never an error."""
    client = _client(tmp_path)
    for name in ("no_such_symbol", "", None):
        params = {} if name is None else {"name": name}
        resp = client.get(SYMBOL_ENDPOINT, params=params)
        assert resp.status_code == 200, params
        assert resp.json() == {
            "found": False,
            "symbol": name or "",
            "kind": "",
            "qualified_name": "",
            "file": "",
            "callers": {"count": 0},
            "callees": {"count": 0},
            "blast_radius": {
                "depth": 2,
                "total": 0,
                "truncated": False,
                "symbols": [],
            },
        }, params


def test_file_endpoint_returns_compass_and_relevant_memories(tmp_path):
    """The module's compass excerpt plus the relevant memories
    (resource, body, and title arms), newest-first."""
    client = _client(tmp_path)
    resp = client.get(FILE_ENDPOINT, params={"path": "src/demo/core.py"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["path"] == "src/demo/core.py"
    assert payload["module"] == "src/demo"
    assert payload["compass"] == {
        "found": True,
        "title": "demo core",
        "body": "demo core: entry points and helpers.",
    }
    assert [m["title"] for m in payload["memories"]] == [
        "src/demo retry rule",
        "Skipped the fuzzy retry",
        "Freeze before consumers",
    ]
    for memory in payload["memories"]:
        assert set(memory) == {"id", "type", "title", "body"}
        assert memory["id"].startswith("memory/tribal/")
    assert [m["type"] for m in payload["memories"]] == [
        "pattern",
        "mistake",
        "decision",
    ]


def test_file_endpoint_unmatched_and_blank_paths_zero_contract(tmp_path):
    """A file whose module has no compass and no relevant memories answers
    the zero contract at 200; a blank or absent path answers the empty
    path/module shape."""
    client = _client(tmp_path)
    resp = client.get(FILE_ENDPOINT, params={"path": "docs/readme.md"})
    assert resp.status_code == 200
    assert resp.json() == {
        "path": "docs/readme.md",
        "module": "docs",
        "compass": {"found": False, "title": "", "body": ""},
        "memories": [],
    }
    for path in ("", None):
        params = {} if path is None else {"path": path}
        resp = client.get(FILE_ENDPOINT, params=params)
        assert resp.status_code == 200, params
        assert resp.json() == {
            "path": "",
            "module": "",
            "compass": {"found": False, "title": "", "body": ""},
            "memories": [],
        }, params


def test_status_endpoint_reports_indexed_state(tmp_path):
    """The indexed store reports its counts and freshness; a schema-only
    store reports the unindexed zero shape — both at 200."""
    client = _client(tmp_path, rows=True, knowledge=False)
    resp = client.get(STATUS_ENDPOINT)
    assert resp.status_code == 200
    assert resp.json() == {
        "indexed": True,
        "files": 2,
        "symbols": 4,
        "indexed_at": "2026-08-20T11:00:00",
    }

    empty = _client(tmp_path, rows=False, knowledge=False)
    resp = empty.get(STATUS_ENDPOINT)
    assert resp.status_code == 200
    assert resp.json() == {
        "indexed": False,
        "files": 0,
        "symbols": 0,
        "indexed_at": "",
    }


def test_editor_endpoints_are_get_only(tmp_path):
    """POST answers 405 on every editor endpoint — the app's POST routes
    stay exclusive to settings."""
    client = _client(tmp_path)
    for path in (SYMBOL_ENDPOINT, FILE_ENDPOINT, STATUS_ENDPOINT):
        resp = client.post(path)
        assert resp.status_code == 405, path


def test_editor_endpoints_leave_store_byte_identical(tmp_path):
    """A full pass over the three endpoints — known, unknown, blank, and
    clamped inputs — leaves the store byte-identical with no sidecars."""
    client = _client(tmp_path)
    db_path = str(tmp_path / "editor.db")
    before = hashlib.sha256(Path(db_path).read_bytes()).hexdigest()
    for path in (
        SYMBOL_ENDPOINT + "?name=demo_util",
        SYMBOL_ENDPOINT + "?name=no_such_symbol&depth=99",
        SYMBOL_ENDPOINT,
        FILE_ENDPOINT + "?path=src/demo/core.py",
        FILE_ENDPOINT,
        STATUS_ENDPOINT,
    ):
        resp = client.get(path)
        assert resp.status_code == 200, path
    assert hashlib.sha256(Path(db_path).read_bytes()).hexdigest() == before
    sidecars = sorted(
        p.name
        for p in Path(db_path).parent.iterdir()
        if p.name.endswith(("-wal", "-shm"))
    )
    assert sidecars == []
