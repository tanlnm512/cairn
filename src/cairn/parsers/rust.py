"""Rust adapter backed by the generic tree-sitter tier."""

from __future__ import annotations

from typing import Optional

from tree_sitter import Node

from .generic_tree_sitter import GenericTreeSitterParser


class RustParser(GenericTreeSitterParser):
    language = "rust"

    definition_kinds = {
        "function_item": "function",
        "function_signature_item": "function",
        "struct_item": "class",
        "enum_item": "enum",
        "trait_item": "interface",
        "impl_item": "implementation",
        "mod_item": "module",
        "type_item": "class",
        "const_item": "constant",
        "static_item": "constant",
        "macro_definition": "macro",
    }
    implementation_kinds = frozenset({"impl_item"})
    scope_kinds = frozenset(
        {"struct_item", "enum_item", "trait_item", "impl_item", "mod_item"}
    )
    type_scope_kinds = frozenset(
        {"struct_item", "enum_item", "trait_item", "impl_item"}
    )
    callable_kinds = frozenset(
        {"function_item", "function_signature_item"}
    )
    name_node_types = ("identifier", "type_identifier", "field_identifier")

    def _symbol_kind(self, node: Node, kind: str) -> str:
        if node.type in {"function_item", "function_signature_item"}:
            return "method" if self._type_scopes else "function"
        return kind

    def _implementation_name(self, node: Node, source: bytes) -> Optional[str]:
        for child in reversed(node.children):
            if child.type == "declaration_list":
                continue
            name = self._simple_type_name(child)
            if name is not None:
                return name
        return None

    def _simple_type_name(self, node: Node) -> Optional[str]:
        if node.type in {"type_identifier", "generic_type", "scoped_type_identifier"}:
            return self._first_type_identifier(node)
        return None

    def _first_type_identifier(self, node: Node) -> Optional[str]:
        if node.type == "type_identifier":
            return node.text.decode("utf-8")
        for child in node.children:
            name = self._first_type_identifier(child)
            if name is not None:
                return name
        return None
