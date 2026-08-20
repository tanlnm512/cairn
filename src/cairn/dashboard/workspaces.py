"""Enumeration of local cairn stores for the workspaces overview (FR-002).

The registry (``<cairn_home>/workspaces.json``) and the hash-keyed store
directories under ``cairn_home`` are two independent records of what exists
on this machine; :func:`enumerate_stores` unions them and classifies each
store. Divergence — a registered key whose dir is gone, or an orphan store
dir no registry entry points at — is data to render, never to repair: this
module writes nothing anywhere (FR-004, tech-spec D-002).

:func:`probe_store` / :func:`probe_stores` add the per-store metrics
(size, freshness, tool-call count) on top — stat-first, with SQL opens
only for counts and only budgeted (FR-001, tech-spec D-003). Every open
is mode=ro; this module still writes nothing anywhere.
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import List, Optional

from cairn.graph.schema import get_db
from cairn.paths import REGISTRY_FILE

# Every state a store can be presented in. "unreadable" is produced by the
# per-store probe (a real read-only open), not by filesystem enumeration.
STORE_STATES = ("populated", "empty", "missing", "unreadable")

# Store dirs under CAIRN_HOME are named by paths.store_key(): 16 hex chars.
_KEY_RE = re.compile(r"^[0-9a-f]{16}$")

# Layout constant mirroring paths.StorePaths (db = <home>/<key>/.kg).
_DB_FILENAME = ".kg"

# FR-005: the overview must render within 2s with 200+ stores, and count
# opens dominate probe cost — cap them; rows past the cap degrade visibly
# (counts_capped) rather than silently (tech-spec D-003).
PROBE_MAX_OPENS = 100


def _load_registry(cairn_home: Path) -> dict:
    """Read the {workspace_abs_path: key} registry under ``cairn_home``.

    Same semantics as :func:`cairn.paths._load_registry` (absent, unparseable,
    or non-dict registry → empty dict) but parameterized by home so callers
    and tests are never bound to the import-time CAIRN_HOME. UnicodeDecodeError
    (a ValueError, like JSONDecodeError) is also swallowed: classifying stores
    must never raise on a corrupt registry.
    """
    registry_file = cairn_home / REGISTRY_FILE.name
    if not registry_file.exists():
        return {}
    try:
        data = json.loads(registry_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def enumerate_stores(cairn_home: Path) -> List[dict]:
    """Every local store: the union of registry entries and store dirs.

    Returns ``[{"key", "path", "state"}, ...]`` sorted populated-first, then
    by key. ``path`` is the registered workspace path verbatim, or None for
    an orphan store dir no registry entry points at.

    Classification is purely filesystem-based: ``populated`` = key dir
    exists with a ``.kg`` file; ``empty`` = key dir exists, no ``.kg``;
    ``missing`` = registered key whose dir does not exist. The
    ``unreadable`` refinement (a ``.kg`` that fails a read-only open) is
    the probe's job, not the enumerator's. A missing/unlistable
    ``cairn_home`` yields an empty list; this function never raises and
    never writes.
    """
    if not cairn_home.is_dir():
        return []

    # key -> registered workspace path; only str keys are usable (a corrupt
    # non-str value is skipped, not fabricated into a store row).
    registered: dict = {}
    for ws_path, key in _load_registry(cairn_home).items():
        if isinstance(key, str):
            registered[key] = ws_path

    # Only 16-hex directories are stores; anything else under home is not.
    on_disk: set = set()
    try:
        names = list(cairn_home.iterdir())
    except OSError:
        names = []
    for entry in names:
        if entry.is_dir() and _KEY_RE.fullmatch(entry.name):
            on_disk.add(entry.name)

    rows: List[dict] = []
    for key in registered.keys() | on_disk:
        store_dir = cairn_home / key
        if key not in on_disk:
            state = "missing"
        elif (store_dir / _DB_FILENAME).is_file():
            state = "populated"
        else:
            state = "empty"
        rows.append({"key": key, "path": registered.get(key), "state": state})

    rows.sort(key=lambda row: (row["state"] != "populated", row["key"]))
    return rows


def _stat_kg(cairn_home: Path, key: str) -> tuple:
    """``(size_bytes, mtime)`` of ``<home>/<key>/.kg``; ``(None, None)`` if absent.

    Free (no open); mtime is the freshness proxy for "last-indexed" — the
    SQLite header does not record a trustworthy indexed-at timestamp, and
    opening every store just to ask would defeat D-003.
    """
    try:
        st = (cairn_home / key / _DB_FILENAME).stat()
    except OSError:
        return (None, None)
    return (st.st_size, st.st_mtime)


def _count_tool_calls(kg_path: Path) -> Optional[int]:
    """Tool-call count via ONE mode=ro open; ``None`` = the open/query failed.

    0 vs None is load-bearing: 0 is a real count (including a store from
    before ``tool_metrics`` existed — missing table reads as 0); None means
    corrupt DB / unusable open and the caller reclassifies to "unreadable".
    """
    conn = None
    try:
        conn = get_db(str(kg_path), read_only=True)
        try:
            return conn.execute("SELECT COUNT(*) FROM tool_metrics").fetchone()[0]
        except sqlite3.OperationalError:
            return 0  # no such table — an older store, not a broken one
    except sqlite3.Error:
        return None  # corrupt DB, locked beyond timeout: unreadable, never raise
    finally:
        if conn is not None:
            conn.close()


def _probe(cairn_home: Path, entry: dict, count_allowed: bool) -> dict:
    """One store row: entry merged with size/freshness and (maybe) call count.

    Only "populated" rows ever open a DB, and only when ``count_allowed``
    (the probe_stores budget). A failed open reclassifies the state to
    "unreadable" keeping the stat-derived fields — the row never carries
    an exception out.
    """
    row = dict(entry)
    size_bytes, last_modified = _stat_kg(cairn_home, row["key"])
    row["size_bytes"] = size_bytes
    row["last_modified"] = last_modified

    if row["state"] != "populated":
        row["call_count"] = None
        row["counts_capped"] = False
        return row

    if not count_allowed:
        row["call_count"] = None
        row["counts_capped"] = True
        return row

    call_count = _count_tool_calls(cairn_home / row["key"] / _DB_FILENAME)
    if call_count is None:
        row["state"] = "unreadable"
    row["call_count"] = call_count
    row["counts_capped"] = False
    return row


def probe_store(cairn_home: Path, entry: dict) -> dict:
    """Probe one :func:`enumerate_stores` row (unbudgeted single-store form).

    Returns the entry merged with ``size_bytes``/``last_modified`` (os.stat
    on the ``.kg``; None when absent), ``call_count`` (one read-only open,
    populated rows only) and ``counts_capped`` (False — no budget applies).
    """
    return _probe(cairn_home, entry, count_allowed=True)


def probe_stores(
    cairn_home: Path, entries: List[dict], max_opens: int = PROBE_MAX_OPENS
) -> List[dict]:
    """:func:`probe_store` over ``entries`` in list order, ≤ ``max_opens`` DB opens.

    Rows past the cap keep their filesystem stats with ``call_count`` None
    and ``counts_capped`` True — the degradation stays visible (FR-005).
    Failed opens count against the budget too: the cap bounds work, not
    just successes.
    """
    rows: List[dict] = []
    opens_used = 0
    for entry in entries:
        count_allowed = opens_used < max_opens
        row = _probe(cairn_home, entry, count_allowed=count_allowed)
        if count_allowed and entry.get("state") == "populated":
            opens_used += 1
        rows.append(row)
    return rows
