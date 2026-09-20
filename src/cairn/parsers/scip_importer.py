"""Edges-only SCIP index overlay: position-joined exact call/reference edges over the tree-sitter graph."""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ..graph.scanner import discover_repos, repository_id

# scip.SymbolRole.Definition (bit 0x1); definition occurrences never emit edges.
_ROLE_DEFINITION = 0x1
# scip.SymbolInformation.Kind values that classify an edge as a call.
_KIND_FUNCTION = 17
_KIND_METHOD = 26

_SKIP_PARSE_ERROR = "scip_parse_error"
_SKIP_JOIN_ANOMALY = "scip_join_anomaly"

# Per-document position-join rate below which the file keeps its tree-sitter
# edges and the anomaly is recorded (FR-005).
_JOIN_ANOMALY_THRESHOLD = 0.5

_INSTALL_HINT = (
    "SCIP support requires the optional [scip] extra; install it with "
    "`pip install 'cairn-intel[scip]'` (or `uv sync --extra scip`)."
)

# Innermost-containing-symbol join: smallest line span first, then latest
# start, then tightest columns as the same-line tie-break.
_SYMBOL_SPANS_SQL = """
    SELECT id, line_start, line_end
    FROM symbols
    WHERE file_id = ? AND line_start IS NOT NULL AND line_end IS NOT NULL
    ORDER BY (line_end - line_start) ASC, line_start DESC,
             column_start DESC, column_end ASC
"""

_INSERT_EDGE = (
    "INSERT INTO edges (id, source_id, target_id, target_name, kind, line, "
    "column, resolution, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'scip')"
)

# Per-file authority: the covered file's tree-sitter call/reference edges give
# way to the index's; other kinds and other files stay untouched.
_DELETE_FILE_CALLREF_EDGES = (
    "DELETE FROM edges WHERE kind IN ('calls','references') AND source_id IN "
    "(SELECT id FROM symbols WHERE file_id = ?)"
)

# Importer-owned skip rows are index-scoped, not repo-scoped, so repo_id stays empty.
_INSERT_SKIP = (
    "INSERT INTO skipped_files (id, repo_id, path, reason, recorded_at)"
    " VALUES (?, '', ?, ?, ?)"
)

# An anomalous-join row names a matched file, so it carries that file's repo id.
_INSERT_ANOMALY_SKIP = (
    "INSERT INTO skipped_files (id, repo_id, path, reason, recorded_at)"
    " VALUES (?, ?, ?, ?, ?)"
)

# Tree-sitter call/reference sites of one file, for (kind, line) disagreement
# matching before the authority DELETE; scip rows never count as the incumbent.
_TS_SITES_SQL = (
    "SELECT e.kind, e.line, e.target_id, e.resolution "
    "FROM edges e JOIN symbols s ON e.source_id = s.id "
    "WHERE s.file_id = ? AND e.kind IN ('calls','references') "
    "AND coalesce(e.source, '') != 'scip'"
)

# --- protobuf availability (FR-006) ----------------------------------------
# A missing runtime AND a runtime older than the vendored stub's gencode
# (ValidateProtobufRuntimeVersion raises VersionError, not ImportError)
# degrade identically: the module imports, scip_available() is False.
try:
    from google.protobuf.runtime_version import VersionError as _VersionError
except ImportError:  # runtimes without a runtime_version module
    class _VersionError(Exception):  # type: ignore[no-redef]
        pass

try:
    from google.protobuf.message import DecodeError as _ProtoDecodeError
    from . import _scip_pb2 as _scip
    _PROTOBUF_AVAILABLE = True
except (ImportError, _VersionError):
    _scip = None  # type: ignore[assignment]
    _ProtoDecodeError = Exception  # type: ignore[assignment,misc]
    _PROTOBUF_AVAILABLE = False


def scip_available() -> bool:
    """True iff the protobuf runtime + vendored stub import cleanly."""
    return _PROTOBUF_AVAILABLE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _terminal_name(symbol: str) -> str:
    """Best-effort readable name from a SCIP symbol string (a label, never a join key)."""
    tail = symbol.rsplit(" ", 1)[-1].rstrip("().")
    tail = tail.rsplit("/", 1)[-1]
    return tail.rsplit(".", 1)[-1] or symbol


def _occurrence_span(occ) -> Optional[Tuple[int, int, int, int]]:
    """(start_line, end_line, start_char, end_char), 0-based inclusive lines; None without a range."""
    which = occ.WhichOneof("typed_range")
    if which == "single_line_range":
        r = occ.single_line_range
        return (r.line, r.line, r.start_character, r.end_character)
    if which == "multi_line_range":
        r = occ.multi_line_range
        return (r.start_line, r.end_line, r.start_character, r.end_character)
    rng = occ.range  # deprecated repeated [startLine, startChar, (endLine,) endChar]
    if len(rng) == 3:
        return (rng[0], rng[0], rng[1], rng[2])
    if len(rng) == 4:
        return (rng[0], rng[2], rng[1], rng[3])
    return None


