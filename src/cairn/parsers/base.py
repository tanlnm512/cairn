"""Base parser interface and shared data model.

All language parsers implement BaseParser.parse(path) -> ParsedFile.
The ParsedFile contains symbols, edges, and imports in a language-agnostic
shape that the graph builder writes into SQLite.
"""
from __future__ import annotations

import abc
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class Symbol:
    name: str
    kind: str  # class|function|method|property|variable|interface|enum|route|...
    line_start: int
    line_end: int
    column_start: int = 0
    column_end: int = 0
    qualified_name: Optional[str] = None
    docstring: Optional[str] = None
    modifiers: List[str] = field(default_factory=list)
    # Structured extras for symbol kinds that need more than
    # name/kind/modifiers -- currently routes (kind='route'), which carry
    # {"http_method", "path", "framework", "handler", "provenance"}. None for
    # every other symbol kind; stored as JSON in the additive
    # symbols.metadata column (see src/graph/schema.py).
    metadata: Optional[Dict[str, Any]] = None
    # Embedding context (additive TEXT columns on `symbols`). All default to
    # None: parsers that don't populate them simply leave the corresponding
    # chunk section empty. `parameters`/`return_type` feed the
    # "Parameters:"/"Return Type:" sections of variant B/C; `parent_scope`,
    # `imports_summary`, and `body` feed the variant-C "Enclosing Scope:" /
    # "Imports:" / "Body:" sections. The builder derives parent_scope and
    # imports_summary at build time when the parser leaves them None, so
    # parsers only need to set the ones they actually know.
    parameters: Optional[str] = None
    return_type: Optional[str] = None
    parent_scope: Optional[str] = None
    imports_summary: Optional[str] = None
    body: Optional[str] = None


@dataclass
class Edge:
    source_name: str  # name of the enclosing symbol that owns this edge
    kind: str  # calls|imports|implements|extends|uses_type|references
    target_name: str  # unresolved name (resolved to symbol_id later by builder)
    line: int
    column: int = 0
    # Bare type name of the call receiver, if the parser could infer one
    # (local var -> type, `this`, constructor call, static/companion call).
    # None means "unknown" -- the resolver's type-aware tier simply abstains
    # and falls through to same-repo/global.
    receiver_type: Optional[str] = None


@dataclass
class Import:
    imported_path: str  # e.g. "retrofit2.Retrofit" or "java.util.List"
    line: int


@dataclass
class ParsedFile:
    path: str
    language: str
    hash: str
    line_count: int
    symbols: List[Symbol] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)
    imports: List[Import] = field(default_factory=list)


class BaseParser(abc.ABC):
    """Abstract parser. Subclasses implement parse() for one language."""

    language: str = ""

    @abc.abstractmethod
    def parse(self, path: str) -> ParsedFile:
        """Parse a source file into symbols, edges, and imports."""
        raise NotImplementedError

    @staticmethod
    def file_hash(path: str) -> str:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    @staticmethod
    def count_lines(path: str) -> int:
        try:
            return sum(1 for _ in Path(path).open(encoding="utf-8", errors="replace"))
        except OSError:
            return 0


class TreeSitterParserBase:
    """Mixin with shared helpers for tree-sitter-based parsers.

    Provides:
    - _node_text: extract text from a tree-sitter node
    - _qualified_name: build qualified names using the scope stack
    - _child_of_type / _find_name: common AST-shape helpers. ``_extract_callee``
      stays per-parser because its shape is genuinely language-specific.
    - Scope stack management (_scope, _callable_scope)
    """

    def __init__(self):
        # Stack of enclosing type names; empty = top-level
        self._scope: List[str] = []
        # Stack of enclosing callable names (functions/methods)
        self._callable_scope: List[str] = []

    def _node_text(self, node, source: bytes) -> str:
        """Extract text from a tree-sitter node."""
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _qualified_name(self, name: str) -> str:
        """Build a qualified name using the current scope stack.

        Most languages use dot-qualified names (e.g., Outer.Inner.name).
        Some parsers override this for file-stem prefix (TypeScript/Dart/ObjC).
        """
        if self._scope:
            return ".".join(self._scope + [name])
        return name

    def _child_of_type(self, node, types):
        """Return the first direct child of ``node`` whose type is in ``types``.

        Single-level scan only (not recursive).
        """
        for c in node.children:
            if c.type in types:
                return c
        return None

    def _find_name(self, node, source: bytes, types=("identifier",)):
        """Return the text of the first ``identifier`` child of ``node``.

        ``types`` lets callers widen the accepted name-node kinds (e.g.
        TypeScript also accepts ``type_identifier``). Defaults to the plain
        ``identifier`` that most languages use.
        """
        for child in node.children:
            if child.type in types:
                return self._node_text(child, source).strip()
        return None

    def _current_edge_owner(self) -> str:
        """Get the current edge owner (callable scope first, then type scope)."""
        if self._callable_scope:
            return self._callable_scope[-1]
        if self._scope:
            return self._scope[-1]
        return ""

    def _infer_receiver_type(self, receiver_text: Optional[str]) -> Optional[str]:
        """Best-effort receiver type for the resolver.

        The receiver is often a local variable or a package qualifier; we only
        return it when it looks like a type (Capitalized), since the resolver's
        type-aware tier matches on receiver type. Package qualifiers like
        ``fmt`` are lowercase and won't match a class anyway.
        """
        if not receiver_text:
            return None
        # Heuristic: a capitalized leading char suggests a type, not a package.
        if receiver_text[0].isupper():
            return receiver_text
        return None

    # Max body chars captured per symbol. Large enough to hold a typical
    # method/function implementation; beyond this the embedding chunk would be
    # truncated by chunk_for_symbol anyway, and very long bodies tend to dilute
    # the distinctive signature/docstring signal rather than add meaning.
    BODY_MAX_CHARS = 1500

    def _extract_body(self, node, source: bytes, block_types=("block", "body_block")) -> Optional[str]:
        """Extract the implementation body of a definition node.

        Returns the joined text of the body block's statements (the actual
        implementation), excluding the signature line and excluding the
        docstring statement (which is captured separately on Symbol.docstring
        and would otherwise be duplicated in the embedding chunk).

        `node` is a tree-sitter definition node (function_definition,
        class_definition, method_definition, ...). The body is the child whose
        type is in `block_types`. The first statement is dropped when it is the
        docstring. Returns None if there is no body block (e.g. an abstract
        method, a forward declaration, or a one-liner with no implementation).

        Truncated to BODY_MAX_CHARS to keep embedding chunks bounded; very long
        bodies add noise rather than signal and would be truncated downstream
        regardless.
        """
        block = None
        for child in node.children:
            if child.type in block_types:
                block = child
                break
        if block is None:
            return None
        # Collect implementation statements, skipping the docstring (a string
        # literal as the first expression statement) so it isn't double-counted
        # in the embedding.
        stmts = []
        skipped_docstring = False
        for stmt in block.children:
            if not skipped_docstring and stmt.type == "expression_statement":
                # Is this first expression a bare string (the docstring)?
                if any(c.type == "string" for c in stmt.children):
                    skipped_docstring = True
                    continue
            skipped_docstring = True  # only the very first stmt can be the docstring
            text = self._node_text(stmt, source).strip()
            if text:
                stmts.append(text)
        if not stmts:
            return None
        body = "\n".join(stmts)
        if len(body) > self.BODY_MAX_CHARS:
            body = body[: self.BODY_MAX_CHARS]
        return body
