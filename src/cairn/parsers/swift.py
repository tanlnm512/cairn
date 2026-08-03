"""Tree-sitter Swift parser.

Extracts class/struct/protocol/enum declarations, functions, properties, call
expressions, imports, and inheritance into the shared ParsedFile model.

Note: Swift `enum`/`struct` may appear under class_declaration-style nodes with
a leading keyword; classification inspects the keyword (same approach as Kotlin).
"""
from __future__ import annotations

from typing import List, Optional

from tree_sitter import Node

from ._registry import get_parser as _get_ts_parser
from .base import BaseParser, Edge, Import, ParsedFile, Symbol, TreeSitterParserBase

SWIFT_MODIFIERS = {
    "public", "private", "fileprivate", "internal", "open", "final", "static",
    "override", "weak", "lazy", "mutating", "async", "throws", "rethrows",
    "class", "convenience", "required", "optional", "indirect",
}

TYPE_DECL_NODES = {
    "class_declaration",
    "struct_declaration",
    "protocol_declaration",
    "enum_declaration",
    "actor_declaration",
}


class SwiftParser(BaseParser, TreeSitterParserBase):
    language = "swift"

    def __init__(self):
        super().__init__()
        self._parser = _get_ts_parser("swift")
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

        if t == "function_declaration":
            sym = self._parse_function(node, source)
            if sym:
                pf.symbols.append(sym)
                self._scope.append(sym.name)
                self._walk(node, source, pf)
                self._scope.pop()
            return

        if t in ("property_declaration",):
            sym = self._parse_property(node, source)
            if sym:
                pf.symbols.append(sym)
            self._walk(node, source, pf)
            return

        if t == "call_expression":
            edge = self._parse_call(node, source)
            if edge:
                pf.edges.append(edge)
            self._walk(node, source, pf)
            return

        self._walk(node, source, pf)

    def _parse_type_identifier(self, node: Node, source: bytes) -> Optional[str]:
        for child in node.children:
            if child.type == "type_identifier":
                return self._node_text(child, source).strip()
        return None

    def _collect_modifiers(self, node: Node, source: bytes) -> List[str]:
        mods = []
        for child in node.children:
            if child.type == "modifiers":
                for m in child.children:
                    txt = self._node_text(m, source).strip()
                    if txt:
                        mods.append(txt)
            else:
                txt = self._node_text(child, source).strip()
                if txt in SWIFT_MODIFIERS:
                    mods.append(txt)
        return mods

    def _parse_type_decl(self, node: Node, source: bytes) -> Optional[Symbol]:
        name = self._parse_type_identifier(node, source)
        if not name:
            return None
        kind = self._classify_type(node, source)
        mods = self._collect_modifiers(node, source)
        self._parse_inheritance(node, source, name)
        return Symbol(
            name=name,
            kind=kind,
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
            modifiers=mods,
        )

    def _classify_type(self, node: Node, source: bytes) -> str:
        mapping = {
            "struct_declaration": "class",  # struct -> class kind
            "protocol_declaration": "interface",
            "enum_declaration": "enum",
            "actor_declaration": "class",
        }
        if node.type in mapping:
            return mapping[node.type]
        # class_declaration may actually be enum/struct in some grammar builds
        for child in node.children:
            txt = self._node_text(child, source).strip()
            if txt == "enum":
                return "enum"
            if txt == "struct":
                return "class"
        return "class"

    def _parse_function(self, node: Node, source: bytes) -> Optional[Symbol]:
        # function_declaration: 'func' name '(' params ')' ...
        for child in node.children:
            if child.type in ("identifier", "simple_identifier"):
                name = self._node_text(child, source).strip()
                mods = self._collect_modifiers(node, source)
                kind = "method" if self._scope else "function"
                return Symbol(
                    name=name,
                    kind=kind,
                    qualified_name=self._qualified_name(name),
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    column_start=node.start_point[1],
                    column_end=node.end_point[1],
                    modifiers=mods,
                )
            # init is a special function with no plain identifier
            if child.type == "init":
                mods = self._collect_modifiers(node, source)
                return Symbol(
                    name="init",
                    kind="method",
                    qualified_name=self._qualified_name("init"),
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    column_start=node.start_point[1],
                    column_end=node.end_point[1],
                    modifiers=mods,
                )
        return None

    def _parse_property(self, node: Node, source: bytes) -> Optional[Symbol]:
        for child in node.children:
            if child.type == "identifier":
                name = self._node_text(child, source).strip()
                mods = self._collect_modifiers(node, source)
                return Symbol(
                    name=name,
                    kind="property",
                    qualified_name=self._qualified_name(name),
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    column_start=node.start_point[1],
                    column_end=node.end_point[1],
                    modifiers=mods,
                )
        return None

    def _parse_import(self, node: Node, source: bytes) -> Optional[Import]:
        # import_declaration: 'import' identifier
        for child in node.children:
            if child.type == "identifier":
                return Import(
                    imported_path=self._node_text(child, source).strip(),
                    line=node.start_point[0] + 1,
                )
        # fallback: whole text
        return Import(
            imported_path=self._node_text(node, source).strip(),
            line=node.start_point[0] + 1,
        )

    def _parse_inheritance(self, node: Node, source: bytes, child_name: str):
        for child in node.children:
            if child.type == "type_inheritance_clause" or (
                child.type == "inheritance_specifier"
            ):
                self._collect_inh_targets(child, source, child_name)

    def _collect_inh_targets(
        self, node: Node, source: bytes, child_name: str
    ):
        if node is None:
            return
        if node.type == "type_identifier":
            self._pending_edges.append(
                Edge(
                    child_name,
                    "implements",
                    self._node_text(node, source).strip(),
                    node.start_point[0] + 1,
                )
            )
            return
        for child in node.children:
            self._collect_inh_targets(child, source, child_name)

    def _parse_call(self, node: Node, source: bytes) -> Optional[Edge]:
        # call_expression: the called function is the first child; for
        # `foo.bar()` it's a navigation_expression/member_expression.
        if not node.children:
            return None
        target = self._extract_callee(node.children[0], source)
        if not target:
            return None
        return Edge(
            source_name=self._current_edge_owner(),
            kind="calls",
            target_name=target,
            line=node.start_point[0] + 1,
        )

    def _extract_callee(self, node: Node, source: bytes) -> Optional[str]:
        if node.type in ("identifier", "simple_identifier"):
            return self._node_text(node, source).strip()
        if node.type in ("navigation_expression", "member_expression"):
            return self._tail_identifier(node, source)
        txt = self._node_text(node, source).strip()
        return txt.split(".")[-1] or None

    def _tail_identifier(self, node: Node, source: bytes) -> Optional[str]:
        last = None
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type in ("identifier", "simple_identifier"):
                last = self._node_text(n, source).strip()
            stack.extend(reversed(n.children))
        return last
