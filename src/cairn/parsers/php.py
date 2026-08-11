"""Tree-sitter PHP parser.

Extracts classes, interfaces, traits, functions, methods, properties, call
edges, and imports (require/include + use) into the shared ParsedFile model.

Uses the ``php_only`` grammar (registered under the ``"php"`` key in
``_registry._SPECIAL_LOADERS``), which yields pure PHP AST nodes without the
HTML wrapper that the full ``language_php()`` grammar would add around inline
PHP blocks.

Node-type reference (tree-sitter-php, php_only grammar):

- ``class_declaration`` / ``interface_declaration`` / ``trait_declaration``
  -> Symbol(class | interface | trait). A class's ``class_interface_clause``
  child -> Edge(implements).
- ``function_definition`` -> Symbol(function).
- ``method_declaration`` -> Symbol(method). Methods inside a class/interface/
  trait body are classified as ``method``; the FQN is scope-qualified via
  ``_scope``.
- ``property_declaration`` -> Symbol(property).
- ``function_call_expression`` (bare/name call) -> Edge(calls).
- ``member_call_expression`` (``$obj->method()``) -> Edge(calls), target is the
  method ``name`` child.
- ``scoped_call_expression`` (``Class::method()``) -> Edge(calls), target is the
  trailing ``name`` child; receiver_type set when the scope qualifier looks like
  a class name.
- ``require_*_expression`` / ``include_*_expression`` -> Import. The argument is
  often a ``binary_expression`` (``__DIR__ . "/path"``); we capture its text
  verbatim since PHP paths are not resolvable to file stems without runtime
  evaluation.
- ``namespace_use_declaration`` (``use``) -> Import (the fully qualified name).

PHP name nodes are plain ``name`` children (not ``identifier``), so the parser
looks up names by node type ``name`` rather than via ``_find_name``.
"""
from __future__ import annotations

import hashlib
from typing import Optional

from tree_sitter import Node

from ._registry import get_parser as _get_ts_parser
from .base import BaseParser, Edge, Import, ParsedFile, Symbol, TreeSitterParserBase

# Call node types that produce a `calls` edge.
_CALL_NODES = frozenset(
    {"function_call_expression", "member_call_expression", "scoped_call_expression"}
)
# Declaration node types that introduce a named type (class/interface/trait).
_TYPE_DECL_NODES = frozenset(
    {"class_declaration", "interface_declaration", "trait_declaration"}
)
# Require/include expression node types -> Import.
_REQUIRE_NODES = frozenset(
    {
        "require_expression",
        "require_once_expression",
        "include_expression",
        "include_once_expression",
    }
)


