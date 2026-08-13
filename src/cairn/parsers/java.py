"""Tree-sitter Java parser.

Extracts class/interface/enum declarations, methods, fields, call expressions,
imports, and inheritance (extends/implements) into the shared ParsedFile model.
"""
from __future__ import annotations

from typing import List, Optional

from tree_sitter import Node

from ._registry import get_parser as _get_ts_parser
from .base import BaseParser, Edge, Import, ParsedFile, Symbol, TreeSitterParserBase

JAVA_MODIFIERS = {
    "public", "private", "protected", "static", "final", "abstract",
    "synchronized", "volatile", "transient", "native", "strictfp", "default",
}

TYPE_DECL_NODES = {"class_declaration", "interface_declaration", "enum_declaration"}


class JavaParser(BaseParser, TreeSitterParserBase):
    language = "java"

    def __init__(self):
        super().__init__()
        self._parser = _get_ts_parser("java")
        self._scope_kinds: List[str] = []
        self._pending_edges: List[Edge] = []

    def parse(self, path: str) -> ParsedFile:
        import hashlib

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
        self._scope_kinds = []
        self._callable_scope = []
        self._walk(tree.root_node, source, pf)
        pf.edges.extend(self._pending_edges)
        return pf

    def _walk(self, node: Node, source: bytes, pf: ParsedFile):
        for child in node.children:
            self._visit(child, source, pf)

    def _visit(self, node: Node, source: bytes, pf: ParsedFile):
        t = node.type

        if t == "import_declaration":
            imp = self._parse_import(node, source)
            if imp:
                pf.imports.append(imp)
            return

        if t in TYPE_DECL_NODES:
            sym = self._parse_type_decl(node, source)
            if sym:
                pf.symbols.append(sym)
                self._scope.append(sym.name)
                self._scope_kinds.append(t)
                self._walk(node, source, pf)
                self._scope.pop()
                self._scope_kinds.pop()
            return

        if t == "method_declaration":
            sym = self._parse_method(node, source)
            if sym:
                pf.symbols.append(sym)
                self._callable_scope.append(sym.name)
                self._walk(node, source, pf)
                self._callable_scope.pop()
            return

        if t == "field_declaration":
            for sym in self._parse_field(node, source):
                pf.symbols.append(sym)
            # Descend into initializers so calls in field initializers (e.g.
            # `private final Repo repo = createRepo();`) emit edges. Mirrors
            # method_declaration above; without this, field-initializer calls
            # were silently dropped.
            self._walk(node, source, pf)
            return

        if t == "method_invocation":
            edge = self._parse_call(node, source)
            if edge:
                pf.edges.append(edge)
            self._walk(node, source, pf)
            return

        if t == "object_creation_expression":
            # `new Foo()` -- a constructor call. Emit a calls edge to the
            # constructed type so get_callers/impact_analysis see it (C# does
            # the same for its object_creation_expression). Without this,
            # every `new Foo()` in Java produced no edge at all.
            edge = self._parse_new(node, source)
            if edge:
                pf.edges.append(edge)
            self._walk(node, source, pf)
            return

        self._walk(node, source, pf)

    # --- helpers -----------------------------------------------------------

    def _collect_modifiers(self, node: Node, source: bytes) -> List[str]:
        mods = []
        for child in node.children:
            if child.type == "modifiers":
                for m in child.children:
                    txt = self._node_text(m, source).strip()
                    if txt in JAVA_MODIFIERS:
                        mods.append(txt)
            elif self._node_text(child, source).strip() in JAVA_MODIFIERS:
                mods.append(self._node_text(child, source).strip())
        return mods

    def _parse_type_decl(self, node: Node, source: bytes) -> Optional[Symbol]:
        name = None
        for child in node.children:
            if child.type == "identifier":
                name = self._node_text(child, source).strip()
                break
        if not name:
            return None
        kind = {
            "class_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "enum",
        }.get(node.type, "class")
        mods = self._collect_modifiers(node, source)
        sym = Symbol(
            name=name,
            kind=kind,
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
            modifiers=mods,
        )
        self._parse_inheritance(node, source, name)
        return sym

    def _parse_method(self, node: Node, source: bytes) -> Optional[Symbol]:
        name = None
        for child in node.children:
            if child.type == "identifier":
                name = self._node_text(child, source).strip()
                break
        if not name:
            return None
        mods = self._collect_modifiers(node, source)
        return Symbol(
            name=name,
            kind="method",
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
            modifiers=mods,
        )

    def _parse_field(self, node: Node, source: bytes) -> List[Symbol]:
        # field_declaration: modifiers type variable_declarator (= value).
        # A single declaration may define several names (e.g. ``int a, b, c;``),
        # so emit one Symbol per variable_declarator.
        mods = self._collect_modifiers(node, source)
        syms: List[Symbol] = []
        for child in node.children:
            if child.type != "variable_declarator":
                continue
            for vc in child.children:
                if vc.type == "identifier":
                    name = self._node_text(vc, source).strip()
                    syms.append(
                        Symbol(
                            name=name,
                            kind="property",
                            qualified_name=self._qualified_name(name),
                            line_start=node.start_point[0] + 1,
                            line_end=node.end_point[0] + 1,
                            column_start=node.start_point[1],
                            column_end=node.end_point[1],
                            modifiers=mods,
                        )
                    )
        return syms

    def _parse_import(self, node: Node, source: bytes) -> Optional[Import]:
        for child in node.children:
            if child.type == "scoped_identifier":
                return Import(
                    imported_path=self._node_text(child, source).strip(),
                    line=node.start_point[0] + 1,
                )
        return None

    def _parse_inheritance(self, node: Node, source: bytes, child_name: str):
        """extends -> 'extends' edge; implements -> 'implements' edge."""
        for child in node.children:
            if child.type == "superclass":
                target = self._extract_type_name(child, source)
                if target:
                    self._pending_edges.append(
                        Edge(child_name, "extends", target, node.start_point[0] + 1)
                    )
            elif child.type == "super_interfaces":
                for tl in self._iter_typelist(child):
                    target = self._extract_type_name(tl, source)
                    if target:
                        self._pending_edges.append(
                            Edge(
                                child_name,
                                "implements",
                                target,
                                node.start_point[0] + 1,
                            )
                        )

    def _extract_type_name(self, node: Node, source: bytes) -> Optional[str]:
        """Return the base type name of a type node.

        Handles plain (``type_identifier``/``scoped_type_identifier``) and
        parameterised (``generic_type``) forms. For ``List<Foo>`` returns
        ``List``; for ``java.util.List<Foo>`` returns ``java.util.List``; for
        a bare ``Bar`` returns ``Bar`` unchanged.
        """
        if node.type == "generic_type":
            # generic_type wraps its head (type_identifier or
            # scoped_type_identifier) followed by type_arguments.
            for child in node.children:
                if child.type in ("type_identifier", "scoped_type_identifier"):
                    return self._node_text(child, source).strip()
            return None
        if node.type in ("type_identifier", "scoped_type_identifier"):
            return self._node_text(node, source).strip()
        # Fall back: a container node (e.g. ``superclass``) wrapping the type.
        # Recurse into the first type-bearing child so generic/scoped heads
        # (e.g. ``extends Base<Integer>``) resolve too.
        for child in node.children:
            if child.type in (
                "generic_type",
                "type_identifier",
                "scoped_type_identifier",
            ):
                return self._extract_type_name(child, source)
        return None

    def _iter_typelist(self, node: Node):
        for child in node.children:
            if child.type == "type_list":
                yield from child.children

    def _parse_call(self, node: Node, source: bytes) -> Optional[Edge]:
        # method_invocation: (object '.')? name '(' args ')'. The called name is
        # the LAST identifier before the argument_list -- the first identifier
        # (when present) is the receiver, not the method.
        owner = self._current_edge_owner()
        callee = None
        receiver_text = None
        for child in node.children:
            if child.type == "argument_list":
                break
            if child.type == "identifier":
                if callee is not None:
                    # A previous identifier was captured; it was the receiver.
                    receiver_text = callee
                callee = self._node_text(child, source).strip()
        if not callee:
            return None
        return Edge(
            source_name=owner,
            kind="calls",
            target_name=callee,
            line=node.start_point[0] + 1,
            receiver_type=self._infer_receiver_type(receiver_text),
        )

    def _parse_new(self, node: Node, source: bytes) -> Optional[Edge]:
        # object_creation_expression: 'new' <type> argument_list. The
        # constructed type is the type node after 'new' (type_identifier for a
        # simple name; class_type/scoped_type_identifier when qualified). Use
        # the trailing identifier so the resolver can match it to the class
        # symbol, mirroring the method-name-only call convention.
        owner = self._current_edge_owner()
        for child in node.children:
            if child.type == "argument_list":
                break
            if child.type in ("type_identifier", "class_type", "scoped_type_identifier"):
                txt = self._node_text(child, source).strip()
                callee = txt.split(".")[-1]
                return Edge(
                    source_name=owner,
                    kind="calls",
                    target_name=callee,
                    line=node.start_point[0] + 1,
                    receiver_type=None,
                )
        return None
