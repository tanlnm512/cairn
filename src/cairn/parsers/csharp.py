"""Tree-sitter C# parser.

Extracts classes, interfaces, structs, enums, methods, constructors,
properties, fields, call edges, inheritance edges, and imports (using
directives) into the shared ParsedFile model.

Node-type reference (tree-sitter-c-sharp):

- ``namespace_declaration`` -> scope (its ``declaration_list`` body is walked;
  the namespace is not itself a Symbol).
- ``class_declaration`` / ``interface_declaration`` / ``struct_declaration`` /
- ``record_declaration`` -> Symbol(class | interface | class | class). A
  class's ``base_list`` -> Edge(extends for the first name, implements for the
  rest).
- ``enum_declaration`` -> Symbol(enum); ``enum_member_declaration`` children
  -> Symbol(enum_case).
- ``method_declaration`` / ``constructor_declaration`` -> Symbol(method).
- ``property_declaration`` -> Symbol(property); ``field_declaration`` ->
  Symbol(property).
- ``invocation_expression`` -> Edge(calls). The callee is an ``identifier``
  (bare call) or ``member_access_expression`` (``obj.Method()``).
- ``object_creation_expression`` (``new T()``) -> Edge(calls), target is the
  type name.
- ``using_directive`` -> Import.

C# name nodes are plain ``identifier`` children. ``qualified_name`` (for
namespace-qualified using directives) is captured verbatim.
"""
from __future__ import annotations

import hashlib
from typing import List, Optional

from tree_sitter import Node

from ._registry import get_parser as _get_ts_parser
from .base import BaseParser, Edge, Import, ParsedFile, Symbol, TreeSitterParserBase

# Call-shaped nodes that produce a `calls` edge.
_CALL_NODES = frozenset({"invocation_expression", "object_creation_expression"})