def _innermost(spans: List[Any], span: Tuple[int, int, int, int]) -> Optional[str]:
    """Id of the first (innermost) symbol row whose 1-based line span contains the 0-based span."""
    start_line, end_line = span[0], span[1]
    for row in spans:
        if row[1] <= start_line + 1 <= end_line + 1 <= row[2]:
            return row[0]
    return None


def _norm_rel(rel_path: str) -> str:
    """Canonical /-separated form of a document's repo-relative path."""
    return rel_path.replace("\\", "/")


def _workspace_repo_ids(workspace: str) -> Set[str]:
    """Repo ids the scanner identity substrate discovers for this workspace."""
    return {repository_id(repo) for repo in discover_repos(workspace)}


def _match_document_path(
    conn: sqlite3.Connection, rel_path: str, repo_ids: Set[str]
) -> Optional[Tuple[str, str]]:
    """(files.id, repo_id) attributing a document's repo-relative path to
    exactly one workspace repo; None when unmatched or ambiguous -- never a
    guessed repo."""
    rows = conn.execute(
        "SELECT id, repo_id FROM files WHERE path = ?", (rel_path,)
    ).fetchall()
    matches = [(file_id, repo_id) for file_id, repo_id in rows if repo_id in repo_ids]
    return matches[0] if len(matches) == 1 else None


def _parse_index(conn: sqlite3.Connection, scip_path: str):
    """Parsed Index protobuf; a corrupt file records scip_parse_error and aborts before any write."""
    try:
        index = _scip.Index()  # type: ignore[attr-defined]  # dynamic gencode module
        index.ParseFromString(Path(scip_path).read_bytes())
        return index
    except _ProtoDecodeError as e:
        conn.execute(_INSERT_SKIP, (_new_id(), str(scip_path), _SKIP_PARSE_ERROR, _now()))
        conn.commit()
        raise ValueError(f"corrupt SCIP index: {scip_path} ({e})") from e


def _resolve_target(
    spans_by_rel: Dict[str, Optional[List[Any]]],
    def_sites: Dict[str, List[Tuple[str, Optional[Tuple[int, int, int, int]]]]],
    symbol: str,
) -> Tuple[Optional[str], Optional[str], bool]:
    """(target_id, resolution, drop): exact via the definition occurrence's own
    position join; unresolved when the symbol has no in-workspace definition
    site; drop when it has one that fails the join (a counted miss, never a
    name-keyed guess)."""
    saw_unjoinable = False
    for rel, span in def_sites.get(symbol, ()):
        spans = spans_by_rel.get(_norm_rel(rel))
        if spans is None or span is None:
            continue
        target = _innermost(spans, span)
        if target is not None:
            return target, "exact", False
        saw_unjoinable = True
    if saw_unjoinable:
        return None, None, True
    return None, "unresolved", False


def _plan_document(
    doc,
    spans_by_rel: Dict[str, Optional[List[Any]]],
    def_sites: Dict[str, List[Tuple[str, Optional[Tuple[int, int, int, int]]]]],
    symbol_kinds: Dict[str, int],
    record: Dict[str, Any],
) -> Tuple[List[Tuple[Any, ...]], bool]:
    """(edge rows, anomalous) for one document; unmatched files and unjoinable
    occurrences are counted, not written, and a position-join rate below
    _JOIN_ANOMALY_THRESHOLD flags the document anomalous (definition
    occurrences excluded)."""
    spans = spans_by_rel.get(_norm_rel(doc.relative_path))
    if spans is None:
        return [], False
    total = 0
    joined = 0
    rows: List[Tuple[Any, ...]] = []
    for occ in doc.occurrences:
        if occ.symbol_roles & _ROLE_DEFINITION:
            continue
        total += 1
        span = _occurrence_span(occ)
        if span is None:
            record["unjoined_occurrences"] += 1
            continue
        source_id = _innermost(spans, span)
        if source_id is None:
            record["unjoined_occurrences"] += 1
            continue
        joined += 1
        record["joined_occurrences"] += 1

        target_id, resolution, dropped = _resolve_target(spans_by_rel, def_sites, occ.symbol)
        if dropped:
            record["unjoined_occurrences"] += 1
            continue

        kind = (
            "calls"
            if symbol_kinds.get(occ.symbol) in (_KIND_FUNCTION, _KIND_METHOD)
            else "references"
        )
        record[kind] += 1
        if resolution == "unresolved":
            record["unresolved"] += 1
        else:
            record["exact"] += 1
        rows.append((
            _new_id(),
            source_id,
            target_id,
            _terminal_name(occ.symbol),
            kind,
            span[0] + 1,  # tree-sitter lines are 1-based
            span[2],
            resolution,
        ))
    anomalous = total > 0 and joined / total < _JOIN_ANOMALY_THRESHOLD
    return rows, anomalous


