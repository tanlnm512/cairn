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

import hashlib
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
except Exception:
    # A missing runtime OR a protobuf runtime older than the stub's gencode
    # version (ValidateProtobufRuntimeVersion raises VersionError, a subclass
    # of Exception -- not ImportError) both degrade the same way: report "SCIP
    # extra not installed" with the install hint rather than crashing the build.
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


# --- language inference from path -------------------------------------------
# Some indexers emit an empty Document.language (scip-java 0.10.4 leaves it
# blank for both Java and Kotlin docs). The hybrid skip logic and downstream
# tooling key off files.language, so fall back to the file extension when the
# document doesn't declare one. Covers the languages cairn's scanner knows.
_EXT_LANGUAGE: Dict[str, str] = {
    ".swift": "swift", ".kt": "kotlin", ".kts": "kotlin",
    ".java": "java", ".py": "python", ".ts": "typescript", ".tsx": "typescript",
    ".mts": "typescript", ".cts": "typescript", ".js": "javascript",
    ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".dart": "dart", ".go": "go", ".rs": "rust",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
    ".cxx": "cpp", ".m": "objc", ".mm": "objc",
}


def _resolve_real_file_id(conn, repo: str, rel: str, fallback: str) -> str:
    """Return the file row id SCIP symbols/edges should reference.

    In a coexistence build, tree-sitter parses every file first and creates a
    ``files`` row with a uuid ``id`` and a real hash (``hash != 'scip_imported'``).
    SCIP must link its symbols/edges to THAT row so JOINs work and the two
    sources share one file identity. If no tree-sitter row exists (the
    standalone ``import-scip`` escape hatch against a fresh DB), fall back to
    the deterministic ``{repo}:{rel}`` shadow id and let the caller insert it.
    """
    try:
        row = conn.execute(
            "SELECT id FROM files WHERE repo_id = ? AND path = ? AND hash != 'scip_imported' "
            "ORDER BY id LIMIT 1",
            (repo, rel),
        ).fetchone()
    except Exception:
        row = None
    return row[0] if row else fallback


def _language_for(rel_path: str, declared: str) -> str:
    """Resolve a document's language, falling back to the file extension.

    Real indexers don't all populate ``Document.language``: scip-java 0.10.4
    leaves it empty for both Java and Kotlin, so the importer would store
    ``'scip'`` and the hybrid skip logic couldn't tell those files apart.
    Derive from the extension when the declared value is empty, and normalize
    case (scip-swift emits ``"Swift"``; scanner keys are lowercase).
    """
    if declared:
        return declared.lower()
    suffix = Path(rel_path).suffix.lower()
    return _EXT_LANGUAGE.get(suffix, "scip")


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


def _resolve_doc_path(
    rel_path: str,
    ws_root: Optional[Path],
    fallback_repo: str,
    project_root: Optional[Path] = None,
):
    """Map a SCIP Document.relative_path to (repo_id, repo_relative_path).

    Real indexers emit ``relative_path`` relative to their OWN
    ``Metadata.project_root`` (the repo dir), NOT the workspace root. When
    ``project_root`` is given (resolved against ``ws_root`` once per index), it
    is used as the base; otherwise we fall back to ``ws_root``. Cairn stores
    ``files.path`` REPO-relative (``"Foo.kt"``) keyed by the inferred repo id,
    so the incremental path (``reindex_paths``) and the scanner agree on a
    file's identity. When ``ws_root`` is given, resolve each path through the
    scanner; otherwise fall back to the legacy single-repo shape
    (repo_id=fallback, path=rel_path).
    """
    if ws_root is None:
        return fallback_repo, rel_path
    from cairn.graph import scanner
    base = project_root if project_root is not None else ws_root
    abs_path = str(base / rel_path)
    repo = scanner.infer_repo_for_path(abs_path, str(ws_root)) or fallback_repo
    try:
        repo_root = scanner.resolve_repo_path(str(ws_root), repo)
        rel_to_repo = str(Path(abs_path).relative_to(repo_root))
    except ValueError:
        # Path.relative_to raises ValueError when abs_path isn't under
        # repo_root; fall back to the raw relative_path. Other errors (OSError,
        # permission denied, ...) must propagate, not be swallowed.
        rel_to_repo = rel_path
    return repo, rel_to_repo