class CSharpParser(BaseParser, TreeSitterParserBase):
    language = "csharp"

    def __init__(self):
        super().__init__()
        self._parser = _get_ts_parser("csharp")
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

        if t == "using_directive":
            imp = self._parse_using(node, source)
            if imp:
                pf.imports.append(imp)
            return

        if t == "namespace_declaration":
            # Namespace scopes its body but is not itself a Symbol. Push the
            # name onto _scope so declarations inside are qualified.
            ns_name = self._qualified_name_from_node(node, source)
            if ns_name:
                self._scope.append(ns_name)
            self._walk(node, source, pf)
            if ns_name:
                self._scope.pop()
            return

        if t in ("class_declaration", "interface_declaration",
                 "struct_declaration", "record_declaration"):
            sym = self._parse_type_decl(node, source)
            if sym:
                pf.symbols.append(sym)
                self._scope.append(sym.name)
                self._walk(node, source, pf)
                self._scope.pop()
            return

        if t == "enum_declaration":
            sym = self._parse_enum(node, source, pf)
            if sym:
                pf.symbols.append(sym)
                self._scope.append(sym.name)
                self._walk(node, source, pf)
                self._scope.pop()
            return

        if t in ("method_declaration", "constructor_declaration"):
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
            self._walk(node, source, pf)
            return

        if t == "field_declaration":
            for sym in self._parse_fields(node, source):
                pf.symbols.append(sym)
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
        kind = {
            "class_declaration": "class",
            "struct_declaration": "class",
            "record_declaration": "class",
            "interface_declaration": "interface",
        }.get(node.type, "class")
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
            modifiers=self._collect_modifiers(node, source),
        )
        # base_list: first name is the base class (extends); the rest are
        # interfaces (implements). C# single-inheritance makes this unambiguous.
        for child in node.children:
            if child.type == "base_list":
                names = [c for c in child.children if c.type == "identifier"]
                for i, name_node in enumerate(names):
                    edge_kind = "extends" if i == 0 and kind == "class" else "implements"
                    target = self._node_text(name_node, source).strip()
                    self._pending_edges.append(
                        Edge(name, edge_kind, target, node.start_point[0] + 1)
                    )
                break
        return sym

    def _parse_enum(self, node: Node, source: bytes, pf: ParsedFile) -> Optional[Symbol]:
        name = self._decl_name(node, source)
        if not name:
            return None
        # Capture enum members as enum_case symbols.
        for child in node.children:
            if child.type == "enum_member_declaration_list":
                for member in child.children:
                    if member.type == "enum_member_declaration":
                        mname = self._decl_name(member, source)
                        if mname:
                            pf.symbols.append(
                                Symbol(
                                    name=mname,
                                    kind="enum_case",
                                    qualified_name=self._qualified_name(mname),
                                    line_start=member.start_point[0] + 1,
                                    line_end=member.end_point[0] + 1,
                                    column_start=member.start_point[1],
                                    column_end=member.end_point[1],
                                )
                            )
        return Symbol(
            name=name,
            kind="enum",
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
            modifiers=self._collect_modifiers(node, source),
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
            modifiers=self._collect_modifiers(node, source),
        )

    def _parse_property(self, node: Node, source: bytes) -> Optional[Symbol]:
        name = self._decl_name(node, source)
        if not name:
            return None
        return Symbol(
            name=name,
            kind="property",
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
            modifiers=self._collect_modifiers(node, source),
        )

    def _parse_fields(self, node: Node, source: bytes) -> List[Symbol]:
        """field_declaration -> one Symbol per variable_declarator."""
        out: List[Symbol] = []
        for var_decl in node.children:
            if var_decl.type == "variable_declaration":
                for vd in var_decl.children:
                    if vd.type == "variable_declarator":
                        fname = self._decl_name(vd, source)
                        if fname:
                            out.append(
                                Symbol(
                                    name=fname,
                                    kind="property",
                                    qualified_name=self._qualified_name(fname),
                                    line_start=node.start_point[0] + 1,
                                    line_end=node.end_point[0] + 1,
                                    column_start=node.start_point[1],
                                    column_end=node.end_point[1],
                                    modifiers=self._collect_modifiers(node, source),
                                )
                            )
        return out

    # ------------------------------------------------------------ call parsing

    def _parse_call(self, node: Node, source: bytes) -> Optional[Edge]:
        """invocation_expression / object_creation_expression -> Edge(calls)."""
        if node.type == "object_creation_expression":
            # new T() -> target is the type name (identifier or generic_name).
            for child in node.children:
                if child.type == "identifier":
                    return Edge(
                        source_name=self._current_edge_owner(),
                        kind="calls",
                        target_name=self._node_text(child, source).strip(),
                        line=node.start_point[0] + 1,
                    )
                if child.type == "generic_name":
                    generic = self._extract_generic_name(child, source)
                    if generic:
                        return Edge(
                            source_name=self._current_edge_owner(),
                            kind="calls",
                            target_name=generic,
                            line=node.start_point[0] + 1,
                        )
            return None
        # invocation_expression: callee is the first child.
        callee_node = node.children[0] if node.children else None
        if callee_node is None:
            return None
        target = self._extract_callee(callee_node, source)
        if not target:
            return None
        return Edge(
            source_name=self._current_edge_owner(),
            kind="calls",
            target_name=target,
            line=node.start_point[0] + 1,
        )

    def _extract_callee(self, node: Node, source: bytes) -> Optional[str]:
        if node.type == "identifier":
            return self._node_text(node, source).strip()
        if node.type == "member_access_expression":
            # obj.Method -> take the trailing identifier (the method name).
            for child in reversed(node.children):
                if child.type == "identifier":
                    return self._node_text(child, source).strip()
        if node.type == "generic_name":
            return self._extract_generic_name(node, source)
        return None

    def _extract_generic_name(self, node: Node, source: bytes) -> Optional[str]:
        """generic_name (``List<T>``) -> the leading identifier (``List``)."""
        for child in node.children:
            if child.type == "identifier":
                return self._node_text(child, source).strip()
        return None

    # ------------------------------------------------------------- import parse

    def _parse_using(self, node: Node, source: bytes) -> Optional[Import]:
        for child in node.children:
            if child.type in ("qualified_name", "identifier"):
                return Import(
                    imported_path=self._node_text(child, source).strip(),
                    line=node.start_point[0] + 1,
                )
        return None

    # ---------------------------------------------------------------- helpers

    def _decl_name(self, node: Node, source: bytes) -> Optional[str]:
        for child in node.children:
            if child.type == "identifier":
                return self._node_text(child, source).strip()
        return None

    def _qualified_name_from_node(self, node: Node, source: bytes) -> Optional[str]:
        """Extract a namespace name from a namespace_declaration."""
        for child in node.children:
            if child.type == "qualified_name":
                return self._node_text(child, source).strip()
            if child.type == "identifier":
                return self._node_text(child, source).strip()
        return None

    def _collect_modifiers(self, node: Node, source: bytes) -> List[str]:
        mods: List[str] = []
        for child in node.children:
            if child.type == "modifier":
                for m in child.children:
                    txt = self._node_text(m, source).strip()
                    if txt and txt not in ("modifier",):
                        mods.append(txt)
        return mods
