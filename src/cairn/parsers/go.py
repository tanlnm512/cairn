"""Tree-sitter Go parser.

Extracts structs, interfaces, type aliases, functions, methods (with their
receiver type as ``parent_scope``), call expressions, and imports into the
shared ParsedFile model.

Go-specific shape notes (verified against the tree-sitter-go grammar):

- ``type_declaration`` wraps one or more ``type_spec`` nodes (Go allows
  ``type ( A = int; B struct{} )`` grouped declarations). Each ``type_spec``
  carries a ``type_identifier`` (the name) and one of ``struct_type`` /
  ``interface_type`` / a type alias body.

- ``function_declaration`` has no receiver: ``func identifier(params) block``.

- ``method_declaration`` has a receiver: ``func (recv RecvType) Name(params)
  block``. The receiver is the first ``parameter_list`` child and the method
  name is a ``field_identifier`` (not ``identifier``). The receiver type becomes
  the method's ``parent_scope`` so ``func (s *Server) Handle()`` is scoped under
  ``Server`` -- the one thing the Java parser doesn't need but Go does.

- ``call_expression`` targets are either a bare ``identifier`` (``Foo()``) or a
  ``selector_expression`` (``pkg.Foo()`` / ``recv.Method()``). The called name
  is the trailing ``field_identifier``/``identifier``; the receiver (if any) is
  captured for the resolver's type-aware tier.

- Imports: single (``import "fmt"``) or grouped (``import ( ... )``), each an
  ``import_spec`` whose path is an ``interpreted_string_literal``.
"""
from __future__ import annotations

import hashlib
from typing import List, Optional

from tree_sitter import Node

from ._registry import get_parser as _get_ts_parser
from .base import BaseParser, Edge, Import, ParsedFile, Symbol, TreeSitterParserBase

# Node types that introduce a named type.
_STRUCT_TYPE = "struct_type"
_INTERFACE_TYPE = "interface_type"
_TYPE_ALIAS = "type_alias"


