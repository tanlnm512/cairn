"""Tree-sitter Ruby parser."""
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

    def _visit(self, node: Node, source: bytes, pf: ParsedFile):
        t = node.type

        if t in ("class", "module"):
            self._visit_type_decl(node, source, pf)
            return

        if t in ("method", "singleton_method"):
            sym = self._parse_method(node, source)
            if sym:
                pf.symbols.append(sym)
                self._callable_scope.append(sym.name)
                self._walk(node, source, pf)
                self._callable_scope.pop()
            else:
                # Still walk so a body under an unparseable name isn't lost.
                self._walk(node, source, pf)
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

    def _visit_type_decl(self, node: Node, source: bytes, pf: ParsedFile):
        """Parse a class or module, emit its superclass edge, then walk its body."""
        name = self._type_name(node, source)
        superclass = self._superclass_name(node, source)
        if not name:
            # Name extraction failed (e.g. an unrecognized shape) -- still
            # walk the body so nested declarations aren't lost.
            self._walk_excluding_superclass(node, source, pf)
            return
        pf.symbols.append(
            Symbol(
                name=name,
                kind="class",
                qualified_name=self._qualified_name(name),
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                column_start=node.start_point[1],
                column_end=node.end_point[1],
            )
        )
        if superclass:
            # Extends source_name must stay bare to match its same-file symbol.
            self._pending_edges.append(
                Edge(name, "extends", superclass, node.start_point[0] + 1)
            )
        self._scope.append(name)
        self._walk_excluding_superclass(node, source, pf)
        self._scope.pop()

    def _walk_excluding_superclass(self, node: Node, source: bytes, pf: ParsedFile):
        """Walk a class/module body, skipping the consumed ``superclass`` child."""
        for child in node.children:
            if child.type == "superclass":
                continue
            if self._rationale_from_comment(child, source, pf):
                continue
            self._visit(child, source, pf)

    def _type_name(self, node: Node, source: bytes) -> Optional[str]:
        """Class/module name: ``constant`` or ``scope_resolution`` (``A::B``)."""
        for child in node.children:
            if child.type == "constant":
                return self._node_text(child, source).strip()
            if child.type == "scope_resolution":
                # A::B -> take the trailing constant.
                for sc in reversed(child.children):
                    if sc.type == "constant":
                        return self._node_text(sc, source).strip()
        return None

    def _superclass_name(self, node: Node, source: bytes) -> Optional[str]:
        """Trailing constant of the ``superclass`` child (``< Base``/``< A::B``)."""
        sc = self._child_of_type(node, ("superclass",))
        if sc is None:
            return None
        inner = self._child_of_type(sc, ("constant", "scope_resolution"))
        if inner is None:
            return None
        if inner.type == "constant":
            return self._node_text(inner, source).strip()
        # scope_resolution: take the trailing constant.
        for c in reversed(inner.children):
            if c.type == "constant":
                return self._node_text(c, source).strip()
        return None

    def _parse_method(self, node: Node, source: bytes) -> Optional[Symbol]:
        # The method name follows any singleton receiver and precedes parameters or body.
        name = self._method_name(node, source)
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
            arity=self._arity(self._child_of_type(node, ("method_parameters",))),
        )

    def _method_name(self, node: Node, source: bytes) -> Optional[str]:
        """Return the method or operator name after any singleton receiver."""
        # Take the last identifier before the first method_parameters / body.
        last_id = None
        for child in node.children:
            if child.type in ("method_parameters", "body_statement"):
                break
            if child.type in ("identifier", "operator"):
                last_id = self._node_text(child, source).strip()
        return last_id

    # ------------------------------------------------------------ call parsing

    def _parse_call(self, node: Node, source: bytes) -> Optional[Edge]:
        """Return a calls Edge for every tree-sitter-ruby call with a method name."""
        callee, receiver = self._split_call(node, source)
        if not callee:
            return None
        return Edge(
            source_name=self._current_edge_owner(),
            kind="calls",
            target_name=callee,
            line=node.start_point[0] + 1,
            receiver_type=self._infer_receiver_type(receiver),
            call_arity=self._arity(self._child_of_type(node, ("argument_list",))),
        )

    def _split_call(self, node: Node, source: bytes):
        """Return callee and receiver from the identifier after the last dot."""
        children = node.children
        dot_idx = None
        for i, c in enumerate(children):
            if c.type in (".", "&."):
                dot_idx = i  # keep the LAST dot (deepest link of a chain)
        if dot_idx is None:
            # Bare call: leading identifier is the callee.
            for c in children:
                if c.type == "identifier":
                    return self._node_text(c, source).strip(), None
            return None, None

        # Method identifier: the first identifier child AFTER the last dot.
        for c in children[dot_idx + 1:]:
            if c.type == "identifier":
                callee = self._node_text(c, source).strip()
                return callee, self._receiver_text(children[:dot_idx], source)

        # No identifier after the dot: ``p.(1)`` proc shorthand. The single
        # identifier (the receiver) sits before the dot and the argument_list
        # follows it directly; there is no method name to record.
        return None, None

    def _receiver_text(self, before_dot, source: bytes) -> Optional[str]:
        """Return a directly inferable identifier, constant, or self receiver."""
        for c in before_dot:
            if c.type == "constant":
                return self._node_text(c, source).strip()
            if c.type == "identifier":
                return self._node_text(c, source).strip()
            if c.type == "self":
                return "self"
        return None

    def _arity(self, list_node) -> int:
        """Return positional slots, excluding block parameters and sibling blocks."""
        if list_node is None:
            return 0
        return sum(
            1
            for c in list_node.named_children
            if c.type not in ("block_argument", "block_parameter")
        )

    # ------------------------------------------------------------- import parse

    def _maybe_import(self, node: Node, source: bytes) -> Optional[Import]:
        """Return an Import only for require or load calls at top-level scope."""
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
        """Extract the string-literal/symbol argument of a require/load call."""
        args = self._child_of_type(node, ("argument_list",))
        if args is None:
            return None
        for child in args.children:
            if child.type == "string":
                return self._string_literal(child, source)
            if child.type == "simple_symbol":
                return self._node_text(child, source).strip().lstrip(":")
        return None

    def _string_literal(self, node: Node, source: bytes) -> Optional[str]:
        text = self._node_text(node, source).strip()
        if len(text) >= 2 and text[0] in "\"'" and text[-1] == text[0]:
            return text[1:-1]
        return text or None
