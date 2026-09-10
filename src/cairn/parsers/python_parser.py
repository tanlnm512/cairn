"""Tree-sitter Python parser.

Extracts class/function definitions, calls, imports, base classes (inheritance
as `extends` edges), decorators (`decorates`), and signature type annotations
(`references`) into the shared ParsedFile model.
"""
from __future__ import annotations

import re
from typing import List, Optional

from tree_sitter import Node

from ._registry import get_parser as _get_ts_parser
from .base import BaseParser, Edge, Import, ParsedFile, Symbol, TreeSitterParserBase

# Annotation tokens that never name a repo symbol; excluded from references
# edges so builtin typing shapes stay out of the graph.
_BUILTIN_TYPE_TOKENS = frozenset({
    "any", "bool", "bytes", "callable", "class", "cls", "dict", "final",
    "float", "frozenset", "int", "iterable", "list", "mapping", "none",
    "optional", "self", "sequence", "set", "str", "tuple", "type", "typing",
    "union",
})

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class PythonParser(BaseParser, TreeSitterParserBase):
    language = "python"

    def __init__(self):
        super().__init__()
        self._parser = _get_ts_parser("python")
        self._pending_edges: List[Edge] = []
        # Decorators sit on the `decorated_definition` wrapper, not on the
        # inner class/function_definition node. Set while the wrapper's
        # children are visited so the definition handlers can read them.
        self._active_decorators: List[Node] = []

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
        self._active_decorators = []
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

        if t == "import_statement" or t == "import_from_statement":
            imp = self._parse_import(node, source)
            if imp:
                pf.imports.append(imp)
            return

        if t == "decorated_definition":
            # (decorator ... definition): make the wrapper's decorators
            # visible to the inner definition's parse, then descend.
            self._active_decorators = [
                c for c in node.children if c.type == "decorator"
            ]
            try:
                self._walk(node, source, pf)
            finally:
                self._active_decorators = []
            return

        if t == "decorator":
            # The decorator->definition relationship is captured as a
            # `decorates` edge; walking in would duplicate it as a stray
            # module-level `calls` edge.
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
        # Inheritance: argument_list holds base classes (class inheritance —
        # `extends`, matching the other parsers' convention).
        for child in node.children:
            if child.type == "argument_list":
                for arg in child.children:
                    if arg.type in ("identifier", "dotted_name"):
                        self._pending_edges.append(
                            Edge(
                                name,
                                "extends",
                                self._node_text(arg, source).strip(),
                                node.start_point[0] + 1,
                            )
                        )
        self._emit_decorator_edges(node, source, name)
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
        # A def is a method only when its enclosing scope is a class. In the
        # tree-sitter Python grammar the function_definition's direct parent is
        # always the wrapping `block`; the meaningful parent is that block's
        # parent, so method iff (block's parent) is a class_definition.
        kind = "function"
        parent = node.parent
        if parent is not None and parent.type == "block":
            grandparent = parent.parent
            if grandparent is not None and grandparent.type == "class_definition":
                kind = "method"
        self._emit_decorator_edges(node, source, name)
        self._emit_type_references(node, source, name)
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

    def _decorator_nodes(self, node: Node) -> List[Node]:
        """Decorators for a definition: the enclosing decorated_definition's
        wrapper-level decorators, plus any directly on the node."""
        nodes = list(getattr(self, "_active_decorators", []))
        nodes.extend(c for c in node.children if c.type == "decorator")
        return nodes

    def _collect_decorators(self, node: Node, source: bytes) -> List[str]:
        mods = []
        for child in self._decorator_nodes(node):
            mods.append(self._node_text(child, source).strip())
        return mods

    def _emit_decorator_edges(self, node: Node, source: bytes, owner: str) -> None:
        """`decorates` edges: owner -> each decorator's callable name.

        `@app.route("/x")` -> target `route` (the attribute tail, matching
        how call edges name their targets); `@functools.lru_cache` ->
        `lru_cache`. Decorators that don't resolve to a symbol simply stay
        unresolved edges, like builtin callees.
        """
        for child in self._decorator_nodes(node):
            text = self._node_text(child, source).strip().lstrip("@")
            name = text.split("(", 1)[0].strip()
            if "." in name:
                name = name.rsplit(".", 1)[-1]
            if name and _IDENT_RE.fullmatch(name):
                self._pending_edges.append(
                    Edge(
                        owner,
                        "decorates",
                        name,
                        child.start_point[0] + 1,
                    )
                )

    def _emit_type_references(self, node: Node, source: bytes, owner: str) -> None:
        """`references` edges: owner -> classes named in signature annotations.

        Covers typed parameters and the return annotation of
        function/method definitions. Subscripted shapes (`Optional[Foo]`,
        `list[Bar]`) contribute their inner identifier tokens; builtin
        typing tokens are filtered. Dotted paths contribute their segments
        (the tail usually resolves; the qualifier usually does not).
        """
        type_nodes: List[Node] = []
        params = node.child_by_field_name("parameters")
        if params is not None:
            for child in params.children:
                ann = child.child_by_field_name("type")
                if ann is not None:
                    type_nodes.append(ann)
        ret = node.child_by_field_name("return_type")
        if ret is not None:
            type_nodes.append(ret)
        seen = set()
        for ann in type_nodes:
            text = self._node_text(ann, source)
            for tok in _IDENT_RE.findall(text):
                lowered = tok.lower()
                if (
                    lowered in _BUILTIN_TYPE_TOKENS
                    or tok == owner
                    or tok in seen
                ):
                    continue
                seen.add(tok)
                self._pending_edges.append(
                    Edge(
                        owner,
                        "references",
                        tok,
                        ann.start_point[0] + 1,
                    )
                )

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
