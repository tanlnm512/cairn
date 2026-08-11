"""Tree-sitter C / C++ parser.

A single shared traversal (``_CFamilyParser``) drives both grammars, since
tree-sitter-cpp is a superset of tree-sitter-c at the node-type level. Two
thin subclasses select the grammar per file. This mirrors the TypeScript /
JavaScript pattern.

Node-type reference (tree-sitter-c 0.23 + tree-sitter-cpp 0.23):

- ``namespace_definition`` (C++ only) -> scope (its ``declaration_list`` body
  is walked; the namespace is not itself a Symbol).
- ``class_specifier`` (C++) / ``struct_specifier`` (C & C++) -> Symbol(class).
  A struct's ``type_identifier`` is the name; C++ structs can have methods.
  C++ ``base_class_clause`` -> Edge(extends).
- ``function_definition`` -> Symbol(function) at file/namespace scope,
  Symbol(method) when inside a class/struct body. The name lives under a
  ``function_declarator`` child (``identifier`` or ``field_identifier``).
- ``call_expression`` -> Edge(calls). The callee is an ``identifier`` (bare
  call), a ``field_expression`` (``obj->method()`` / ``obj.method()``), or a
  ``template_function`` (``max_val<int>()``).
- ``preproc_include`` (``#include``) -> Import (``<stdio.h>`` or ``"foo.h"``).
- ``type_definition`` (C ``typedef struct {...} Name``) -> the inner
  ``struct_specifier`` is captured as a class symbol.

C uses ``field_identifier`` and ``type_identifier`` for names; C++ uses
``identifier`` and ``type_identifier``. Both are accepted.
"""
from __future__ import annotations

import hashlib
from typing import List, Optional

from tree_sitter import Node

from ._registry import get_parser as _get_ts_parser
from .base import BaseParser, Edge, Import, ParsedFile, Symbol, TreeSitterParserBase

# Name node types across both grammars.
_NAME_TYPES = ("identifier", "field_identifier", "type_identifier")