def _normalize_project_root(raw_root: str, ws_root: Path) -> Path:
    """Normalize ``Metadata.project_root`` into an absolute ``Path``.

    Real indexers use different conventions here:

    - scip-swift writes a ``file://`` URL (it does
      ``URL(fileURLWithPath: repoPath).absoluteString``), e.g.
      ``file:///Users/me/repo``.
    - scip-kotlin / scip-typescript emit a plain absolute path.
    - Some emit a path relative to the workspace.

    ``Path("file:///abs").is_absolute()`` is ``False`` (the ``file://`` prefix
    isn't a POSIX path marker), so without scheme handling the old inline
    expression joined the URL verbatim onto ``ws_root`` and produced garbage,
    silently mis-attributing every document. Strip any ``file://`` scheme first,
    then resolve absolute vs. relative exactly as before.
    """
    from urllib.parse import urlparse
    from urllib.request import url2pathname

    root = raw_root.strip()
    if root.startswith("file:"):
        parsed = urlparse(root)
        # urlparse("file:///a/b") -> scheme="file", netloc="", path="/a/b"
        # urlparse("file://localhost/a/b") -> netloc="localhost", path="/a/b"
        host = parsed.netloc or parsed.hostname or ""
        path = url2pathname(parsed.path)
        if host and host not in ("localhost", ""):
            # A non-empty non-localhost host is a network URL we can't map to a
            # local path; fall back to resolving the raw value below.
            local = None
        else:
            local = path
        if local:
            return Path(local).resolve()
    if Path(root).is_absolute():
        return Path(root).resolve()
    return (ws_root / root).resolve()


# --- coexistence merge ------------------------------------------------------

# Edge kinds tree-sitter emits that SCIP has no equivalent for. These survive
# the merge (SCIP replaces only call/reference/import edges; inheritance edges
# from tree-sitter are kept since SCIP's role bitmask has no inheritance bit).
_TS_ONLY_EDGE_KINDS = ("implements", "extends")

# Edge kinds the merge replaces: tree-sitter's calls (fuzzy resolution) are
# dropped in favor of SCIP's call/reference/import edges (exact resolution).
_REPLACEABLE_EDGE_KINDS = ("calls", "call", "reference", "import")


def _normalize_name_for_match(name: str) -> str:
    """Normalize a symbol name for cross-source matching.

    SCIP descriptors for callables often carry a trailing ``()`` (e.g.
    ``greet()`` from scip-java's semanticdb format), while tree-sitter extracts
    the bare identifier (``greet``). Strip trailing parens so the two match.
    scip-swift's opaque USRs (``\\`s:...\\``` ) won't match tree-sitter names
    regardless -- that's an inherent scip-swift limitation (opaque symbol
    identity), documented in the README.
    """
    return name.rstrip("()").rstrip(".") or name


