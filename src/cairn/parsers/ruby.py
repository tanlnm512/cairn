"""Tree-sitter Ruby parser.

Extracts modules, classes, methods, singleton methods, call edges, and imports
(``require`` / ``require_relative`` / ``load``) into the shared ParsedFile model.

Node-type reference (tree-sitter-ruby):

- ``module`` -> Symbol(class). Ruby modules serve as namespaces (and mixins);
  both map to ``class`` since cairn has no separate "module" symbol kind.
- ``class`` -> Symbol(class).
- ``method`` -> Symbol(method).
- ``singleton_method`` (``def self.foo``) -> Symbol(method).
- ``call`` -> Edge(calls). A ``call`` node is only a real call when it carries
  an ``argument_list`` child; bare ``identifier`` references (local var reads)
  show up as ``call`` nodes without one and are skipped. ``obj.method`` is a
  ``call`` whose first child is the receiver (``identifier`` or ``constant``)
  and whose trailing ``identifier`` is the method name.
- top-level ``call`` to ``require``/``require_relative``/``load`` -> Import.

Ruby has no canonical qualified-name scheme; we scope-qualify via ``_scope``
(the enclosing module/class stack), matching the convention used by the other
non-JS-family parsers.
"""
from __future__ import annotations

import hashlib
from typing import List, Optional

from tree_sitter import Node

from ._registry import get_parser as _get_ts_parser
from .base import BaseParser, Edge, Import, ParsedFile, Symbol, TreeSitterParserBase

# Calls to these methods at the top of a file are import-like.
_REQUIRE_METHODS = frozenset({"require", "require_relative", "load"})


class RubyParser(BaseParser, TreeSitterParserBase):
    language = "ruby"

    def __init__(self):
        super().__init__()
        self._parser = _get_ts_parser("ruby")
        self._pending_edges: List[Edge] = []

    # ------------------------------------------------------------------ parse

    def parse(self, path: str) -> ParsedFile:
        source = open(path, "rb").read()
        tree = self._parser.parse(source)
        pf = ParsedFile(
            path=path,
            language=self.language,
            hash=hashlib.sha256(source).hexdigest(),
            line_count=source.count(b"\n") + 1,
        )
        # Parsers are cached singletons reused across files, so reset all
        # per-file accumulators here.
        self._pending_edges = []
        self._scope = []
        self._callable_scope = []
        self._walk(tree.root_node, source, pf)
        pf.edges.extend(self._pending_edges)
        return pf

    def _walk(self, node: Node, source: bytes, pf: ParsedFile):
        for child in node.children:
            self._visit(child, source, pf)

    def _visit(self, node: Node, source: bytes, pf: ParsedFile):
        t = node.type

        if t in ("class", "module"):
            sym = self._parse_type_decl(node, source)
            if sym:
                pf.symbols.append(sym)
                self._scope.append(sym.name)
                self._walk(node, source, pf)
                self._scope.pop()
            return

        if t in ("method", "singleton_method"):
            sym = self._parse_method(node, source)
            if sym:
                pf.symbols.append(sym)
                self._callable_scope.append(sym.name)
                self._walk(node, source, pf)
                self._callable_scope.pop()
            return

        if t == "call":
            # require/require_relative/load -> Import (only at top level).
            imp = self._maybe_import(node, source)
            if imp is not None:
                pf.imports.append(imp)
                return
            edge = self._parse_call(node, source)
            if edge:
                pf.edges.append(edge)
            self._walk(node, source, pf)
            return

        self._walk(node, source, pf)

    # -------------------------------------------------------- declaration parse

    def _parse_type_decl(self, node: Node, source: bytes) -> Optional[Symbol]:
        # class/module: keyword constant [('<' superclass)] body_statement 'end'
        name = None
        superclass = None
        for child in node.children:
            if child.type == "constant" and name is None:
                name = self._node_text(child, source).strip()
            elif child.type == "superclass":
                sc = self._child_of_type(child, ("constant",))
                if sc is not None:
                    superclass = self._node_text(sc, source).strip()
        if not name:
            return None
        sym = Symbol(
            name=name,
            kind="class",
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
        )
        # Record inheritance as an edge owned by the class itself.
        if superclass:
            pf_edge_owner = self._qualified_name(name)
            self._pending_edges.append(
                Edge(pf_edge_owner, "extends", superclass, node.start_point[0] + 1)
            )
        return sym

    def _parse_method(self, node: Node, source: bytes) -> Optional[Symbol]:
        # method: 'def' identifier method_parameters? body_statement 'end'
        # singleton_method: 'def' ('self' | receiver) '.' identifier ...
        name = None
        for child in node.children:
            if child.type == "identifier" and name is None:
                name = self._node_text(child, source).strip()
        if not name:
            return None
        return Symbol(
            name=name,
            kind="method",
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
        )

    # ------------------------------------------------------------ call parsing

    def _parse_call(self, node: Node, source: bytes) -> Optional[Edge]:
        """A ``call`` node with an argument_list is a real call; else skip.

        ``obj.method(args)`` -- trailing identifier is the callee, the leading
        receiver (identifier/constant) feeds receiver_type. ``bare(args)`` --
        the leading identifier is the callee.
        """
        has_args = any(c.type == "argument_list" for c in node.children)
        if not has_args:
            return None
        callee, receiver = self._split_call(node, source)
        if not callee:
            return None
        return Edge(
            source_name=self._current_edge_owner(),
            kind="calls",
            target_name=callee,
            line=node.start_point[0] + 1,
            receiver_type=self._infer_receiver_type(receiver),
        )

    def _split_call(self, node: Node, source: bytes):
        # Gather the meaningful name children in order.
        ids = [c for c in node.children if c.type in ("identifier", "constant")]
        has_dot = any(c.type == "." for c in node.children)
        if has_dot and len(ids) >= 2:
            # receiver.method -> take the last identifier as the callee and the
            # first as the receiver.
            callee = self._node_text(ids[-1], source).strip()
            receiver = self._node_text(ids[0], source).strip()
            return callee, receiver
        if ids:
            # bare call -- leading identifier is the callee.
            return self._node_text(ids[0], source).strip(), None
        return None, None

    # ------------------------------------------------------------- import parse

    def _maybe_import(self, node: Node, source: bytes) -> Optional[Import]:
        """Map top-level require/require_relative/load calls to Import rows.

        Returns None when this call isn't an import (so the caller falls
        through to ``_parse_call``). We only treat it as an import when not
        inside any class/module/method scope; inside a body, the same method
        names are ordinary runtime calls.
        """
        if self._scope or self._callable_scope:
            return None
        first = next(
            (c for c in node.children if c.type == "identifier"), None
        )
        if first is None:
            return None
        method_name = self._node_text(first, source).strip()
        if method_name not in _REQUIRE_METHODS:
            return None
        path = self._require_argument(node, source)
        if not path:
            return None
        return Import(imported_path=path, line=node.start_point[0] + 1)

    def _require_argument(self, node: Node, source: bytes) -> Optional[str]:
        """Extract the string literal argument of a require/load call."""
        args = self._child_of_type(node, ("argument_list",))
        if args is None:
            return None
        for child in args.children:
            if child.type == "string":
                # string: '"' string_content '"'  -> strip quotes
                return self._string_literal(child, source)
            if child.type == "simple_symbol":
                # :name -- strip leading colon
                return self._node_text(child, source).strip().lstrip(":")
        return None

    def _string_literal(self, node: Node, source: bytes) -> Optional[str]:
        text = self._node_text(node, source).strip()
        if len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]:
            return text[1:-1]
        return text or None
