"""Tree-sitter Python parser.

Extracts class/function definitions, calls, imports, and base classes (inheritance)
into the shared ParsedFile model.
"""
from __future__ import annotations

from typing import List, Optional

from tree_sitter import Node

from ._registry import get_parser as _get_ts_parser
from .base import BaseParser, Edge, Import, ParsedFile, Symbol, TreeSitterParserBase


class PythonParser(BaseParser, TreeSitterParserBase):
    language = "python"

    def __init__(self):
        super().__init__()
        self._parser = _get_ts_parser("python")
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

        if t in ("import_statement", "import_from_statement"):
            imp = self._parse_import(node, source)
            if imp:
                pf.imports.append(imp)
            return

        if t == "class_definition":
            sym = self._parse_class(node, source)
            if sym:
                pf.symbols.append(sym)
                self._scope.append(sym.name)
                self._walk(node, source, pf)
                self._scope.pop()
            return

        if t == "function_definition":
            sym = self._parse_function(node, source)
            if sym:
                pf.symbols.append(sym)
                self._scope.append(sym.name)
                self._walk(node, source, pf)
                self._scope.pop()
            return

        if t == "call":
            edge = self._parse_call(node, source)
            if edge:
                pf.edges.append(edge)
            self._walk(node, source, pf)
            return

        self._walk(node, source, pf)

    def _current_owner(self) -> str:
        """Python-specific: edges owned by _scope, not _callable_scope."""
        return self._scope[-1] if self._scope else ""

    def _parse_class(self, node: Node, source: bytes) -> Optional[Symbol]:
        # class_definition: 'class' name argument_list? ':' block
        name = None
        for child in node.children:
            if child.type == "identifier":
                name = self._node_text(child, source).strip()
                break
        if not name:
            return None
        # Inheritance: argument_list holds base classes.
        for child in node.children:
            if child.type == "argument_list":
                for arg in child.children:
                    if arg.type in ("identifier", "dotted_name"):
                        self._pending_edges.append(
                            Edge(
                                name,
                                "implements",
                                self._node_text(arg, source).strip(),
                                node.start_point[0] + 1,
                            )
                        )
        # decorators as modifiers
        mods = self._collect_decorators(node, source)
        doc = self._extract_docstring(node, source)
        return Symbol(
            name=name,
            kind="class",
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
            docstring=doc,
            modifiers=mods,
            body=self._extract_body(node, source),
        )

    def _parse_function(self, node: Node, source: bytes) -> Optional[Symbol]:
        name = None
        for child in node.children:
            if child.type == "identifier":
                name = self._node_text(child, source).strip()
                break
        if not name:
            return None
        # A def is a method only when its IMMEDIATE enclosing scope is a class.
        # In the tree-sitter Python grammar the function_definition's direct
        # parent is always the wrapping `block`; the meaningful parent is that
        # block's parent. So: method iff (block's parent) is a class_definition.
        # This correctly classifies nested functions (def-inside-def) as plain
        # functions / closures rather than methods, even when the outer def
        # lives inside a class.
        kind = "function"
        parent = node.parent
        if parent is not None and parent.type == "block":
            grandparent = parent.parent
            if grandparent is not None and grandparent.type == "class_definition":
                kind = "method"
        mods = self._collect_decorators(node, source)
        doc = self._extract_docstring(node, source)
        # async detection
        for child in node.children:
            if self._node_text(child, source).strip() == "async":
                mods.append("async")
                break
        return Symbol(
            name=name,
            kind=kind,
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
            docstring=doc,
            modifiers=mods,
            body=self._extract_body(node, source),
        )

    def _extract_docstring(self, node: Node, source: bytes) -> Optional[str]:
        block = None
        for child in node.children:
            if child.type == "block":
                block = child
                break
        if not block or not block.children:
            return None
        first_stmt = block.children[0]
        if first_stmt.type == "expression_statement":
            for sub in first_stmt.children:
                if sub.type == "string":
                    text = self._node_text(sub, source).strip()
                    for quote in ('"""', "'''", '"', "'"):
                        if text.startswith(quote) and text.endswith(quote) and len(text) >= len(quote) * 2:
                            text = text[len(quote):-len(quote)].strip()
                            break
                    return text if text else None
        return None

    def _collect_decorators(self, node: Node, source: bytes) -> List[str]:
        mods = []
        for child in node.children:
            if child.type == "decorator":
                mods.append(self._node_text(child, source).strip())
        return mods

    def _parse_import(self, node: Node, source: bytes) -> Optional[Import]:
        text = self._node_text(node, source).replace("\n", " ").strip()
        return Import(imported_path=text, line=node.start_point[0] + 1)

    def _parse_call(self, node: Node, source: bytes) -> Optional[Edge]:
        # call: function (arguments). The called function is the first child.
        if not node.children:
            return None
        callee = node.children[0]
        target = self._extract_callee(callee, source)
        if not target:
            return None
        return Edge(
            source_name=self._current_owner(),
            kind="calls",
            target_name=target,
            line=node.start_point[0] + 1,
        )

    def _extract_callee(self, node: Node, source: bytes) -> Optional[str]:
        if node.type in ("identifier", "dotted_name"):
            return self._node_text(node, source).strip()
        # attribute call: a.b.method -> take tail
        if node.type == "attribute":
            for child in reversed(node.children):
                if child.type == "identifier":
                    return self._node_text(child, source).strip()
        # Chained call: f()() -> the callable is the inner call's callee.
        # Recurse into the call node's "function" field to recover the real
        # target (e.g. f()() -> f, factory()() -> factory).
        if node.type == "call":
            inner = node.child_by_field_name("function")
            if inner is not None:
                return self._extract_callee(inner, source)
            return None
        # Unresolvable callable shapes (subscript like d["k"](),
        # parenthesized expressions, etc.): emit nothing rather than a garbage
        # string that would pollute the graph with bogus call edges.
        return None
