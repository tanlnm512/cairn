"""Tree-sitter Swift parser.

Extracts class/struct/protocol/enum declarations, functions, properties, call
expressions, imports, and inheritance into the shared ParsedFile model.

Note: Swift `enum`/`struct` may appear under class_declaration-style nodes with
a leading keyword; classification inspects the keyword (same approach as Kotlin).

Signals: call edges carry the receiver type read from the
navigation_expression's `target` field one level down (swift 0.7.3 exposes no
field labels on call_expression itself) and an argument count; function
symbols carry a parameter-count arity (variadic → None). Swift has no import
aliasing, so local_alias stays None for every import form.
"""
from __future__ import annotations

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
        self._var_types = ScopeTypeTracker()

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
        self._var_types.reset()
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
                self._var_types.push()
                self._var_types.record("self", sym.name)
                self._walk(node, source, pf)
                self._var_types.pop()
                self._scope.pop()
                self._scope_kinds.pop()
            return

        if t == "function_declaration":
            sym = self._parse_function(node, source)
            if sym:
                pf.symbols.append(sym)
                self._scope.append(sym.name)
                self._var_types.push()
                self._record_param_types(node, source)
                self._walk(node, source, pf)
                self._var_types.pop()
                self._scope.pop()
            return

        if t in ("property_declaration",):
            sym = self._parse_property(node, source)
            if sym:
                pf.symbols.append(sym)
            self._record_property_type(node, source)
            self._walk(node, source, pf)
            return

        if t == "call_expression":
            edge = self._parse_call(node, source)
            if edge:
                pf.edges.append(edge)
            self._walk(node, source, pf)
            return

        if t == "statements":
            # Each brace level is a lexical scope for var→type tracking.
            self._var_types.push()
            self._walk(node, source, pf)
            self._var_types.pop()
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
                # The nested modifiers node can contain non-modifier children
                # (e.g. an `attribute` like @available(...)). Filter through
                # SWIFT_MODIFIERS so only real modifier keywords are kept --
                # matching the direct-child path below and the Java/Kotlin
                # extractors' pattern. Without this, an attribute's text
                # pollutes the symbol's modifier list.
                for m in child.children:
                    txt = self._node_text(m, source).strip()
                    if txt and txt in SWIFT_MODIFIERS:
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
        arity = self._param_arity(node, source)
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
                    arity=arity,
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
                    arity=arity,
                )
        return None

    def _param_arity(self, node: Node, source: bytes) -> Optional[int]:
        """Parameter count of a function_declaration; None when a variadic
        parameter (``...``) makes the declared count unknowable."""
        count = 0
        for child in node.children:
            if child.type != "parameter":
                continue
            if any(
                not c.is_named and self._node_text(c, source) == "..."
                for c in child.children
            ):
                return None
            count += 1
        return count

    def _record_param_types(self, node: Node, source: bytes) -> None:
        """Record each parameter's local name → declared type."""
        for child in node.children:
            if child.type == "parameter":
                self._var_types.record(*self._param_name_type(child, source))

    def _param_name_type(self, node: Node, source: bytes):
        """(local_name, declared_type) of a ``parameter``.

        An external argument label may precede the local name, so the local
        name is the last identifier before the type.
        """
        name = None
        type_name = None
        for c in node.children:
            if c.type in ("simple_identifier", "identifier"):
                name = self._node_text(c, source).strip()
            elif c.type == "user_type":
                type_name = self._type_identifier_text(c, source)
                break
        return name, type_name

    def _record_property_type(self, node: Node, source: bytes) -> None:
        """Record a ``let``/``var`` binding's name → type for receiver lookup.

        The declared type annotation wins; otherwise a ``Type()`` initializer
        call (capitalized callee) infers the type. Anything else abstains.
        """
        name = None
        type_name = None
        for child in node.children:
            if child.type == "pattern":
                for c in child.children:
                    if c.type in ("simple_identifier", "identifier"):
                        name = self._node_text(c, source).strip()
            elif child.type == "type_annotation":
                for c in child.children:
                    if c.type == "user_type":
                        type_name = self._type_identifier_text(c, source)
            elif type_name is None and child.type == "call_expression":
                callee = self._initializer_callee(child, source)
                if callee:
                    type_name = callee
        self._var_types.record(name, type_name)

    def _initializer_callee(self, node: Node, source: bytes) -> Optional[str]:
        """Callee of an initializer call when it names a type (capitalized)."""
        if not node.children:
            return None
        lead = node.children[0]
        if lead.type != "simple_identifier":
            return None
        txt = self._node_text(lead, source).strip()
        return txt if txt[:1].isupper() else None

    def _type_identifier_text(self, node: Node, source: bytes) -> Optional[str]:
        for c in node.children:
            if c.type == "type_identifier":
                return self._node_text(c, source).strip()
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
        """Inheritance-clause targets as ordered `extends`/`implements` edges.

        A class/actor's first listed type is its superclass (`extends`);
        every other target, and every target on structs/enums/protocols
        (where inheritance is protocol conformance only), is `implements`.
        """
        specifiers: List[Node] = []
        for child in node.children:
            if child.type in ("type_inheritance_clause", "inheritance_specifier"):
                self._collect_specifiers(child, specifiers)
        # A grammar build may parse `struct`/`enum` declarations under
        # class_declaration; the keyword child distinguishes them (the same
        # inspection _classify_type uses). Only a true class/actor has a
        # superclass; structs/enums conform to protocols only.
        keyword_children = {
            self._node_text(c, source).strip() for c in node.children
        }
        superclass_first = node.type == "actor_declaration" or (
            node.type == "class_declaration"
            and not keyword_children & {"struct", "enum"}
        )
        for i, spec in enumerate(specifiers):
            name = self._node_text(spec, source).strip().split("<", 1)[0].strip()
            if not name:
                continue
            self._pending_edges.append(
                Edge(
                    child_name,
                    "extends" if (superclass_first and i == 0) else "implements",
                    name,
                    spec.start_point[0] + 1,
                )
            )

    def _collect_specifiers(self, node: Node, out: List[Node]) -> None:
        if node.type == "inheritance_specifier":
            out.append(node)
            return
        for child in node.children:
            self._collect_specifiers(child, out)

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
            receiver_type=self._receiver_type(self._call_receiver_text(node, source)),
            call_arity=self._call_arity(node),
        )

    def _call_receiver_text(self, node: Node, source: bytes) -> Optional[str]:
        """Text of the called navigation_expression's `target` field.

        call_expression exposes no field labels at swift 0.7.3; the receiver
        is readable only one level down, on the navigation_expression.
        """
        lead = node.children[0]
        if lead.type not in ("navigation_expression", "member_expression"):
            return None
        target = lead.child_by_field_name("target")
        if target is None:
            return None
        return self._node_text(target, source).strip()

    def _receiver_type(self, receiver: Optional[str]) -> Optional[str]:
        if receiver is None:
            return None
        return self._var_types.resolve(receiver) or self._infer_receiver_type(receiver)

    def _call_arity(self, node: Node) -> Optional[int]:
        """Argument count: value_argument children plus each trailing
        closure (each trailing closure is one argument)."""
        suffix = self._child_of_type(node, ("call_suffix",))
        if suffix is None:
            return None
        count = 0
        for child in suffix.children:
            if child.type == "value_arguments":
                count += sum(
                    1 for a in child.children if a.type == "value_argument"
                )
            elif child.type == "lambda_literal":
                count += 1
        return count

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
