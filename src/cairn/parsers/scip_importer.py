"""SCIP (Sourcegraph Code Intelligence Protocol) index importer for cairn.

Converts pre-built SCIP indexes into cairn symbols and **exact** call edges.
Cairn is a *consumer* of SCIP indexes, never a producer -- indexes are
generated out-of-band (CI, a make target) by compiler-grade indexers
(``scip-kotlin``, ``scip-typescript``, ...) and pointed at via ``cairn.json``.

Format support:
- **Protobuf** (the default, what real indexers emit): parsed via the vendored
  generated stub ``_scip_pb2`` + the real ``protobuf`` runtime. Detected by
  magic byte: protobuf ``Index.documents`` starts with ``0x12`` (field 2,
  wire type 2); JSON starts with ``{`` (``0x7b``).
- **JSON** (legacy, kept for the historical test path only).

Resolution model: SCIP carries exact symbol bindings -- every reference
occurrence names the definition it points to, so resolution is a single dict
lookup instead of tree-sitter's 4-tier resolver. A reference whose target
isn't in the index (stdlib, external) is tagged ``resolution='unresolved'``.

Provenance: every symbol this importer writes is tagged ``source='scip'`` so
the build pipeline can tell SCIP data from tree-sitter data. ``INSERT OR
IGNORE`` on ``files``/``symbols`` keeps tree-sitter rows intact when both
paths touch the same workspace (the hybrid build skips tree-sitter for
SCIP-covered languages, but ``import-scip`` against an already-built DB must
not clobber).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

# --- SCIP symbol_roles bitmask ----------------------------------------------
# See https://github.com/sourcegraph/scip/blob/main/scip.proto
_SCIP_ROLE_DEFINITION = 1            # 1 << 0
_SCIP_ROLE_IMPORT = 2                # 1 << 1
_SCIP_ROLE_FORWARD_DEFINITION = 4    # 1 << 2
_SCIP_ROLE_READ_ACCESS = 64          # 1 << 6
_SCIP_ROLE_WRITE_ACCESS = 128        # 1 << 7
_SCIP_ROLE_ACCESS_MASK = _SCIP_ROLE_READ_ACCESS | _SCIP_ROLE_WRITE_ACCESS

# --- protobuf availability --------------------------------------------------
# Tree-sitter-only builds never import the stub; a missing/mismatched protobuf
# runtime degrades with an install hint rather than crashing the build.
try:
    from google.protobuf.message import DecodeError as _ProtoDecodeError
    from . import _scip_pb2 as _scip  # noqa: F401 -- re-exported below
    _PROTOBUF_AVAILABLE = True
except ImportError:
    _scip = None  # type: ignore[assignment]
    _ProtoDecodeError = Exception  # type: ignore[assignment,misc]
    _PROTOBUF_AVAILABLE = False


def scip_available() -> bool:
    """True iff the protobuf runtime + vendored stub import cleanly."""
    return _PROTOBUF_AVAILABLE


def _install_hint() -> str:
    return (
        "SCIP support requires the optional protobuf dependency. "
        "Install it with: uv tool install 'cairn-intel[scip]' --force "
        "(or `pip install cairn-intel[scip]`)."
    )


# --- range extraction -------------------------------------------------------

def _extract_range(occ) -> Tuple[int, int, int, int, int]:
    """Read an Occurrence's range into (line_start, col_start, line_end, col_end, _).

    Prefers the typed ``single_line_range``/``multi_line_range`` oneof (what
    up-to-date indexers emit); falls back to the deprecated ``repeated int32
    ``range`` only if the oneof is unset. Returns 1-based line_start (SCIP is
    0-based, cairn stores 1-based). Columns stay 0-based (byte offsets), like
    tree-sitter's column_start/column_end.
    """
    which = occ.WhichOneof("typed_range") if hasattr(occ, "WhichOneof") else None
    if which == "single_line_range":
        r = occ.single_line_range
        line = r.line + 1
        return (line, r.start_character, line, r.end_character, line)
    if which == "multi_line_range":
        r = occ.multi_line_range
        return (r.start_line + 1, r.start_character, r.end_line + 1, r.end_character, r.end_line + 1)
    # Deprecated repeated int32 form: [startLine, startChar, endChar] or
    # [startLine, startChar, endLine, endChar] (0-based).
    rng = list(occ.range) if hasattr(occ, "range") else []
    if not rng:
        return (1, 0, 1, 0, 1)
    sl = rng[0] + 1
    sc = rng[1] if len(rng) > 1 else 0
    if len(rng) >= 4:
        return (sl, sc, rng[2] + 1, rng[3], rng[2] + 1)
    return (sl, sc, sl, rng[2] if len(rng) > 2 else sc, sl)


def _enclosing_range(occ) -> Optional[Tuple[int, int, int, int]]:
    """Nearest enclosing AST range for an occurrence, for source-symbol lookup.

    Prefers typed_enclosing_range, falls back to the deprecated enclosing_range.
    Returns (start_line_1based, start_col, end_line_1based, end_col) or None.
    """
    which = occ.WhichOneof("typed_enclosing_range") if hasattr(occ, "WhichOneof") else None
    if which == "single_line_enclosing_range":
        r = occ.single_line_enclosing_range
        return (r.line + 1, r.start_character, r.line + 1, r.end_character)
    if which == "multi_line_enclosing_range":
        r = occ.multi_line_enclosing_range
        return (r.start_line + 1, r.start_character, r.end_line + 1, r.end_character)
    er = list(occ.enclosing_range) if hasattr(occ, "enclosing_range") else []
    if not er:
        return None
    sl = er[0] + 1
    sc = er[1] if len(er) > 1 else 0
    if len(er) >= 4:
        return (sl, sc, er[2] + 1, er[3])
    return (sl, sc, sl, er[2] if len(er) > 2 else sc)


# --- kind mapping -----------------------------------------------------------

# Coarse SyntaxKind -> cairn kind. Graph traversal keys on edges, not kinds;
# this just keeps the symbols table legible.
_SYNTAX_KIND_MAP = {
    16: "function",   # IdentifierFunctionDefinition
    15: "function",   # IdentifierFunction
    18: "macro",      # IdentifierMacroDefinition
    17: "macro",      # IdentifierMacro
    19: "class",      # IdentifierType (non-builtin)
    20: "class",      # IdentifierBuiltinType
    21: "property",   # IdentifierAttribute
    14: "module",     # IdentifierNamespace
}


def _kind_from_syntax(syntax_kind: int) -> str:
    return _SYNTAX_KIND_MAP.get(syntax_kind, "scip_symbol")


def _short_name(symbol_descriptor: str) -> str:
    """The human-facing name from a SCIP descriptor (last path segment)."""
    # SCIP descriptors look like "scip-python python repo main main_func ."
    # or "scip-kotlin com example Foo#bar().". The final segment is the name.
    cleaned = symbol_descriptor.rstrip(".").rstrip("#")
    last = cleaned.split(" ")[-1]
    # Some descriptors encode "Owner#member" or "path/Name" -- take the tail.
    for sep in ("#", "/"):
        if sep in last:
            last = last.rsplit(sep, 1)[-1]
    return last or "scip_symbol"


def _resolve_doc_path(rel_path: str, ws_root: Optional[Path], fallback_repo: str):
    """Map a SCIP Document.relative_path to (repo_id, repo_relative_path).

    SCIP documents carry paths relative to the project root (workspace root),
    e.g. ``"demo/Foo.kt"``. Cairn stores ``files.path`` REPO-relative
    (``"Foo.kt"``) keyed by the inferred repo id, so the incremental path
    (``reindex_paths``) and the scanner agree on a file's identity. When
    ``ws_root`` is given, resolve each path through the scanner; otherwise fall
    back to the legacy single-repo shape (repo_id=fallback, path=rel_path).
    """
    if ws_root is None:
        return fallback_repo, rel_path
    from cairn.graph import scanner
    abs_path = str(ws_root / rel_path)
    repo = scanner.infer_repo_for_path(abs_path, str(ws_root)) or fallback_repo
    try:
        repo_root = scanner.resolve_repo_path(str(ws_root), repo)
        rel_to_repo = str(Path(abs_path).relative_to(repo_root))
    except Exception:
        rel_to_repo = rel_path
    return repo, rel_to_repo


# --- protobuf import --------------------------------------------------------

def _import_protobuf(conn, index, repo_id: str, ws_root: Optional[Path] = None) -> dict:
    """Two-pass import of a parsed ``scip_pb2.Index``.

    Pass 1 collects every Definition occurrence into a descriptor map; pass 2
    emits symbols + edges with real target resolution. Reads typed ranges
    (falling back to deprecated fields), never clobbers existing tree-sitter
    rows, and tags every symbol ``source='scip'``. When ``ws_root`` is given,
    each document's path is normalized to (repo_id, repo-relative path) via the
    scanner so SCIP rows align with the tree-sitter/incremental file identity.
    """
    cur = conn.cursor()
    files_added = symbols_added = edges_added = 0

    # Pre-index SymbolInformation.documentation by descriptor for docstring
    # attachment on definition symbols.
    docs: Dict[str, str] = {}
    for doc in index.documents:
        for info in doc.symbols:
            d = getattr(info, "documentation", None)
            if d and info.symbol:
                docs[info.symbol] = "\n\n".join(d) if isinstance(d, (list, tuple)) else str(d)
    for info in index.external_symbols:
        d = getattr(info, "documentation", None)
        if d and info.symbol:
            docs[info.symbol] = "\n\n".join(d) if isinstance(d, (list, tuple)) else str(d)

    # Pass 1: collect definitions {descriptor -> (sym_id, file_id, line, col)}.
    # Resolve each document's path to (repo_id, repo-relative) once.
    doc_paths: Dict[int, Tuple[str, str]] = {}
    defs: Dict[str, Tuple[str, str, int, int]] = {}
    for i, doc in enumerate(index.documents):
        rel = doc.relative_path
        doc_repo, rel_to_repo = _resolve_doc_path(rel, ws_root, repo_id)
        doc_paths[i] = (doc_repo, rel_to_repo)
        file_id = f"{doc_repo}:{rel_to_repo}"
        for occ in doc.occurrences:
            if not (occ.symbol_roles & (_SCIP_ROLE_DEFINITION | _SCIP_ROLE_FORWARD_DEFINITION)):
                continue
            sl, sc, _, _, _ = _extract_range(occ)
            sym_id = f"{file_id}:{_short_name(occ.symbol)}:{sl}:{sc}"
            defs[occ.symbol] = (sym_id, file_id, sl, sc)

    # Pass 2: emit symbols + edges.
    for i, doc in enumerate(index.documents):
        doc_repo, rel = doc_paths[i]
        lang = getattr(doc, "language", "") or ""
        file_id = f"{doc_repo}:{rel}"

        # repos + files: INSERT OR IGNORE so we never overwrite tree-sitter
        # metadata (hash/line_count/size/mtime).
        cur.execute(
            "INSERT OR IGNORE INTO repos (id, name, path) VALUES (?, ?, ?)",
            (doc_repo, doc_repo, "."),
        )
        cur.execute(
            "INSERT OR IGNORE INTO files (id, path, repo_id, hash, line_count, language) "
            "VALUES (?, ?, ?, 'scip_imported', 0, ?)",
            (file_id, rel, doc_repo, lang or "scip"),
        )
        files_added += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

        # Per-file: track definitions in line order so occurrences lacking an
        # enclosing_range fall back to the nearest preceding definition.
        file_defs_by_line: list[Tuple[int, str]] = []

        for occ in doc.occurrences:
            sym_descriptor = occ.symbol
            if not sym_descriptor:
                continue
            sl, sc, el, ec, _ = _extract_range(occ)
            is_def = bool(occ.symbol_roles & (_SCIP_ROLE_DEFINITION | _SCIP_ROLE_FORWARD_DEFINITION))
            name = _short_name(sym_descriptor)

            if is_def:
                sym_id = defs.get(sym_descriptor, (f"{file_id}:{name}:{sl}:{sc}", file_id, sl, sc))[0]
                kind = _kind_from_syntax(occ.syntax_kind)
                docstring = docs.get(sym_descriptor)
                cur.execute(
                    """INSERT OR IGNORE INTO symbols
                       (id, file_id, name, qualified_name, kind,
                        line_start, line_end, column_start, column_end,
                        docstring, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'scip')""",
                    (sym_id, file_id, name, sym_descriptor, kind, sl, el, sc, ec, docstring),
                )
                if cur.rowcount > 0:
                    symbols_added += 1
                file_defs_by_line.append((sl, sym_id))
                continue

            # Non-definition: resolve target via the Pass-1 map.
            target = defs.get(sym_descriptor)
            target_id = target[0] if target else None
            resolution = "exact" if target else "unresolved"

            # Source symbol: enclosing definition in this file.
            source_id = None
            enc = _enclosing_range(occ)
            if enc is None:
                # Fall back to nearest preceding definition in line order.
                for dline, did in reversed(file_defs_by_line):
                    if dline <= sl:
                        source_id = did
                        break
            else:
                el_start = enc[0]
                for dline, did in reversed(file_defs_by_line):
                    if dline <= el_start:
                        source_id = did
                        break

            # Classify edge kind from roles.
            if occ.symbol_roles & _SCIP_ROLE_IMPORT:
                edge_kind = "import"
            elif occ.symbol_roles & _SCIP_ROLE_ACCESS_MASK:
                edge_kind = "reference"
            else:
                edge_kind = "call"

            edge_id = f"{file_id}:{name}:{sl}:{sc}:{abs(hash(sym_descriptor)) % 100000}"
            cur.execute(
                """INSERT OR REPLACE INTO edges
                   (id, source_id, target_id, target_name, kind, line, column, resolution)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (edge_id, source_id, target_id, name, edge_kind, sl, sc, resolution),
            )
            if cur.rowcount > 0:
                edges_added += 1

    conn.commit()
    return {"files_added": files_added, "symbols_added": symbols_added, "edges_added": edges_added}


