"""Base parser interface and shared data model."""
from __future__ import annotations

import abc
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
    # Symbol-kind-specific JSON payload stored in symbols.metadata.
    metadata: Optional[Dict[str, Any]] = None
    # Embedding context (TEXT columns on `symbols`). All default to None;
    # parsers only set the ones they know. `parent_scope` and
    # `imports_summary` are derived by the builder when left None.
    parameters: Optional[str] = None
    return_type: Optional[str] = None
    parent_scope: Optional[str] = None
    imports_summary: Optional[str] = None
    body: Optional[str] = None
    # Parameter count of the definition when the parser could count it
    # unambiguously; None = unknown.
    arity: Optional[int] = None


@dataclass
class Edge:
    source_name: str  # name of the enclosing symbol that owns this edge
    # Structural traversal uses calls and extends/implements; other kinds inform display and resolution.
    kind: str
    target_name: str  # unresolved name (resolved to symbol_id later by builder)
    line: int
    column: int = 0
    # Bare receiver type; None makes the type-aware resolver abstain.
    receiver_type: Optional[str] = None
    # Count of call-site arguments when the parser could count them;
    # None = unknown.
    call_arity: Optional[int] = None


@dataclass
class Import:
    imported_path: str  # e.g. "retrofit2.Retrofit" or "java.util.List"
    line: int
    # Local binding name when it differs from the imported name
    # (`import m.x as y` -> "y"); None when identical or absent.
    local_alias: Optional[str] = None


@dataclass
class RationaleRecord:
    line: int
    kind: str  # note|why|hack
    text: str


@dataclass
class ParsedFile:
    path: str
    language: str
    hash: str
    line_count: int
    symbols: List[Symbol] = field(default_factory=list)
    edges: List[Edge] = field(default_factory=list)
    imports: List[Import] = field(default_factory=list)
    rationale: List[RationaleRecord] = field(default_factory=list)


class BaseParser(abc.ABC):
    """Abstract parser. Subclasses implement parse() for one language."""

    language: str = ""

    @abc.abstractmethod
    def parse(self, path: str) -> ParsedFile:
        """Parse a source file into symbols, edges, and imports."""
        raise NotImplementedError

    @staticmethod
    def count_lines(path: str) -> int:
        try:
            return sum(1 for _ in Path(path).open(encoding="utf-8", errors="replace"))
        except OSError:
            return 0


class TreeSitterParserBase:
    """Provide shared AST helpers and scope tracking for tree-sitter parsers."""

    language: str = ""

    def _visit(self, node, source: bytes, pf: "ParsedFile") -> None:
        raise NotImplementedError

    def __init__(self):
        # Stack of enclosing type names; empty = top-level
        self._scope: List[str] = []
        # Stack of enclosing callable names (functions/methods)
        self._callable_scope: List[str] = []

    def _node_text(self, node, source: bytes) -> str:
        """Extract text from a tree-sitter node."""
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _qualified_name(self, name: str) -> str:
        """Qualify name with the current dot-separated scope, if any."""
        if self._scope:
            return ".".join(self._scope + [name])
        return name

    def _child_of_type(self, node, types):
        """Return the first direct child whose type is in types."""
        for c in node.children:
            if c.type in types:
                return c
        return None

    def _find_name(self, node, source: bytes, types=("identifier",)):
        """Return the text of the first direct child name node."""
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
        """Return a receiver text only when its capitalized shape looks like a type."""
        if not receiver_text:
            return None
        # Heuristic: a capitalized leading char suggests a type, not a package.
        if receiver_text[0].isupper():
            return receiver_text
        return None

    def _walk(self, node, source: bytes, pf: "ParsedFile"):
        for child in node.children:
            if self._rationale_from_comment(child, source, pf):
                continue
            self._visit(child, source, pf)

    def _rationale_from_comment(self, node, source: bytes, pf: "ParsedFile") -> bool:
        """Consume a mapped comment node into pf.rationale; True when handled."""
        # Function-level import: _rationale imports RationaleRecord from here.
        from ._rationale import COMMENT_NODE_TYPES, extract_rationale

        comment_types = COMMENT_NODE_TYPES.get(self.language)
        if not comment_types or node.type not in comment_types:
            return False
        record = extract_rationale(node, source)
        if record is not None:
            pf.rationale.append(record)
        return True

    BODY_MAX_CHARS = 1500

    def _extract_body(self, node, source: bytes, block_types=("block", "body_block")) -> Optional[str]:
        """Return a bounded implementation body without its signature or docstring."""
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


class ScopeTypeTracker:
    """Scope-ordered variable to type tracker for receiver-type inference."""

    def __init__(self) -> None:
        # Innermost scope last. {name: None} marks a name whose recorded
        # types conflict: it resolves to None ("ambiguous"), which is
        # distinct from "never recorded".
        self._scopes: List[Dict[str, Optional[str]]] = [{}]

    def reset(self) -> None:
        """Drop every scope and recorded type; parsers are reused per file."""
        self._scopes = [{}]

    def push(self) -> None:
        """Enter a nested block scope."""
        self._scopes.append({})

    def pop(self) -> None:
        """Exit the innermost scope; the root scope is never popped."""
        if len(self._scopes) > 1:
            self._scopes.pop()

    def record(self, name: str, type_name: Optional[str]) -> None:
        """Record a scope type, making conflicting visible types ambiguous."""
        if not name or not type_name:
            return
        visible: Optional[str] = None
        found = False
        for scope in reversed(self._scopes):
            if name in scope:
                visible = scope[name]
                found = True
                break
        if not found:
            self._scopes[-1][name] = type_name
        elif visible is None or visible == type_name:
            return
        else:
            self._scopes[-1][name] = None

    def resolve(self, name: str) -> Optional[str]:
        """Innermost visible type for ``name``, else None (unknown/ambiguous)."""
        for scope in reversed(self._scopes):
            if name in scope:
                return scope[name]
        return None