class GoParser(BaseParser, TreeSitterParserBase):
    language = "go"

    def __init__(self):
        super().__init__()
        self._parser = _get_ts_parser("go")

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
        self._walk(tree.root_node, source, pf)
        return pf

    def _walk(self, node: Node, source: bytes, pf: ParsedFile):
        """Depth-first traversal, recursing into every child by default.

        Visitors for specific node types either consume the subtree (return after
        handling children themselves) or fall through to the default child walk.
        """
        for child in node.children:
            self._visit(child, source, pf)

    def _visit(self, node: Node, source: bytes, pf: ParsedFile):
        t = node.type

        if t == "import_declaration":
            for imp in self._parse_imports(node, source):
                pf.imports.append(imp)
            return

        if t == "type_declaration":
            # A type_declaration contains one or more type_spec nodes.
            for spec in self._children_of_type(node, "type_spec"):
                sym = self._parse_type_spec(spec, source)
                if sym:
                    pf.symbols.append(sym)
                    # Push scope and walk inside (e.g. struct fields, methods).
                    self._scope.append(sym.name)
                    self._walk(spec, source, pf)
                    self._scope.pop()
            return

        if t == "function_declaration":
            sym = self._parse_function(node, source, receiver_type=None)
            if sym:
                pf.symbols.append(sym)
                self._callable_scope.append(sym.name)
                self._walk(node, source, pf)
                self._callable_scope.pop()
            return

        if t == "method_declaration":
            sym = self._parse_method(node, source)
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
            # A call expression may itself contain nested calls (e.g. arguments);
            # keep walking so we don't miss them.
            self._walk(node, source, pf)
            return

        self._walk(node, source, pf)

    # ----------------------------------------------------------- type parsing

    def _parse_type_spec(self, node: Node, source: bytes) -> Optional[Symbol]:
        """Parse a ``type_spec`` into a Symbol (struct/interface/alias)."""
        name = None
        for child in node.children:
            if child.type == "type_identifier":
                name = self._node_text(child, source).strip()
                break
        if not name:
            return None

        # Determine the kind from the body node.
        kind = "class"  # default for aliases / other types
        if self._has_child(node, _STRUCT_TYPE):
            kind = "class"  # Go structs map to the "class" symbol kind
        elif self._has_child(node, _INTERFACE_TYPE):
            kind = "interface"
        elif self._has_child(node, _TYPE_ALIAS):
            kind = "class"

        return Symbol(
            name=name,
            kind=kind,
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
        )

    # ------------------------------------------------------- function parsing

    def _parse_function(
        self, node: Node, source: bytes, receiver_type: Optional[str]
    ) -> Optional[Symbol]:
        name = self._find_name(node, source, types=("identifier",))
        if not name:
            return None
        params, return_type = self._parse_signature(node, source)
        return Symbol(
            name=name,
            kind="method" if receiver_type else "function",
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
            parameters=params,
            return_type=return_type,
            parent_scope=receiver_type,
        )

    def _parse_method(self, node: Node, source: bytes) -> Optional[Symbol]:
        """A method_declaration: ``func (recv RecvType) Name(params) block``."""
        # The method name is a ``field_identifier`` child (NOT an identifier).
        name = None
        receiver_type = None
        param_lists = []
        for child in node.children:
            if child.type == "field_identifier" and name is None:
                name = self._node_text(child, source).strip()
            elif child.type == "parameter_list":
                param_lists.append(child)

        # The first parameter_list is the receiver; the second is the params.
        if param_lists:
            receiver_type = self._receiver_type_from_params(param_lists[0], source)

        if not name:
            return None
        # Params/return come from the SECOND parameter_list (the real signature).
        params, return_type = (None, None)
        if len(param_lists) >= 2:
            params, return_type = self._parse_params_and_return(param_lists[1], node, source)

        return Symbol(
            name=name,
            kind="method",
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
            parameters=params,
            return_type=return_type,
            parent_scope=receiver_type,
        )

    def _parse_signature(self, node: Node, source: bytes):
        """Return (parameters_str, return_type_str) from a function_declaration.

        The signature's parameter_list is the FIRST one (function_declaration has
        no receiver). The return type is the type node following it.
        """
        param_lists = [c for c in node.children if c.type == "parameter_list"]
        if not param_lists:
            return None, None
        return self._parse_params_and_return(param_lists[0], node, source)

    def _parse_params_and_return(
        self, params_node: Node, parent: Node, source: bytes
    ):
        """Extract a parameters summary and the return type from a func node."""
        params = self._summarize_params(params_node, source)
        # The return type, if any, is a type node after the params list.
        return_type = None
        seen_params = False
        for child in parent.children:
            if child is params_node:
                seen_params = True
                continue
            if not seen_params:
                continue
            # The block child is the body; everything else before it that looks
            # like a type is the return type.
            if child.type == "block":
                break
            if child.type in _TYPE_NODE_KINDS:
                return_type = self._node_text(child, source).strip()
                break
        return params, return_type

    def _summarize_params(self, params_node: Node, source: bytes) -> Optional[str]:
        """Render a parameter_list as ``name1 type1, name2 type2``."""
        parts = []
        for child in params_node.children:
            if child.type != "parameter_declaration":
                continue
            # parameter_declaration: identifier(s) + type
            names = []
            ptype = None
            for pc in child.children:
                if pc.type == "identifier":
                    names.append(self._node_text(pc, source).strip())
                elif pc.type in _TYPE_NODE_KINDS:
                    ptype = self._node_text(pc, source).strip()
            label = ", ".join(names) if names else ""
            if ptype:
                label = f"{label} {ptype}".strip() if label else ptype
            if label:
                parts.append(label)
        return ", ".join(parts) if parts else None

    def _receiver_type_from_params(self, recv_node: Node, source: bytes) -> Optional[str]:
        """Extract the receiver type from a method's first parameter_list.

        ``func (s *Server) ...`` -> ``Server``. Handles pointer_type and
        qualified_type (``*http.Server`` -> ``http.Server``).
        """
        for child in recv_node.children:
            if child.type != "parameter_declaration":
                continue
            for pc in child.children:
                if pc.type == "pointer_type":
                    # pointer_type wraps the base type.
                    for gc in pc.children:
                        if gc.type in _TYPE_NODE_KINDS:
                            return self._node_text(gc, source).strip()
                elif pc.type in _TYPE_NODE_KINDS:
                    return self._node_text(pc, source).strip()
        return None

    # ------------------------------------------------------------ call parsing

    def _parse_call(self, node: Node, source: bytes) -> Optional[Edge]:
        """call_expression -> Edge(kind='calls').

        Target is the callee name: bare ``Foo()`` -> ``Foo``; ``pkg.Foo()`` or
        ``recv.Method()`` -> ``Foo``/``Method``. When there's a receiver, capture
        its base type for the resolver's type-aware tier (best-effort: the
        receiver is often a local variable whose type isn't visible here).
        """
        callee = None
        receiver_text = None
        for child in node.children:
            if child.type == "identifier":
                callee = self._node_text(child, source).strip()
            elif child.type == "field_identifier":
                callee = self._node_text(child, source).strip()
            elif child.type == "selector_expression":
                # selector_expression: operand '.' field_identifier
                callee, receiver_text = self._split_selector(child, source)

        if not callee:
            return None
        owner = self._current_edge_owner()
        return Edge(
            source_name=owner,
            kind="calls",
            target_name=callee,
            line=node.start_point[0] + 1,
            receiver_type=self._infer_receiver_type(receiver_text),
        )

    def _split_selector(self, node: Node, source: bytes):
        """Split a selector_expression into (field_name, receiver_text)."""
        field_name = None
        operand_text = None
        for child in node.children:
            if child.type == "field_identifier" and field_name is None:
                field_name = self._node_text(child, source).strip()
            elif child.type in ("identifier", "call_expression", "selector_expression"):
                if operand_text is None:
                    operand_text = self._node_text(child, source).strip()
        return field_name, operand_text

    # ------------------------------------------------------------- import parse

    def _parse_imports(self, node: Node, source: bytes) -> List[Import]:
        """import_declaration -> one Import per import_spec.

        Handles both single (``import "fmt"``) and grouped
        (``import ( "a"; "b" )``) forms.
        """
        imports: List[Import] = []
        # import_specs may be direct children or nested under import_spec_list.
        for spec in self._all_import_specs(node):
            path = None
            for child in spec.children:
                if child.type == "interpreted_string_literal":
                    raw = self._node_text(child, source).strip()
                    # Strip the surrounding quotes.
                    path = raw.strip('"')
                    break
            if path:
                imports.append(Import(imported_path=path, line=spec.start_point[0] + 1))
        return imports

    def _all_import_specs(self, node: Node):
        """Yield every import_spec under an import_declaration (any depth)."""
        found = []
        stack = list(node.children)
        while stack:
            cur = stack.pop(0)
            if cur.type == "import_spec":
                found.append(cur)
            else:
                stack.extend(cur.children)
        return found

    # ---------------------------------------------------------------- helpers

    def _children_of_type(self, node: Node, *types: str) -> List[Node]:
        return [c for c in node.children if c.type in types]

    def _has_child(self, node: Node, *types: str) -> bool:
        return any(c.type in types for c in node.children)


# Tree-sitter node kinds that represent a type reference in Go. Used by the
# signature/return-type and receiver-type extraction. ``qualified_type`` covers
# ``pkg.Type``; ``pointer_type`` is handled separately (it wraps a base type).
_TYPE_NODE_KINDS = {
    "type_identifier",
    "qualified_type",
    "pointer_type",
    "slice_type",
    "array_type",
    "map_type",
    "channel_type",
    "function_type",
    "struct_type",
    "interface_type",
    "type_alias",
}