# --- public API -------------------------------------------------------------

def import_scip_bytes(
    conn: sqlite3.Connection,
    data: bytes,
    repo_id: str = "default",
    ws_root: Optional[Path] = None,
) -> dict:
    """Import a raw SCIP protobuf ``Index`` (serialized bytes) into the DB.

    When ``ws_root`` is given, each document's ``relative_path`` is normalized
    to ``(repo_id, repo-relative path)`` via the scanner so SCIP rows share the
    same file identity as tree-sitter/incremental rows. Raises ``ImportError``
    if the optional ``[scip]`` dependency isn't installed.
    """
    if not _PROTOBUF_AVAILABLE:
        raise ImportError(_install_hint())
    try:
        index = _scip.Index.FromString(data)  # type: ignore[union-attr]
    except _ProtoDecodeError as e:  # type: ignore[arg-type]
        raise ValueError(f"data is not a valid SCIP protobuf Index: {e}") from e
    return _import_protobuf(conn, index, repo_id, ws_root=ws_root)


def import_scip_data(conn: sqlite3.Connection, scip_dict: Dict[str, Any], repo_id: str = "default") -> dict:
    """Import a SCIP index (JSON dict) into cairn's database.

    Reads the same JSON shape real SCIP tooling emits via ``--json``:
    ``{"documents": [{"relative_path", "language", "occurrences": [...]}]}``,
    each occurrence carrying ``symbol``, ``symbol_roles``, ``range`` (the
    deprecated ``[startLine, startChar, endChar|endLine, endChar]`` form), and
    optionally ``syntax_kind``. Two-pass resolution (definitions collected
    first, then references resolved against them) produces real ``resolution``
    instead of the legacy "always exact" placeholder.
    """
    cur = conn.cursor()
    files_added = symbols_added = edges_added = 0

    documents = scip_dict.get("documents", [])
    # Pass 1: collect definitions.
    defs: Dict[str, Tuple[str, str, int, int]] = {}
    for doc in documents:
        rel = doc.get("relative_path") or doc.get("path")
        if not rel:
            continue
        file_id = f"{repo_id}:{rel}"
        for occ in doc.get("occurrences", []):
            roles = occ.get("symbol_roles", 0) or 0
            if not (roles & (_SCIP_ROLE_DEFINITION | _SCIP_ROLE_FORWARD_DEFINITION)):
                continue
            rng = occ.get("range") or [0, 0, 0, 0]
            sl = (rng[0] if rng else 0) + 1
            sc = rng[1] if len(rng) > 1 else 0
            sym_desc = occ.get("symbol", "")
            defs[sym_desc] = (f"{file_id}:{_short_name(sym_desc)}:{sl}:{sc}", file_id, sl, sc)

    # Pass 2: emit.
    for doc in documents:
        rel = doc.get("relative_path") or doc.get("path")
        if not rel:
            continue
        lang = doc.get("language") or "scip"
        file_id = f"{repo_id}:{rel}"

        cur.execute(
            "INSERT OR IGNORE INTO repos (id, name, path) VALUES (?, ?, ?)",
            (repo_id, repo_id, "."),
        )
        cur.execute(
            "INSERT OR IGNORE INTO files (id, path, repo_id, hash, line_count, language) "
            "VALUES (?, ?, ?, 'scip_imported', 0, ?)",
            (file_id, rel, repo_id, lang),
        )

        file_defs_by_line: list[Tuple[int, str]] = []
        for occ in doc.get("occurrences", []):
            sym_desc = occ.get("symbol", "")
            if not sym_desc:
                continue
            rng = occ.get("range") or [0, 0, 0, 0]
            sl = (rng[0] if rng else 0) + 1
            sc = rng[1] if len(rng) > 1 else 0
            el = (rng[2] + 1 if len(rng) >= 4 else sl)
            ec = rng[3] if len(rng) >= 4 else (rng[2] if len(rng) > 2 else sc)
            roles = occ.get("symbol_roles", 0) or 0
            is_def = bool(roles & (_SCIP_ROLE_DEFINITION | _SCIP_ROLE_FORWARD_DEFINITION))
            name = _short_name(sym_desc)

            if is_def:
                sym_id = defs.get(sym_desc, (f"{file_id}:{name}:{sl}:{sc}", file_id, sl, sc))[0]
                kind = _kind_from_syntax(occ.get("syntax_kind", 0) or 0)
                cur.execute(
                    """INSERT OR IGNORE INTO symbols
                       (id, file_id, name, qualified_name, kind,
                        line_start, line_end, column_start, column_end, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'scip')""",
                    (sym_id, file_id, name, sym_desc, kind, sl, el, sc, ec),
                )
                if cur.rowcount > 0:
                    symbols_added += 1
                file_defs_by_line.append((sl, sym_id))
                continue

            target = defs.get(sym_desc)
            target_id = target[0] if target else None
            resolution = "exact" if target else "unresolved"
            source_id = None
            for dline, did in reversed(file_defs_by_line):
                if dline <= sl:
                    source_id = did
                    break
            if roles & _SCIP_ROLE_IMPORT:
                edge_kind = "import"
            elif roles & _SCIP_ROLE_ACCESS_MASK:
                edge_kind = "reference"
            else:
                edge_kind = "call"
            edge_id = f"{file_id}:{name}:{sl}:{sc}:{abs(hash(sym_desc)) % 100000}"
            cur.execute(
                """INSERT OR REPLACE INTO edges
                   (id, source_id, target_id, target_name, kind, line, column, resolution)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (edge_id, source_id, target_id, name, edge_kind, sl, sc, resolution),
            )
            if cur.rowcount > 0:
                edges_added += 1

    conn.commit()
    files_added = len(documents)
    return {"files_added": files_added, "symbols_added": symbols_added, "edges_added": edges_added}


def import_scip_file(
    conn: sqlite3.Connection,
    scip_path: Union[str, Path],
    repo_id: str = "default",
    fmt: str = "proto",
    ws_root: Optional[Path] = None,
) -> dict:
    """Import a SCIP index file. ``fmt`` selects ``'proto'`` (default) or ``'json'``.

    ``ws_root`` (proto only) normalizes each document's path to
    ``(repo_id, repo-relative)`` so SCIP rows share file identity with the
    tree-sitter/incremental paths; pass the workspace root for build-integrated
    imports, leave None for the standalone ``import-scip`` escape hatch.
    """
    path = Path(scip_path)
    if not path.exists():
        raise FileNotFoundError(f"SCIP index file not found: {scip_path}")

    if fmt == "json":
        import json
        data = json.loads(path.read_text(encoding="utf-8"))
        return import_scip_data(conn, data, repo_id=repo_id)

    if fmt != "proto":
        raise ValueError(f"unknown SCIP format {fmt!r} (use 'proto' or 'json')")

    if not _PROTOBUF_AVAILABLE:
        raise ImportError(_install_hint())
    return import_scip_bytes(conn, path.read_bytes(), repo_id=repo_id, ws_root=ws_root)