def _count_disagreements(
    conn: sqlite3.Connection, file_id: str, rows: List[Tuple[Any, ...]]
) -> Tuple[int, int]:
    """(disagreements, upgrades) between planned scip edges and the file's
    tree-sitter edges, matched by (kind, line): both sides resolved to
    different targets counts a disagreement; an ambiguous/unresolved
    tree-sitter site the scip edge resolves exact counts an upgrade (FR-014).
    The scip edge wins either way."""
    sites: Dict[Tuple[str, int], List[Tuple[Optional[str], str]]] = {}
    for kind, line, target_id, resolution in conn.execute(_TS_SITES_SQL, (file_id,)):
        sites.setdefault((kind, line), []).append((target_id, resolution))
    disagreements = 0
    upgrades = 0
    for row in rows:
        matches = sites.get((row[4], row[5]))
        if not matches:
            continue
        if row[7] == "exact" and any(
            res in ("ambiguous", "unresolved") for _, res in matches
        ):
            upgrades += 1
        target = row[2]
        if target is not None and any(
            tid is not None and tid != target for tid, _ in matches
        ):
            disagreements += 1
    return disagreements, upgrades


def import_scip_file(conn: sqlite3.Connection, scip_path: str, workspace: str) -> Dict[str, Any]:
    """Import one .scip index as an edges-only overlay onto the built graph; returns the import record."""
    if not scip_available():
        raise ImportError(_INSTALL_HINT)
    index = _parse_index(conn, str(scip_path))

    record: Dict[str, Any] = {
        "index": str(scip_path),
        "documents": len(index.documents),
        "matched_documents": 0,
        "skipped_documents": 0,
        "edges": 0,
        "calls": 0,
        "references": 0,
        "exact": 0,
        "unresolved": 0,
        "joined_occurrences": 0,
        "unjoined_occurrences": 0,
        "join_anomalies": 0,
        "disagreements": 0,
        "upgrades": 0,
    }

    # Whole-index validate pass: target kinds and definition sites, no writes.
    symbol_kinds: Dict[str, int] = {i.symbol: i.kind for i in index.external_symbols}
    def_sites: Dict[str, List[Tuple[str, Optional[Tuple[int, int, int, int]]]]] = {}
    for doc in index.documents:
        for info in doc.symbols:
            symbol_kinds[info.symbol] = info.kind
        for occ in doc.occurrences:
            if occ.symbol_roles & _ROLE_DEFINITION:
                def_sites.setdefault(occ.symbol, []).append(
                    (doc.relative_path, _occurrence_span(occ))
                )

    # Match every document before planning: an abort must leave no partial overlay.
    repo_ids = _workspace_repo_ids(workspace)
    spans_by_rel: Dict[str, Optional[List[Any]]] = {}
    covered_file_ids: Dict[str, str] = {}
    covered_repo_ids: Dict[str, str] = {}
    for doc in index.documents:
        rel = _norm_rel(doc.relative_path)
        match = _match_document_path(conn, rel, repo_ids)
        if match is None:
            spans_by_rel[rel] = None
            record["skipped_documents"] += 1
        else:
            file_id, repo_id = match
            spans_by_rel[rel] = conn.execute(_SYMBOL_SPANS_SQL, (file_id,)).fetchall()
            covered_file_ids[rel] = file_id
            covered_repo_ids[rel] = repo_id
            record["matched_documents"] += 1

    planned: List[Tuple[str, bool, List[Tuple[Any, ...]]]] = []
    for doc in index.documents:
        rows, anomalous = _plan_document(doc, spans_by_rel, def_sites, symbol_kinds, record)
        planned.append((_norm_rel(doc.relative_path), anomalous, rows))

    cur = conn.cursor()
    kept_rows: List[Tuple[Any, ...]] = []
    for rel, anomalous, rows in planned:
        matched_id: Optional[str] = covered_file_ids.get(rel)
        if matched_id is None:
            continue
        if anomalous:
            cur.execute(
                _INSERT_ANOMALY_SKIP,
                (_new_id(), covered_repo_ids[rel], rel, _SKIP_JOIN_ANOMALY, _now()),
            )
            record["join_anomalies"] += 1
            continue
        disagreements, upgrades = _count_disagreements(conn, matched_id, rows)
        record["disagreements"] += disagreements
        record["upgrades"] += upgrades
        cur.execute(_DELETE_FILE_CALLREF_EDGES, (matched_id,))
        kept_rows.extend(rows)
    if kept_rows:
        cur.executemany(_INSERT_EDGE, kept_rows)
    conn.commit()
    record["edges"] = len(kept_rows)
    return record
