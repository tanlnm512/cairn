"""Generic definition and call extraction for grammar-only language tiers."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Mapping, Optional

from tree_sitter import Node

from ._registry import get_parser
from .base import BaseParser, Edge, ParsedFile, Symbol, TreeSitterParserBase


class GenericTierCallEdge(Edge):
    """Call edge whose target must remain name-based and unresolved."""

    generic_tier = True


class GenericTreeSitterParser(BaseParser, TreeSitterParserBase):
    """Traverse configured declaration nodes and call expressions."""

    definition_kinds: Mapping[str, str] = {}
    implementation_kinds: frozenset[str] = frozenset()
    scope_kinds: frozenset[str] = frozenset()
    type_scope_kinds: frozenset[str] = frozenset()
    callable_kinds: frozenset[str] = frozenset()
    name_node_types: tuple[str, ...] = ("identifier",)

    def __init__(self):
        super().__init__()
        self._parser = get_parser(self.language)
        self._type_scopes: list[str] = []

    def parse(self, path: str) -> ParsedFile:
        source = Path(path).read_bytes()
        tree = self._parser.parse(source)
        parsed = ParsedFile(
            path=path,
            language=self.language,
            hash=hashlib.sha256(source).hexdigest(),
            line_count=source.count(b"\n") + 1,
        )
        self._scope = []
        self._callable_scope = []
        self._type_scopes = []
        self._visit(tree.root_node, source, parsed)
        return parsed

    def _visit(self, node: Node, source: bytes, parsed: ParsedFile) -> None:
        kind = self.definition_kinds.get(node.type)
        if kind is not None:
            name = self._definition_name(node, source)
            if name:
                parsed.symbols.append(
                    Symbol(
                        name=name,
                        kind=self._symbol_kind(node, kind),
                        qualified_name=self._qualified_name(name),
                        line_start=node.start_point[0] + 1,
                        line_end=node.end_point[0] + 1,
                        column_start=node.start_point[1],
                        column_end=node.end_point[1],
                    )
                )
                self._visit_declaration_body(node, name, source, parsed)
                return

        if node.type == "call_expression":
            edge = self._parse_call(node, source)
            if edge is not None:
                parsed.edges.append(edge)

        self._visit_children(node, source, parsed)

    def _visit_declaration_body(
        self, node: Node, name: str, source: bytes, parsed: ParsedFile
    ) -> None:
        scope_name = self._scope_name(node, name, source)
        if scope_name is not None:
            self._scope.append(scope_name)
            tracks_type = node.type in self.type_scope_kinds
            if tracks_type:
                self._type_scopes.append(scope_name)

        if node.type in self.callable_kinds:
            self._callable_scope.append(name)

        self._visit_children(node, source, parsed)

        if node.type in self.callable_kinds:
            self._callable_scope.pop()
        if scope_name is not None:
            if node.type in self.type_scope_kinds:
                self._type_scopes.pop()
            self._scope.pop()

    def _visit_children(self, node: Node, source: bytes, parsed: ParsedFile) -> None:
        for child in node.children:
            self._visit(child, source, parsed)

    def _definition_name(self, node: Node, source: bytes) -> Optional[str]:
        if node.type in self.implementation_kinds:
            implemented = self._implementation_name(node, source)
            return f"impl {implemented}" if implemented else None
        return self._direct_name(node, source, self.name_node_types)

    def _direct_name(
        self, node: Node, source: bytes, node_types: tuple[str, ...]
    ) -> Optional[str]:
        for child in node.children:
            if child.type in node_types:
                return self._node_text(child, source).strip()
        return None

    def _scope_name(
        self, node: Node, name: str, source: bytes
    ) -> Optional[str]:
        if node.type not in self.scope_kinds:
            return None
        if node.type in self.implementation_kinds:
            return self._implementation_name(node, source)
        return name

    def _symbol_kind(self, node: Node, kind: str) -> str:
        return kind

    def _implementation_name(self, node: Node, source: bytes) -> Optional[str]:
        return None

    def _parse_call(self, node: Node, source: bytes) -> Optional[Edge]:
        function_node = next(
            (child for child in node.named_children if child.type != "arguments"),
            None,
        )
        if function_node is None:
            return None
        callee = self._callee_name(function_node, source)
        if callee is None:
            return None
        return GenericTierCallEdge(
            source_name=self._current_edge_owner(),
            kind="calls",
            target_name=callee,
            line=node.start_point[0] + 1,
            column=node.start_point[1],
            call_arity=self._argument_count(node),
        )

    def _callee_name(self, node: Node, source: bytes) -> Optional[str]:
        if node.type in self.name_node_types:
            return self._node_text(node, source).strip()
        if node.type == "generic_function":
            return self._direct_name(node, source, ("identifier",))
        if node.type == "field_expression":
            field = self._last_descendant_of_type(node, "field_identifier")
            return self._node_text(field, source).strip() if field else None
        if node.type == "scoped_identifier":
            identifier = self._last_descendant_of_type(node, "identifier")
            return self._node_text(identifier, source).strip() if identifier else None
        return None

    def _last_descendant_of_type(self, node: Node, node_type: str) -> Optional[Node]:
        found = None
        for child in node.children:
            descendant = self._last_descendant_of_type(child, node_type)
            if descendant is not None:
                found = descendant
        if node.type == node_type:
            found = node
        return found

    def _argument_count(self, node: Node) -> Optional[int]:
        arguments = self._child_of_type(node, ("arguments",))
        if arguments is None:
            return None
        return sum(1 for child in arguments.children if child.is_named)
