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
  call), a ``field_expression`` (``obj->method()`` / ``obj.method()``), a
  ``qualified_identifier`` (``ns::func()``; C++ only), or a
  ``template_function`` (``max_val<int>()``). Edges carry ``call_arity``
  (argument_list named-child count) and ``receiver_type`` (inferred from the
  receiver expression via a scope-ordered var->type tracker, or the
  qualified_identifier ``scope`` when capitalized). Function symbols carry
  ``arity`` when the parameter_list pins it exactly (no varargs/defaults).
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
from .base import (
    BaseParser,
    Edge,
    Import,
    ParsedFile,
    ScopeTypeTracker,
    Symbol,
    TreeSitterParserBase,
)

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
        # Scope-ordered var->type map for receiver inference (params + locals).
        self._types = ScopeTypeTracker()

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
        self._types.reset()
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
                self._types.push()
                self._record_params(node, source)
                self._walk(node, source, pf)
                self._types.pop()
                self._callable_scope.pop()
            return

        if t == "compound_statement":
            self._types.push()
            self._walk(node, source, pf)
            self._types.pop()
            return

        if t == "call_expression":
            edge = self._parse_call(node, source)
            if edge:
                pf.edges.append(edge)
            self._walk(node, source, pf)
            return

        if t == "declaration":
            self._record_declaration(node, source)

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
            arity=self._definition_arity(node, source),
        )

    # ------------------------------------------------------------ call parsing

    def _parse_call(self, node: Node, source: bytes) -> Optional[Edge]:
        """call_expression -> Edge(calls).

        callee shapes (field-labeled per the pinned grammars):
          - identifier           -> bare call:  ``foo()``
          - field_expression     -> member call: ``obj->method()`` / ``obj.method()``
          - qualified_identifier -> scoped call: ``ns::func()`` (C++ only);
                                    its ``scope`` field is the receiver signal
          - template_function    -> generic call: ``max_val<int>()``
        """
        callee = node.child_by_field_name("function")
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
            receiver_type=self._call_receiver_type(callee, source),
            call_arity=self._call_arity(node),
        )

    def _call_arity(self, node: Node) -> Optional[int]:
        """Named-child count of the ``argument_list`` (parens/commas are anonymous)."""
        args = node.child_by_field_name("arguments")
        if args is None:
            return None
        return sum(1 for c in args.children if c.is_named)

    def _call_receiver_type(self, callee: Node, source: bytes) -> Optional[str]:
        if callee.type == "field_expression":
            return self._expr_receiver_type(
                callee.child_by_field_name("argument"), source
            )
        if callee.type == "qualified_identifier":
            scope = callee.child_by_field_name("scope")
            if scope is not None:
                # `Math::abs()` -> Math; lowercase namespaces miss the
                # capitalized-type heuristic and stay unknown.
                return self._infer_receiver_type(self._node_text(scope, source).strip())
        return None

    def _expr_receiver_type(self, expr: Optional[Node], source: bytes) -> Optional[str]:
        """Type of a receiver expression, or None when not inferable."""
        if expr is None:
            return None
        if expr.type == "this":
            return self._scope[-1] if self._class_depth > 0 else None
        if expr.type == "identifier":
            name = self._node_text(expr, source).strip()
            tracked = self._types.resolve(name)
            if tracked is not None:
                return tracked
            return self._infer_receiver_type(name)
        return None

    def _extract_callee(self, node: Node, source: bytes) -> Optional[str]:
        if node.type == "field_expression":
            # obj->method / obj.method -> the ``field`` member name.
            field = node.child_by_field_name("field")
            if field is not None:
                return self._node_text(field, source).strip()
            return None
        return self._callee_name(node, source)

    def _callee_name(self, node: Node, source: bytes) -> Optional[str]:
        if node.type in ("identifier", "field_identifier"):
            return self._node_text(node, source).strip()
        if node.type in ("template_function", "qualified_identifier"):
            # `max_val<int>` / the `name` under `ns::`, itself possibly a
            # template_function (`std::make_unique<Engine>`).
            name = node.child_by_field_name("name")
            if name is not None:
                return self._callee_name(name, source)
        return None

    # -------------------------------------------------- arity + type tracking

    def _definition_arity(self, node: Node, source: bytes) -> Optional[int]:
        """Parameter count of a function_definition; None when not exact."""
        declarator = self._child_of_type(node, ("function_declarator",))
        if declarator is None:
            return None
        params = declarator.child_by_field_name("parameters")
        if params is None:
            return None
        # Varargs: a named variadic_parameter in C, an anonymous `...` in C++.
        if any(self._node_text(c, source) == "..." for c in params.children):
            return None
        named = [c for c in params.children if c.is_named]
        if not named:
            # `()` means zero parameters in C++ but is unspecified in C.
            return 0 if self.language == "cpp" else None
        if any(c.type != "parameter_declaration" for c in named):
            # optional_parameter_declaration (C++ default argument) or any
            # other shape: the count a call site must match is not exact.
            return None
        if len(named) == 1:
            type_node = named[0].child_by_field_name("type")
            if (
                named[0].child_by_field_name("declarator") is None
                and type_node is not None
                and self._node_text(type_node, source).strip() == "void"
            ):
                return 0  # `(void)` is zero parameters
        return len(named)

    def _record_params(self, fn_node: Node, source: bytes) -> None:
        """Record each typed parameter into the tracker's function scope."""
        declarator = self._child_of_type(fn_node, ("function_declarator",))
        if declarator is None:
            return
        params = declarator.child_by_field_name("parameters")
        if params is None:
            return
        for child in params.children:
            if child.type != "parameter_declaration":
                continue
            type_name = self._type_name_of(child.child_by_field_name("type"), source)
            if type_name is None:
                continue
            name = self._declared_var_name(child.child_by_field_name("declarator"), source)
            if name:
                self._types.record(name, type_name)

    def _record_declaration(self, node: Node, source: bytes) -> None:
        """Record each typed local declarator (``Engine a, *b = x;``)."""
        type_node = node.child_by_field_name("type")
        type_name = self._type_name_of(type_node, source)
        if type_name is None:
            return
        for child in node.children:
            if child.is_named and child is not type_node:
                name = self._declared_var_name(child, source)
                if name:
                    self._types.record(name, type_name)

    def _type_name_of(self, type_node: Optional[Node], source: bytes) -> Optional[str]:
        """Bare class-like type name; primitives, ``auto``, and qualified or
        template-argumented types stay None (unmatchable or ambiguous)."""
        if type_node is None:
            return None
        if type_node.type == "type_identifier":
            return self._node_text(type_node, source).strip()
        if type_node.type in (
            "struct_specifier",
            "union_specifier",
            "enum_specifier",
            "class_specifier",
        ):
            name = type_node.child_by_field_name("name")
            if name is not None:
                return self._node_text(name, source).strip()
        return None

    def _declared_var_name(self, node: Optional[Node], source: bytes) -> Optional[str]:
        """Identifier bound by a declarator chain (init/pointer/reference/array)."""
        while node is not None:
            if node.type == "identifier":
                return self._node_text(node, source).strip()
            if node.type in ("function_declarator", "function_pointer_declarator"):
                return None  # a function name, not a variable
            inner = node.child_by_field_name("declarator")
            if inner is None:
                inner = self._child_of_type(node, ("identifier",))
            node = inner
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
