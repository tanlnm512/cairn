"""Enumeration of local cairn stores for the workspaces overview."""
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

# The overview must render within 2s with 200+ stores, and count
# opens dominate probe cost — cap them; rows past the cap degrade visibly
# (counts_capped) rather than silently.
PROBE_MAX_OPENS = 100


def _load_registry(cairn_home: Path) -> dict:
    """Read a registry under the supplied home, degrading corrupt input to empty."""
    registry_file = cairn_home / REGISTRY_FILE.name
    if not registry_file.exists():
        return {}
    try:
        data = json.loads(registry_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def enumerate_stores(cairn_home: Path) -> List[dict]:
    """Return the registry/directory store union with filesystem-only classification."""
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
    """Return .kg size and mtime, or Nones when absent—no DB open."""
    try:
        st = (cairn_home / key / _DB_FILENAME).stat()
    except OSError:
        return (None, None)
    return (st.st_size, st.st_mtime)


def _count_tool_calls(kg_path: Path) -> Optional[int]:
    """Return one read-only call count: zero is valid and None means unreadable."""
    conn = None
    try:
        conn = get_db(str(kg_path), read_only=True)
        try:
            return conn.execute("SELECT COUNT(*) FROM tool_metrics").fetchone()[0]
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc):
                raise  # locked/corrupt: the outer handler reclassifies to unreadable
            return 0  # no such table — an older store, not a broken one
    except sqlite3.Error:
        return None  # corrupt DB, locked beyond timeout: unreadable, never raise
    finally:
        if conn is not None:
            conn.close()


def _probe(cairn_home: Path, entry: dict, count_allowed: bool) -> dict:
    """Return one store row with stats and optional budgeted call count."""
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
    """Probe one store row without the batch open budget."""
    return _probe(cairn_home, entry, count_allowed=True)


def probe_stores(
    cairn_home: Path, entries: List[dict], max_opens: int = PROBE_MAX_OPENS
) -> List[dict]:
    """Probe rows in order while bounding every attempted DB open."""
    rows: List[dict] = []
    opens_used = 0
    for entry in entries:
        count_allowed = opens_used < max_opens
        row = _probe(cairn_home, entry, count_allowed=count_allowed)
        if count_allowed and entry.get("state") == "populated":
            opens_used += 1
        rows.append(row)
    return rows
