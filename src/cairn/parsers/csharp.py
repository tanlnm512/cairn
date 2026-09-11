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
  (bare call) or ``member_access_expression`` (``obj.Method()``); a nameable
  receiver resolves to a type via the scope-ordered tracker (locals, fields,
  parameters, foreach variables) or the capitalized heuristic, else None.
- ``object_creation_expression`` (``new T()``) -> Edge(calls), target is the
  type name. Call arity is the ``argument_list`` argument count; definition
  arity is the ``parameter_list`` count, None when any parameter carries a
  default value, ``params``, or an extension ``this``.
- ``using_directive`` -> Import. ``using Foo = Bar.Baz;`` stores the target
  path with ``local_alias="Foo"``; plain and ``using static`` directives carry
  no alias.

C# name nodes are plain ``identifier`` children. ``qualified_name`` (for
namespace-qualified using directives) is captured verbatim.
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

# Call-shaped nodes that produce a `calls` edge.
_CALL_NODES = frozenset({"invocation_expression", "object_creation_expression"})


class CSharpParser(BaseParser, TreeSitterParserBase):
    language = "csharp"

    def __init__(self):
        super().__init__()
        self._parser = _get_ts_parser("csharp")
        self._pending_edges: List[Edge] = []
        self._types = ScopeTypeTracker()

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
        self._types.reset()
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
                self._types.push()
                self._walk(node, source, pf)
                self._types.pop()
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
                self._types.push()
                self._record_parameters(node, source)
                self._walk(node, source, pf)
                self._types.pop()
                self._callable_scope.pop()
            return

        if t == "property_declaration":
            sym = self._parse_property(node, source)
            if sym:
                pf.symbols.append(sym)
            self._walk(node, source, pf)
            return

        if t == "field_declaration":
            self._record_statement_declarations(node, source)
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

        if t == "block":
            self._types.push()
            self._walk(node, source, pf)
            self._types.pop()
            return

        if t == "local_declaration_statement":
            self._record_statement_declarations(node, source)
            self._walk(node, source, pf)
            return

        if t in ("using_statement", "for_statement"):
            # The declared variable is scoped to the statement, body included.
            self._types.push()
            self._record_statement_declarations(node, source)
            self._walk(node, source, pf)
            self._types.pop()
            return

        if t == "foreach_statement":
            self._types.push()
            self._record_foreach(node, source)
            self._walk(node, source, pf)
            self._types.pop()
            return

        if t == "assignment_expression":
            self._record_assignment(node, source)
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
            arity=self._parameter_arity(node, source),
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
                        call_arity=self._argument_arity(node),
                    )
                if child.type == "generic_name":
                    generic = self._extract_generic_name(child, source)
                    if generic:
                        return Edge(
                            source_name=self._current_edge_owner(),
                            kind="calls",
                            target_name=generic,
                            line=node.start_point[0] + 1,
                            call_arity=self._argument_arity(node),
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
            receiver_type=self._member_receiver_type(callee_node, source),
            call_arity=self._argument_arity(node),
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
        """using_directive -> Import.

        Alias directives are structural at the pinned grammar version (no
        ``name_equals`` node exists): an ``=`` token makes the leading
        ``identifier`` the local alias and the ``qualified_name`` -- or the
        trailing identifier -- the imported target. Plain and ``static``
        directives import their single name child verbatim.
        """
        line = node.start_point[0] + 1
        idents: List[Node] = []
        qualified: List[Node] = []
        is_alias = any(c.type == "=" for c in node.children)
        for child in node.children:
            if child.type == "identifier":
                idents.append(child)
            elif child.type == "qualified_name":
                qualified.append(child)
        if not is_alias:
            name_node = (qualified or idents)[:1]
            if not name_node:
                return None
            return Import(
                imported_path=self._node_text(name_node[0], source).strip(),
                line=line,
            )
        if not idents:
            return None
        if qualified:
            target = self._node_text(qualified[0], source).strip()
        elif len(idents) > 1:
            target = self._node_text(idents[1], source).strip()
        else:
            return None
        return Import(
            imported_path=target,
            line=line,
            local_alias=self._node_text(idents[0], source).strip(),
        )

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

    # ------------------------------------------------- receiver-type tracking

    def _record_statement_declarations(self, node: Node, source: bytes) -> None:
        for child in node.children:
            if child.type == "variable_declaration":
                self._record_variable_declaration(child, source)

    def _record_variable_declaration(self, var_decl: Node, source: bytes) -> None:
        children = var_decl.children
        if not children:
            return
        type_node = children[0]
        for child in children[1:]:
            if child.type != "variable_declarator":
                continue
            name = self._decl_name(child, source)
            if name:
                self._types.record(
                    name, self._declared_type(type_node, child, source)
                )

    def _record_parameters(self, node: Node, source: bytes) -> None:
        for child in node.children:
            if child.type != "parameter_list":
                continue
            for p in child.children:
                if p.type != "parameter":
                    continue
                ids = [c for c in p.children if c.type == "identifier"]
                if not ids:
                    continue
                type_node = next(
                    (
                        c
                        for c in p.children
                        if c.is_named and c.type != "modifier" and c is not ids[-1]
                    ),
                    None,
                )
                if type_node is not None:
                    self._types.record(
                        self._node_text(ids[-1], source).strip(),
                        self._type_name(type_node, source),
                    )

    def _record_foreach(self, node: Node, source: bytes) -> None:
        named = [c for c in node.children if c.is_named and c.type != "block"]
        if len(named) < 2 or named[1].type != "identifier":
            return
        if named[0].type == "implicit_type":
            return
        self._types.record(
            self._node_text(named[1], source).strip(),
            self._type_name(named[0], source),
        )

    def _record_assignment(self, node: Node, source: bytes) -> None:
        lhs = node.children[0] if node.children else None
        if lhs is None or lhs.type != "identifier":
            return
        value = self._initializer_node(node)
        if value is None:
            return
        name = self._node_text(lhs, source).strip()
        if value.type == "object_creation_expression":
            self._types.record(name, self._creation_type_name(value, source))
        elif value.type == "identifier":
            resolved = self._types.resolve(self._node_text(value, source).strip())
            if resolved is not None:
                self._types.record(name, resolved)

    def _initializer_node(self, node: Node) -> Optional[Node]:
        """First named child after a plain ``=`` token, if any."""
        seen_eq = False
        for child in node.children:
            if child.type == "=":
                seen_eq = True
            elif seen_eq and child.is_named:
                return child
        return None

    def _declared_type(
        self, type_node: Optional[Node], declarator: Node, source: bytes
    ) -> Optional[str]:
        if type_node is not None and type_node.type != "implicit_type":
            return self._type_name(type_node, source)
        # ``var`` -- the initializer decides; anything else is unknown.
        value = self._initializer_node(declarator)
        if value is None:
            return None
        if value.type == "object_creation_expression":
            return self._creation_type_name(value, source)
        if value.type == "identifier":
            return self._types.resolve(self._node_text(value, source).strip())
        return None

    def _creation_type_name(self, node: Node, source: bytes) -> Optional[str]:
        for child in node.children:
            if child.type in ("identifier", "generic_name", "qualified_name"):
                return self._type_name(child, source)
        return None

    def _type_name(self, node: Node, source: bytes) -> Optional[str]:
        """Bare type name for a type node (``List<T>``/``A.B``/``T?``/``T[]``)."""
        t = node.type
        if t in ("identifier", "predefined_type"):
            return self._node_text(node, source).strip()
        if t == "generic_name":
            return self._decl_name(node, source)
        if t == "qualified_name":
            last = None
            for child in node.children:
                if child.is_named:
                    last = child
            return self._type_name(last, source) if last is not None else None
        if t in ("array_type", "nullable_type", "pointer_type"):
            for child in node.children:
                if child.is_named:
                    return self._type_name(child, source)
        return None

    def _member_receiver_type(
        self, callee_node: Optional[Node], source: bytes
    ) -> Optional[str]:
        """Receiver type for ``recv.Member(...)`` calls, else None."""
        if callee_node is None or callee_node.type != "member_access_expression":
            return None
        expr = callee_node.child_by_field_name("expression")
        if expr is None or expr.type != "identifier":
            return None
        text = self._node_text(expr, source).strip()
        resolved = self._types.resolve(text)
        if resolved is not None:
            return resolved
        return self._infer_receiver_type(text)

    # ------------------------------------------------------------------ arity

    def _parameter_arity(self, node: Node, source: bytes) -> Optional[int]:
        """Parameter count, or None when a fixed count cannot match calls."""
        for child in node.children:
            if child.type != "parameter_list":
                continue
            params = [c for c in child.children if c.type == "parameter"]
            # An inlined ``params`` array parameter has no ``parameter``
            # wrapper, so any named child outside one makes counting unsafe.
            if any(c.is_named and c.type != "parameter" for c in child.children):
                return None
            for p in params:
                if any(
                    m.type == "="
                    or m.type == "params"
                    or (
                        m.type == "modifier"
                        and self._node_text(m, source).strip() == "this"
                    )
                    for m in p.children
                ):
                    return None
            return len(params)
        return None

    def _argument_arity(self, node: Node) -> Optional[int]:
        for child in node.children:
            if child.type == "argument_list":
                return sum(1 for c in child.children if c.type == "argument")
        return None
