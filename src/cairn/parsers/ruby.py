"""Tree-sitter Ruby parser.

Extracts modules, classes, methods, singleton methods, inheritance edges,
call edges, and imports (``require`` / ``require_relative`` / ``load``) into
the shared ParsedFile model.

Node-type reference (tree-sitter-ruby):

- ``module`` -> Symbol(class). Ruby modules serve as namespaces (and mixins);
  both map to ``class`` since cairn has no separate "module" symbol kind.
- ``class`` -> Symbol(class). The class name may be a plain ``constant`` or a
  ``scope_resolution`` (``class A::B``). The optional ``superclass`` child
  -> Edge(extends); the superclass name may be a ``constant`` or
  ``scope_resolution`` (``< ::Base``, ``< User::Base``) -- the trailing
  constant is recorded as the target so the resolver's bare-name index can
  match it.
- ``method`` -> Symbol(method). The name is an ``identifier`` child (operators
  like ``def +`` are captured too -- see ``_method_name``).
- ``singleton_method`` (``def obj.foo`` / ``def self.foo``) -> Symbol(method).
- ``call`` -> Edge(calls). Every ``call`` node is a real call in tree-sitter-
  ruby: zero-argument calls (``X.new``, ``user.name``), parenless calls, and
  safe-navigation calls (``obj&.name``) are all ``call`` nodes. Calls with a
  block (``each do ... end``, ``map { ... }``) are also calls. The proc-call
  shorthand ``p.(1)`` has no method ``identifier`` and is skipped. Local-
  variable reads are plain ``identifier`` nodes, not ``call`` nodes.
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
        """Parse a class/module, push scope, and walk its body.

        The superclass subtree is skipped during the body walk: it is consumed
        here for the ``extends`` edge, and walking it would otherwise emit
        spurious ``calls`` edges when the superclass expression is itself a
        call (e.g. ``class C < Factory.build``).
        """
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
            # ``source_name`` must be the bare name (matching the symbol's
            # ``name`` field) so the builder's same-file name lookup resolves
            # the edge's source_id. Using _qualified_name here would break for
            # nested classes (builder.py:824-832 keys on bare name).
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
        # method: 'def' <name> method_parameters? body_statement 'end'
        # singleton_method: 'def' <receiver> '.' <name> method_parameters? ...
        #   (receiver may be 'self' or an identifier/constant).
        # The name is the LAST identifier/operator before method_parameters.
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
        )

    def _method_name(self, node: Node, source: bytes) -> Optional[str]:
        """Method name: the identifier/operator immediately before params/body.

        For ``def foo`` -> "foo". For ``def obj.helper`` -> "helper" (skipping
        the receiver ``obj``). For ``def self.bar`` -> "bar". For ``def +`` ->
        "+" (operator). For ``def []`` -> "[]" (element reference).
        """
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
        """A ``call`` node -> Edge(calls).

        Every ``call`` in tree-sitter-ruby is a real call: zero-arg
        (``X.new``), parenless (``puts "x"``), safe-navigation (``a&.b``), and
        block-bearing (``each do ... end``). The proc-call shorthand ``p.(1)``
        has no method identifier and is skipped. ``obj.method`` records the
        trailing identifier as the callee and the leading receiver for the
        resolver's type-aware tier.
        """
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
        """Split a ``call`` node into (callee_name, receiver_text).

        Shapes handled:
          - bare call:        ``foo(args)`` or ``foo`` -> ("foo", None)
          - method call:      ``obj.method(args)`` -> ("method", "obj")
          - safe navigation:  ``obj&.method`` -> ("method", "obj")
          - block call:       ``items.each do ... end`` -> ("each", "items")
          - receiver types:   identifier, constant (``X.new``), self
          - proc shorthand:   ``p.(1)`` -> (None, None) [no method identifier;
            receiver is a bare identifier/constant with ``.`` immediately
            followed by ``argument_list``]
        """
        # Receiver candidates: identifiers (local vars), constants (types/modules),
        # and the implicit self. The method name is always an identifier.
        ids = [c for c in node.children if c.type == "identifier"]
        constants = [c for c in node.children if c.type == "constant"]
        has_dot = any(c.type in (".", "&.") for c in node.children)
        # Proc shorthand: ``p.(1)`` -- identifier + '.' + argument_list, no
        # second identifier to act as the method name. Detect by checking that
        # an argument_list is a direct child immediately after the dot.
        has_arg = any(c.type == "argument_list" for c in node.children)
        if has_dot:
            # ``X.new`` -> ids=["new"], constants=["X"]; receiver is the constant.
            # ``obj.method`` -> ids=["obj","method"]; callee is last id.
            # ``p.(1)`` -> ids=["p"], constants=[], has_arg True, no second id.
            if len(ids) >= 2:
                callee = self._node_text(ids[-1], source).strip()
                receiver = self._node_text(ids[0], source).strip()
                return callee, receiver
            if len(ids) == 1 and constants:
                # Constant receiver, identifier method: ``X.new``.
                callee = self._node_text(ids[0], source).strip()
                receiver = self._node_text(constants[0], source).strip()
                return callee, receiver
            if len(ids) == 1 and not constants and has_arg:
                # ``p.(1)`` proc shorthand: single identifier receiver, no
                # method name. Skip.
                return None, None
            # len(ids) == 1 and not constants and not has_arg: a zero-arg
            # method call on an implicit receiver (rare). Treat the identifier
            # as the callee.
            if len(ids) == 1:
                return self._node_text(ids[0], source).strip(), None
        if ids:
            # Bare call: leading identifier is the callee.
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