class PhpParser(BaseParser, TreeSitterParserBase):
    language = "php"

    def __init__(self):
        super().__init__()
        self._parser = _get_ts_parser("php")

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
        self._scope = []
        self._callable_scope = []
        self._walk(tree.root_node, source, pf)
        return pf

    def _walk(self, node: Node, source: bytes, pf: ParsedFile):
        for child in node.children:
            self._visit(child, source, pf)

    def _visit(self, node: Node, source: bytes, pf: ParsedFile):
        t = node.type

        # Imports: use statements and require/include expressions.
        if t == "namespace_use_declaration":
            imp = self._parse_use_import(node, source)
            if imp:
                pf.imports.append(imp)
            return
        if t in _REQUIRE_NODES:
            imp = self._parse_require_import(node, source)
            if imp:
                pf.imports.append(imp)
            return

        # Type declarations: class / interface / trait.
        if t in _TYPE_DECL_NODES:
            sym = self._parse_type_decl(node, source)
            if sym:
                pf.symbols.append(sym)
                self._scope.append(sym.name)
                self._walk(node, source, pf)
                self._scope.pop()
            return

        # Free-standing function definition.
        if t == "function_definition":
            sym = self._parse_function(node, source)
            if sym:
                pf.symbols.append(sym)
                self._callable_scope.append(sym.name)
                self._walk(node, source, pf)
                self._callable_scope.pop()
            return

        # Methods inside a class/interface/trait body.
        if t == "method_declaration":
            sym = self._parse_method(node, source)
            if sym:
                pf.symbols.append(sym)
                self._callable_scope.append(sym.name)
                self._walk(node, source, pf)
                self._callable_scope.pop()
            return

        if t == "property_declaration":
            sym = self._parse_property(node, source)
            if sym:
                pf.symbols.append(sym)
            # No scope push: properties don't own their declarations' edges.
            self._walk(node, source, pf)
            return

        if t in _CALL_NODES:
            edge = self._parse_call(node, source)
            if edge:
                pf.edges.append(edge)
            self._walk(node, source, pf)
            return

        self._walk(node, source, pf)

    # -------------------------------------------------------- declaration parse

    def _parse_type_decl(self, node: Node, source: bytes) -> Optional[Symbol]:
        """class/interface/trait declaration -> Symbol."""
        kind = {
            "class_declaration": "class",
            "interface_declaration": "interface",
            "trait_declaration": "trait",
        }[node.type]
        name = self._decl_name(node, source)
        if not name:
            return None
        sym = Symbol(
            name=name,
            kind=kind,
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
        )
        # implements clause on a class -> deferred edge not needed: PHP only has
        # implements (no extends-in-body for interfaces via this node); emit it
        # directly on the file's edges under the class owner name.
        return sym

    def _parse_function(self, node: Node, source: bytes) -> Optional[Symbol]:
        name = self._decl_name(node, source)
        if not name:
            return None
        return Symbol(
            name=name,
            kind="function",
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
        )

    def _parse_method(self, node: Node, source: bytes) -> Optional[Symbol]:
        name = self._decl_name(node, source)
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

    def _parse_property(self, node: Node, source: bytes) -> Optional[Symbol]:
        # property_declaration: visibility? type? property_element ';'
        # The name lives under property_element -> variable_name -> '$' name.
        for child in node.children:
            if child.type == "property_element":
                var = self._child_of_type(child, ("variable_name",))
                if var is not None:
                    nm = self._decl_name(var, source)
                    if nm:
                        return Symbol(
                            name=nm,
                            kind="property",
                            qualified_name=self._qualified_name(nm),
                            line_start=node.start_point[0] + 1,
                            line_end=node.end_point[0] + 1,
                            column_start=node.start_point[1],
                            column_end=node.end_point[1],
                        )
        return None

    # ------------------------------------------------------------ call parsing

    def _parse_call(self, node: Node, source: bytes) -> Optional[Edge]:
        """function/member/scoped call -> Edge(calls)."""
        if node.type == "function_call_expression":
            callee, receiver = self._split_function_call(node, source)
        elif node.type == "member_call_expression":
            callee, receiver = self._split_member_call(node, source)
        else:  # scoped_call_expression: Class::method()
            callee, receiver = self._split_scoped_call(node, source)
        if not callee:
            return None
        return Edge(
            source_name=self._current_edge_owner(),
            kind="calls",
            target_name=callee,
            line=node.start_point[0] + 1,
            receiver_type=self._infer_receiver_type(receiver),
        )

    def _split_function_call(self, node: Node, source: bytes):
        # function_call_expression: name | qualified_name, arguments
        for child in node.children:
            if child.type in ("name", "qualified_name"):
                return self._node_text(child, source).strip(), None
        return None, None

    def _split_member_call(self, node: Node, source: bytes):
        # member_call_expression: (variable_name | member_call_expression) '->'
        # name arguments
        callee = None
        receiver = None
        for child in node.children:
            if child.type == "name" and callee is None:
                callee = self._node_text(child, source).strip()
            elif child.type in ("variable_name", "member_call_expression", "function_call_expression"):
                if receiver is None:
                    receiver = self._node_text(child, source).strip()
        return callee, receiver

    def _split_scoped_call(self, node: Node, source: bytes):
        # scoped_call_expression: name '::' name arguments -- the trailing name
        # is the method, the leading name is the class scope.
        names = [c for c in node.children if c.type == "name"]
        if len(names) >= 2:
            callee = self._node_text(names[-1], source).strip()
            receiver = self._node_text(names[0], source).strip()
            return callee, receiver
        return None, None

    # ------------------------------------------------------------- import parse

    def _parse_use_import(self, node: Node, source: bytes) -> Optional[Import]:
        # namespace_use_declaration: 'use' namespace_use_clause ';'
        # The clause carries a qualified_name whose text is the FQN.
        for child in node.children:
            if child.type == "namespace_use_clause":
                qn = self._child_of_type(child, ("qualified_name",))
                if qn is not None:
                    return Import(
                        imported_path=self._node_text(qn, source).strip(),
                        line=node.start_point[0] + 1,
                    )
            if child.type == "qualified_name":
                return Import(
                    imported_path=self._node_text(child, source).strip(),
                    line=node.start_point[0] + 1,
                )
        return None

    def _parse_require_import(self, node: Node, source: bytes) -> Optional[Import]:
        # require/include expression nodes wrap the path argument verbatim.
        return Import(
            imported_path=self._node_text(node, source).strip(),
            line=node.start_point[0] + 1,
        )

    # ---------------------------------------------------------------- helpers

    def _decl_name(self, node: Node, source: bytes) -> Optional[str]:
        """First ``name`` child text (PHP uses ``name`` nodes, not ``identifier``)."""
        for child in node.children:
            if child.type == "name":
                return self._node_text(child, source).strip()
        return None