def _merge_scip_defs_into_tree_sitter(conn, scip_def_rows: list) -> int:
    """Fold each SCIP definition symbol into its matching tree-sitter row.

    Coexistence model: tree-sitter parses every file (rich metadata), then SCIP
    imports exact-resolution edges. To get one row per symbol carrying both,
    each SCIP definition is matched to a tree-sitter symbol by
    ``(file_id, name, line_start)`` (falling back to name-only if the line
    disagrees). On a match:

    1. UPDATE the tree-sitter symbol: adopt SCIP's richer ``qualified_name``,
       ``docstring`` (when SCIP has one), and mark ``source='merged'``.
       Tree-sitter's ``modifiers``, ``body``, ``parent_scope``,
       ``imports_summary``, ``parameters``, ``return_type``, ``metadata`` are
       preserved (SCIP doesn't populate them).
    2. DELETE tree-sitter's calls/reference/import edges for that symbol
       (fuzzy resolution) -- SCIP's exact edges (already INSERT OR REPLACE'd)
       take over. ``implements``/``extends`` edges survive (SCIP can't emit
       inheritance).
    3. DELETE the SCIP shadow symbol row -- its data now lives on the merged
       tree-sitter row.

    Unmatched SCIP definitions (no tree-sitter row, e.g. a symbol tree-sitter's
    grammar missed) are left as standalone ``source='scip'`` rows.

    Returns the number of definitions merged.
    """
    if not scip_def_rows:
        return 0
    cur = conn.cursor()
    merged = 0
    for scip_sym_id, file_id, name, sl in scip_def_rows:
        # Normalize for matching: scip-java emits "greet()", tree-sitter "greet".
        match_name = _normalize_name_for_match(name)
        # Match by (file_id, name, line_start); fall back to name-only if the
        # exact line disagrees (tree-sitter and SCIP can differ on where a
        # definition's anchor lands).
        ts_row = cur.execute(
            "SELECT id FROM symbols WHERE file_id = ? AND name = ? AND line_start = ? "
            "AND id != ? AND source != 'scip' LIMIT 1",
            (file_id, match_name, sl, scip_sym_id),
        ).fetchone()
        if ts_row is None:
            ts_row = cur.execute(
                "SELECT id FROM symbols WHERE file_id = ? AND name = ? "
                "AND id != ? AND source != 'scip' LIMIT 1",
                (file_id, match_name, scip_sym_id),
            ).fetchone()
        if ts_row is None:
            continue  # no tree-sitter match -- leave the standalone SCIP row

        ts_sym_id = ts_row[0]

        # 1. Enrich the tree-sitter symbol with SCIP's higher-fidelity fields.
        cur.execute(
            "SELECT qualified_name, docstring FROM symbols WHERE id = ?",
            (scip_sym_id,),
        )
        scip_data = cur.fetchone()
        if scip_data:
            scip_qn, scip_doc = scip_data
            # COALESCE: keep tree-sitter's docstring if SCIP didn't carry one.
            cur.execute(
                """UPDATE symbols SET
                     qualified_name = COALESCE(?, qualified_name),
                     docstring = COALESCE(?, docstring),
                     source = 'merged'
                   WHERE id = ?""",
                (scip_qn, scip_doc, ts_sym_id),
            )

        # 2. Drop tree-sitter's fuzzy call/reference edges for this symbol
        #    FIRST, then re-point SCIP's exact edges from the shadow symbol to
        #    the merged row. Order matters: the DELETE must run before the
        #    re-point, otherwise the re-pointed SCIP edge (kind='call') gets
        #    caught by the kind-IN-(calls,call,...) delete.
        placeholders = ",".join("?" for _ in _REPLACEABLE_EDGE_KINDS)
        cur.execute(
            f"DELETE FROM edges WHERE source_id = ? AND kind IN ({placeholders})",
            (ts_sym_id, *_REPLACEABLE_EDGE_KINDS),
        )
        cur.execute(
            "UPDATE edges SET source_id = ? WHERE source_id = ?",
            (ts_sym_id, scip_sym_id),
        )
        cur.execute(
            "UPDATE edges SET target_id = ? WHERE target_id = ?",
            (ts_sym_id, scip_sym_id),
        )

        # 3. Remove the now-merged SCIP shadow row.
        cur.execute("DELETE FROM symbols WHERE id = ?", (scip_sym_id,))
        merged += 1
    return merged


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

    # Track SCIP definition rows inserted during Pass 2 so the merge pass can
    # fold each into its matching tree-sitter symbol (coexistence model).
    scip_def_rows: list[tuple] = []  # (scip_sym_id, file_id, name, line_start)

    # Real indexers emit Document.relative_path relative to their OWN
    # Metadata.project_root (the repo dir), not the workspace root. Read it once
    # per index and resolve against ws_root so per-document path resolution
    # matches how the indexer wrote the paths. Some indexes omit metadata;
    # access it defensively.
    project_root = None
    if ws_root is not None:
        meta = getattr(index, "metadata", None)
        raw_root = getattr(meta, "project_root", None) if meta is not None else None
        if raw_root:
            project_root = _normalize_project_root(raw_root, ws_root)

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
    # Resolve each document's path to (repo_id, repo-relative) once. A real
    # Definition (role 1) always wins over a ForwardDefinition (role 4) for the
    # same descriptor; a forward decl only fills in the map when no real def is
    # seen, so references still resolve without spurious symbol rows.
    doc_paths: Dict[int, Tuple[str, str]] = {}
    doc_file_ids: Dict[int, str] = {}
    defs: Dict[str, Tuple[str, str, int, int]] = {}
    real_defs: set = set()
    for i, doc in enumerate(index.documents):
        rel = doc.relative_path
        doc_repo, rel_to_repo = _resolve_doc_path(rel, ws_root, repo_id, project_root=project_root)
        doc_paths[i] = (doc_repo, rel_to_repo)
        # Coexistence: link to the tree-sitter file row if it exists so both
        # sources share one file identity (JOINs work, incremental clears both).
        doc_file_ids[i] = _resolve_real_file_id(
            conn, doc_repo, rel_to_repo, f"{doc_repo}:{rel_to_repo}")
        file_id = doc_file_ids[i]
        for occ in doc.occurrences:
            roles = occ.symbol_roles
            if not (roles & (_SCIP_ROLE_DEFINITION | _SCIP_ROLE_FORWARD_DEFINITION)):
                continue
            is_real = bool(roles & _SCIP_ROLE_DEFINITION)
            if is_real:
                real_defs.add(occ.symbol)
            # Don't overwrite a real def with a forward decl (last-writer-wins
            # would otherwise let `class Foo;` clobber the real definition).
            if not is_real and occ.symbol in real_defs:
                continue
            sl, sc, _, _, _ = _extract_range(occ)
            sym_id = f"{file_id}:{_short_name(occ.symbol)}:{sl}:{sc}"
            defs[occ.symbol] = (sym_id, file_id, sl, sc)

    # Pass 2: emit symbols + edges.
    for i, doc in enumerate(index.documents):
        doc_repo, rel = doc_paths[i]
        lang = _language_for(rel, getattr(doc, "language", "") or "")
        file_id = doc_file_ids[i]

        # repos + files: INSERT OR IGNORE so we never overwrite tree-sitter
        # metadata (hash/line_count/size/mtime). When coexisting with
        # tree-sitter, file_id already points at the real row and this is a
        # no-op; in standalone mode it inserts the shadow row.
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
            is_real_def = bool(occ.symbol_roles & _SCIP_ROLE_DEFINITION)
            name = _short_name(sym_descriptor)

            if is_def:
                sym_id = defs.get(sym_descriptor, (f"{file_id}:{name}:{sl}:{sc}", file_id, sl, sc))[0]
                # Only real definitions get a symbols row; a pure forward decl
                # (role 4 alone) is resolvable via defs but emits no symbol.
                if is_real_def:
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
                        scip_def_rows.append((sym_id, file_id, name, sl))
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

            # A file-level occurrence with no enclosing definition (top-level
            # code in a Swift main.swift, a reference before any definition,
            # etc.) has no owning symbol to attribute the edge to. Skip it --
            # the tree-sitter path does the same (builder: "file-level call
            # with no owning symbol; cannot attach"). edges.source_id is NOT
            # NULL, so a NULL here would crash the import (found against real
            # scip-swift output).
            if source_id is None:
                continue

            # Classify edge kind from roles.
            if occ.symbol_roles & _SCIP_ROLE_IMPORT:
                edge_kind = "import"
            elif occ.symbol_roles & _SCIP_ROLE_ACCESS_MASK:
                edge_kind = "reference"
            else:
                edge_kind = "call"

            edge_id = f"{file_id}:{name}:{sl}:{sc}:{hashlib.sha1(sym_descriptor.encode('utf-8'), usedforsecurity=False).hexdigest()[:8]}"
            cur.execute(
                """INSERT OR REPLACE INTO edges
                   (id, source_id, target_id, target_name, kind, line, column, resolution)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (edge_id, source_id, target_id, name, edge_kind, sl, sc, resolution),
            )
            if cur.rowcount > 0:
                edges_added += 1

    # Coexistence merge: fold each SCIP definition into its matching tree-sitter
    # symbol so one row carries both sources' strengths (tree-sitter's
    # modifiers/body/parent_scope + SCIP's exact edges/richer qualified_name).
    merged = _merge_scip_defs_into_tree_sitter(conn, scip_def_rows)

    conn.commit()
    return {
        "files_added": files_added,
        "symbols_added": symbols_added,
        "edges_added": edges_added,
        "symbols_merged": merged,
    }


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
    # Pass 1: collect definitions. A real Definition (role 1) wins over a
    # ForwardDefinition (role 4); a forward decl only fills the map as a
    # fallback so references still resolve without emitting a symbol row.
    defs: Dict[str, Tuple[str, str, int, int]] = {}
    real_defs: set = set()
    for doc in documents:
        rel = doc.get("relative_path") or doc.get("path")
        if not rel:
            continue
        file_id = f"{repo_id}:{rel}"
        for occ in doc.get("occurrences", []):
            roles = occ.get("symbol_roles", 0) or 0
            if not (roles & (_SCIP_ROLE_DEFINITION | _SCIP_ROLE_FORWARD_DEFINITION)):
                continue
            is_real = bool(roles & _SCIP_ROLE_DEFINITION)
            sym_desc = occ.get("symbol", "")
            if is_real:
                real_defs.add(sym_desc)
            elif sym_desc in real_defs:
                continue
            rng = occ.get("range") or [0, 0, 0, 0]
            sl = (rng[0] if rng else 0) + 1
            sc = rng[1] if len(rng) > 1 else 0
            defs[sym_desc] = (f"{file_id}:{_short_name(sym_desc)}:{sl}:{sc}", file_id, sl, sc)

    # Pass 2: emit.
    for doc in documents:
        rel = doc.get("relative_path") or doc.get("path")
        if not rel:
            continue
        lang = _language_for(rel, doc.get("language") or "")
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
        files_added += cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0

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
            is_real_def = bool(roles & _SCIP_ROLE_DEFINITION)
            name = _short_name(sym_desc)

            if is_def:
                sym_id = defs.get(sym_desc, (f"{file_id}:{name}:{sl}:{sc}", file_id, sl, sc))[0]
                # Only real definitions get a symbols row; a pure forward decl
                # (role 4 alone) is resolvable via defs but emits no symbol.
                if is_real_def:
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
            # No enclosing definition -> no owning symbol (see proto path).
            if source_id is None:
                continue
            if roles & _SCIP_ROLE_IMPORT:
                edge_kind = "import"
            elif roles & _SCIP_ROLE_ACCESS_MASK:
                edge_kind = "reference"
            else:
                edge_kind = "call"
            edge_id = f"{file_id}:{name}:{sl}:{sc}:{hashlib.sha1(sym_desc.encode('utf-8'), usedforsecurity=False).hexdigest()[:8]}"
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