class _CFamilyParser(BaseParser, TreeSitterParserBase):
    """Shared traversal for the C and C++ grammars."""

    language = ""

    def __init__(self):
        super().__init__()
        self._parser = self._select_parser()
        self._pending_edges: List[Edge] = []
        self._class_depth = 0  # > 0 when inside a class/struct body

    # Subclasses override this to pick the grammar capsule.
    def _select_parser(self):
        raise NotImplementedError

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
        self._class_depth = 0
        self._walk(tree.root_node, source, pf)
        pf.edges.extend(self._pending_edges)
        return pf

    def _walk(self, node: Node, source: bytes, pf: ParsedFile):
        for child in node.children:
            self._visit(child, source, pf)

    def _visit(self, node: Node, source: bytes, pf: ParsedFile):
        t = node.type

        if t == "preproc_include":
            imp = self._parse_include(node, source)
            if imp:
                pf.imports.append(imp)
            return

        if t == "namespace_definition":
            ns_name = self._struct_name(node, source)
            if ns_name:
                self._scope.append(ns_name)
            self._walk(node, source, pf)
            if ns_name:
                self._scope.pop()
            return

        if t in ("class_specifier", "struct_specifier"):
            sym = self._parse_type_decl(node, source)
            if sym:
                pf.symbols.append(sym)
                self._scope.append(sym.name)
                self._class_depth += 1
                self._walk(node, source, pf)
                self._class_depth -= 1
                self._scope.pop()
            else:
                # Anonymous struct/enum -- still walk the body.
                self._walk(node, source, pf)
            return

        if t == "function_definition":
            sym = self._parse_function(node, source)
            if sym:
                pf.symbols.append(sym)
                self._callable_scope.append(sym.name)
                self._walk(node, source, pf)
                self._callable_scope.pop()
            return

        if t == "call_expression":
            edge = self._parse_call(node, source)
            if edge:
                pf.edges.append(edge)
            self._walk(node, source, pf)
            return

        self._walk(node, source, pf)

    # -------------------------------------------------------- declaration parse

    def _parse_type_decl(self, node: Node, source: bytes) -> Optional[Symbol]:
        """class_specifier / struct_specifier -> Symbol(class)."""
        name = self._struct_name(node, source)
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
        # C++ base_class_clause: ': public Engine, private Cloneable'
        for child in node.children:
            if child.type == "base_class_clause":
                for bc in child.children:
                    if bc.type == "type_identifier":
                        target = self._node_text(bc, source).strip()
                        self._pending_edges.append(
                            Edge(name, "extends", target, node.start_point[0] + 1)
                        )
        return sym

    def _parse_function(self, node: Node, source: bytes) -> Optional[Symbol]:
        """function_definition -> Symbol(function | method).

        The name lives under a function_declarator child. A function is a
        method when it appears inside a class/struct body (i.e. _scope is
        non-empty and the enclosing scope is a class).
        """
        name = None
        for child in node.children:
            if child.type == "function_declarator":
                name = self._decl_name(child, source)
                break
        if not name:
            # Some declarators (e.g. C function pointers) don't nest under
            # function_declarator; fall back to a direct identifier/field_identifier.
            name = self._decl_name(node, source)
        if not name:
            return None
        kind = "method" if self._class_depth > 0 else "function"
        return Symbol(
            name=name,
            kind=kind,
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
        )

    # ------------------------------------------------------------ call parsing

    def _parse_call(self, node: Node, source: bytes) -> Optional[Edge]:
        """call_expression -> Edge(calls).

        callee shapes:
          - identifier           -> bare call:  ``foo()``
          - field_expression     -> member call: ``obj->method()`` / ``obj.method()``
          - template_function    -> generic call: ``max_val<int>()``
        """
        callee = node.children[0] if node.children else None
        if callee is None:
            return None
        target = self._extract_callee(callee, source)
        if not target:
            return None
        return Edge(
            source_name=self._current_edge_owner(),
            kind="calls",
            target_name=target,
            line=node.start_point[0] + 1,
        )

    def _extract_callee(self, node: Node, source: bytes) -> Optional[str]:
        if node.type in ("identifier", "field_identifier"):
            return self._node_text(node, source).strip()
        if node.type == "field_expression":
            # obj->method / obj.method -> take the trailing field_identifier.
            for child in reversed(node.children):
                if child.type in ("field_identifier", "identifier"):
                    return self._node_text(child, source).strip()
            return None
        if node.type == "template_function":
            # max_val<int> -> leading identifier "max_val".
            for child in node.children:
                if child.type == "identifier":
                    return self._node_text(child, source).strip()
            return None
        return None

    # ------------------------------------------------------------- import parse

    def _parse_include(self, node: Node, source: bytes) -> Optional[Import]:
        # preproc_include: '#include' + system_lib_string | string_literal
        for child in node.children:
            if child.type in ("system_lib_string", "string_literal"):
                return Import(
                    imported_path=self._node_text(child, source).strip(),
                    line=node.start_point[0] + 1,
                )
        return None

    # ---------------------------------------------------------------- helpers

    def _decl_name(self, node: Node, source: bytes) -> Optional[str]:
        """First identifier / field_identifier child text."""
        for child in node.children:
            if child.type in ("identifier", "field_identifier"):
                return self._node_text(child, source).strip()
        return None

    def _struct_name(self, node: Node, source: bytes) -> Optional[str]:
        """The type_identifier name of a class/struct/namespace."""
        for child in node.children:
            if child.type == "type_identifier":
                return self._node_text(child, source).strip()
            if child.type == "namespace_identifier":
                return self._node_text(child, source).strip()
        return None


class CParser(_CFamilyParser):
    """Handles .c files (tree-sitter-c grammar)."""

    language = "c"

    def _select_parser(self):
        return _get_ts_parser("c")


class CppParser(_CFamilyParser):
    """Handles .cpp / .cc / .cxx / .hpp files (tree-sitter-cpp grammar)."""

    language = "cpp"

    def _select_parser(self):
        return _get_ts_parser("cpp")
