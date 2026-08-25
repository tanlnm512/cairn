"""Tree-sitter Kotlin parser.

Extracts class/interface/enum/object declarations, function/method declarations,
property declarations, call expressions, imports, and inheritance into the
shared ParsedFile model.
"""
from __future__ import annotations

from typing import List, Optional

from tree_sitter import Node

from ._registry import get_parser as _get_ts_parser
from .base import BaseParser, Edge, Import, ParsedFile, Symbol, TreeSitterParserBase

# Modifiers we record (see spec). Visibility, inheritance, and special keywords.
KOTLIN_MODIFIERS = {
    "public", "private", "protected", "internal",
    "open", "abstract", "sealed", "data", "suspend",
    "companion", "lateinit", "override", "final", "const", "static",
    "value", "tailrec", "external", "infix", "operator",
}

# Node types that declare a named type containing members.
TYPE_DECL_NODES = {
    "class_declaration",
    "object_declaration",
    "interface_declaration",
    "enum_declaration",
}

# Node types representing a function/method.
FUNC_DECL_NODES = {"function_declaration"}


class KotlinParser(BaseParser, TreeSitterParserBase):
    language = "kotlin"

    def __init__(self):
        super().__init__()
        self._parser = _get_ts_parser("kotlin")
        self._scope_kinds: List[str] = []
        # Per-parse accumulator for inheritance edges.
        self._pending_edges: List[Edge] = []
        # In-file receiver-type tracker: a stack of {var_name: type_name}
        # scopes, one per enclosing function.
        self._var_types: List[dict] = [{}]
        # {type_name: {field_name: type_name}}, populated by _prescan_field_types
        # before the main walk.
        self._field_types: dict = {}

    def parse(self, path: str) -> ParsedFile:
        source = open(path, "rb").read()
        tree = self._parser.parse(source)

        import hashlib
        file_hash = hashlib.sha256(source).hexdigest()

        pf = ParsedFile(
            path=path,
            language=self.language,
            hash=file_hash,
            line_count=source.count(b"\n") + 1,
        )

        # Parsers are cached singletons reused across files, so reset all
        # per-file accumulators here.
        self._pending_edges = []
        self._scope = []
        self._scope_kinds = []
        self._callable_scope = []
        # In-file receiver-type tracker: a stack of {var_name: type_name}
        # scopes, one per enclosing function.
        self._var_types: List[dict] = [{}]
        # {type_name: {field_name: type_name}}, populated by _prescan_field_types
        # before the main walk.
        self._field_types: dict = {}
        self._prescan_field_types(tree.root_node, source)

        self._walk(tree.root_node, source, pf)
        pf.edges.extend(self._pending_edges)
        return pf

    # --- field-type pre-scan ----------------------------------------------

    def _prescan_field_types(self, node: Node, source: bytes) -> None:
        """Populate self._field_types with {type: {field: type}} before the walk."""
        for child in node.children:
            if child.type in TYPE_DECL_NODES:
                type_name = self._parse_type_identifier(child, source)
                if type_name:
                    fields = self._field_types.setdefault(type_name, {})
                    for cc in child.children:
                        if cc.type == "primary_constructor":
                            for pc in cc.children:
                                if pc.type == "class_parameter":
                                    pname, ptype = self._class_param_name_and_type(pc, source)
                                    if pname and ptype:
                                        fields[pname] = ptype
                        elif cc.type in ("class_body", "enum_class_body"):
                            for member in cc.children:
                                if member.type == "property_declaration":
                                    pname, ptype = self._var_name_and_type(member, source)
                                    if pname and ptype:
                                        fields[pname] = ptype
                            # Recurse for nested type declarations.
                            self._prescan_field_types(cc, source)
            else:
                self._prescan_field_types(child, source)

    # --- traversal ---------------------------------------------------------

    def _walk(self, node: Node, source: bytes, pf: ParsedFile):
        for child in node.children:
            self._visit(child, source, pf)

    def _visit(self, node: Node, source: bytes, pf: ParsedFile):
        t = node.type

        # Accept both `import` and `import_header` so imports aren't
        # silently dropped across grammar updates.
        if t in ("import", "import_header"):
            imp = self._parse_import(node, source)
            if imp:
                pf.imports.append(imp)
            return  # don't descend

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
                self._callable_scope.append(sym.name)
                # Push a var-type scope for this function, seeded with
                # `this` -> enclosing type and typed parameters.
                scope = {}
                if self._scope:
                    scope["this"] = self._scope[-1]
                for pname, ptype in self._param_types(node, source):
                    scope[pname] = ptype
                self._var_types.append(scope)
                self._walk(node, source, pf)
                self._var_types.pop()
                self._callable_scope.pop()
            return

        if t == "property_declaration":
            sym = self._parse_property(node, source)
            if sym:
                pf.symbols.append(sym)
            # Only track local var types inside a function body; class-level
            # property types are covered by the _field_types pre-scan.
            if self._callable_scope:
                name, vtype = self._var_name_and_type(node, source)
                if name and vtype:
                    self._var_types[-1][name] = vtype
            self._walk(node, source, pf)
            return

        if t == "class_parameter":
            # Constructor parameter like `private val baseUrl: String`.
            # These are properties when they declare val/var.
            sym = self._parse_class_parameter(node, source)
            if sym:
                pf.symbols.append(sym)
            return  # leaf; no further meaningful children

        if t == "call_expression":
            edge = self._parse_call(node, source)
            if edge:
                pf.edges.append(edge)
            self._walk(node, source, pf)
            return

        # Default: descend.
        self._walk(node, source, pf)

    # --- name & modifier helpers ------------------------------------------

    def _classify_type_decl(self, node: Node, source: bytes) -> str:
        """Classify a type declaration node into a symbol kind.

        The vendored fwcd grammar folds `interface` and `enum class` into
        class_declaration/object_declaration with a leading keyword child.
        """
        if node.type == "class_declaration":
            # Look at the first keyword child: interface | class | (enum class)
            for child in node.children:
                txt = self._node_text(child, source).strip()
                if txt == "interface":
                    return "interface"
                if txt == "enum":
                    return "enum"
                if txt == "class":
                    return "class"
            return "class"
        if node.type == "object_declaration":
            return "class"
        if node.type == "interface_declaration":
            return "interface"
        if node.type == "enum_declaration":
            return "enum"
        return "class"

    def _collect_modifiers(self, node: Node, source: bytes) -> List[str]:
        mods = []
        for child in node.children:
            if child.type == "modifiers":
                for m in child.children:
                    # open/sealed/abstract sit in an inheritance_modifier
                    # wrapper's anonymous keyword children.
                    kws = m.children if m.type == "inheritance_modifier" else (m,)
                    for kw in kws:
                        txt = self._node_text(kw, source).strip()
                        if txt in KOTLIN_MODIFIERS:
                            mods.append(txt)
            elif child.type in KOTLIN_MODIFIERS:
                txt = self._node_text(child, source).strip()
                if txt:
                    mods.append(txt)
        return mods

    def _parse_type_identifier(self, node: Node, source: bytes) -> Optional[str]:
        """Find the declared name of a type/function node."""
        for child in node.children:
            if child.type in ("type_identifier", "simple_identifier", "identifier"):
                return self._node_text(child, source).strip()
        return None

    # --- declarations ------------------------------------------------------

    def _parse_type_decl(self, node: Node, source: bytes) -> Optional[Symbol]:
        name = self._parse_type_identifier(node, source)
        if not name:
            return None

        # `interface Foo {}` parses as a class_declaration whose first
        # keyword child is `interface`. Same for object/enum.
        kind = self._classify_type_decl(node, source)

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
        # Inheritance edges (accumulated into _pending_edges).
        self._parse_inheritance(node, source, sym.name)
        return sym

    def _parse_function(self, node: Node, source: bytes) -> Optional[Symbol]:
        # function_declaration: [modifiers] 'fun' [<type-params>] name '(' ... ')'
        name = None
        for child in node.children:
            if child.type in ("simple_identifier", "identifier"):
                name = self._node_text(child, source).strip()
                break
        if not name:
            return None

        # method if declared inside a class/object; function at top level.
        kind = "method" if any(
            k in ("class_declaration", "object_declaration")
            for k in self._scope_kinds
        ) else "function"

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
        return sym

    def _parse_property(self, node: Node, source: bytes) -> Optional[Symbol]:
        # property_declaration: [modifiers] (val|var) variable_declaration ...
        # The name lives inside variable_declaration; accept both
        # `simple_identifier` and `identifier` spellings -- the sibling
        # extractors (_var_name_and_type,
        # _parse_function, _parse_type_identifier) already do. Without the
        # `identifier` spelling every class-body val/var produced no Symbol.
        name = None
        for child in node.children:
            if child.type == "variable_declaration":
                for vc in child.children:
                    if vc.type in ("simple_identifier", "identifier"):
                        name = self._node_text(vc, source).strip()
                        break
            elif child.type in ("simple_identifier", "identifier") and name is None:
                name = self._node_text(child, source).strip()
        if not name:
            return None
        kind = "property"
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
        return sym

    def _parse_class_parameter(self, node: Node, source: bytes) -> Optional[Symbol]:
        """Constructor parameter: [modifiers] (val|var) name : Type.

        Treated as a property when it has val/var (a real backing field).
        """
        has_val_or_var = False
        name = None
        for child in node.children:
            txt = self._node_text(child, source).strip()
            if txt in ("val", "var"):
                has_val_or_var = True
            elif child.type in ("simple_identifier", "identifier") and name is None:
                name = txt
        if not name or not has_val_or_var:
            return None  # plain ctor param, not a property
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

    def _parse_import(self, node: Node, source: bytes) -> Optional[Import]:
        # `import` node. Extract the imported path from the identifier
        # child if present, else from the node text. Drop trailing 'as Alias'.
        text = self._node_text(node, source).strip()
        if text.startswith("import "):
            text = text[len("import "):].strip()
        text = text.split(" as ")[0].strip()
        # Prefer the identifier child text when available (cleaner).
        for child in node.children:
            if child.type == "identifier":
                text = self._node_text(child, source).strip()
                break
        if not text:
            return None
        return Import(imported_path=text, line=node.start_point[0] + 1)

    # --- edges -------------------------------------------------------------


    def _parse_inheritance(self, node: Node, source: bytes, child_name: str):
        """Emit implements/extends edges for a type declaration.

        Kotlin uses ':' for both extends and implements, distinguished by node
        type: a superclass (extends) is a `constructor_invocation` `Base(...)`;
        interfaces (implements) are plain `user_type` targets.
        """
        # Each supertype is a direct `delegation_specifier` child (the plural
        # container is hidden). Targets are (name, is_extends).
        targets: List[tuple] = []
        for child in node.children:
            if child.type == "delegation_specifier":
                self._collect_inheritance_targets(child, source, targets)
        for name, is_extends in targets:
            self._pending_edges.append(
                Edge(
                    source_name=child_name,
                    kind="extends" if is_extends else "implements",
                    target_name=name,
                    line=node.start_point[0] + 1,
                )
            )

    def _collect_inheritance_targets(
        self, node: Node, source: bytes, targets: List[tuple]
    ):
        """Recursively gather (name, is_extends) inheritance targets.

        For a `constructor_invocation` `Base(...)`, the target name is the inner
        `user_type`'s identifier (not the argument list). Plain `user_type`
        targets are interfaces (implements).
        """
        if node is None:
            return
        if node.type == "constructor_invocation":
            for child in node.children:
                if child.type == "user_type":
                    name = self._extract_usertype_name(child, source)
                    if name:
                        targets.append((name, True))
                    return
            return
        if node.type == "user_type":
            name = self._extract_usertype_name(node, source)
            if name:
                targets.append((name, False))
            return
        for child in node.children:
            self._collect_inheritance_targets(child, source, targets)

    def _parse_call(self, node: Node, source: bytes) -> Optional[Edge]:
        target = self._extract_call_target_name(node, source)
        if not target:
            return None
        receiver_type = self._infer_call_receiver_type(node, source)

        # Kotlin operator-invoke sugar: `someUseCase(params)` desugars to
        # `someUseCase.invoke(params)` on a DI-injected property/param/local.
        # Rewrite the edge target from the variable name to its declared TYPE
        # so callers reach the shared class.
        if receiver_type is None:
            bare = self._bare_callee_identifier(node, source)
            if bare is not None and bare == target and bare[:1].islower():
                inferred = self._resolve_bare_name_type(bare)
                if inferred and inferred != bare and inferred[:1].isupper():
                    target = inferred

        # The same operator-invoke sugar applies to the explicit-receiver
        # shape `this.prop(c)` / `obj.prop(c)` where `prop` is a DI-injected
        # property of a UseCase type. Rewrite the target from the property name
        # to the type name so the edge resolves to the shared UseCase class.
        enclosing = self._scope[-1] if self._scope else None
        declared_type_of_target = (
            self._field_types.get(enclosing, {}).get(target) if enclosing else None
        )
        if (
            receiver_type
            and target[:1].islower()
            and receiver_type[:1].isupper()
            and target != receiver_type
            and receiver_type != enclosing
            # Guard: only fire when the receiver_type IS the declared type of
            # this target (property-invoke), not a method call.
            and declared_type_of_target == receiver_type
        ):
            target = receiver_type

        return Edge(
            source_name=self._current_edge_owner(),
            kind="calls",
            target_name=target,
            line=node.start_point[0] + 1,
            receiver_type=receiver_type,
        )

    # --- receiver-type inference ------------------------------------------

    def _param_types(self, fn_node: Node, source: bytes) -> List[tuple]:
        """Yield (param_name, type_name) for typed function parameters."""
        out = []
        for ch in fn_node.children:
            if ch.type == "function_value_parameters":
                for p in ch.children:
                    if p.type in ("parameter", "function_value_parameter"):
                        pname = None
                        ptype = None
                        for pc in p.children:
                            if pc.type in ("simple_identifier", "identifier") and pname is None:
                                pname = self._node_text(pc, source).strip()
                            elif pc.type in ("user_type", "type_reference", "nullable_type"):
                                ptype = self._extract_usertype_name(pc, source)
                        if pname and ptype:
                            out.append((pname, ptype))
        return out

    def _var_name_and_type(self, prop_node: Node, source: bytes):
        """(name, type) for a property_declaration: `val x: Foo`, `val x = Foo(...)`,
        or `val x` (name only, no inferable type). Returns (None, None) if no name.
        """
        name = None
        vtype = None
        for child in prop_node.children:
            if child.type == "variable_declaration":
                for vc in child.children:
                    if vc.type in ("simple_identifier", "identifier") and name is None:
                        name = self._node_text(vc, source).strip()
                    elif vc.type in ("user_type", "type_reference", "nullable_type"):
                        vtype = self._extract_usertype_name(vc, source)
            elif child.type in ("simple_identifier", "identifier") and name is None:
                name = self._node_text(child, source).strip()
        if not vtype:
            vtype = self._ctor_call_type(prop_node, source)
        return name, vtype

    def _class_param_name_and_type(self, node: Node, source: bytes):
        """(name, type) for a `val`/`var` primary-constructor parameter.

        Plain constructor params (no val/var, not a backing field) return
        (None, None) -- they aren't accessible as `this.x` outside __init__.
        """
        has_val_or_var = False
        name = None
        vtype = None
        for child in node.children:
            txt = self._node_text(child, source).strip()
            if txt in ("val", "var"):
                has_val_or_var = True
            elif child.type in ("simple_identifier", "identifier") and name is None:
                name = txt
            elif child.type in ("user_type", "type_reference", "nullable_type"):
                vtype = self._extract_usertype_name(child, source)
        if has_val_or_var:
            return name, vtype
        return None, None

    def _ctor_call_type(self, node: Node, source: bytes) -> Optional[str]:
        """If node's initializer is a constructor call `Foo(...)`, return `Foo`."""
        stack = list(node.children)
        while stack:
            n = stack.pop()
            if n.type == "call_expression":
                lead = n.children[0] if n.children else None
                if lead is not None and lead.type in ("simple_identifier", "identifier"):
                    text = self._node_text(lead, source).strip()
                    if text[:1].isupper():   # heuristic: types are Capitalized
                        return text
            stack.extend(n.children)
        return None

    def _infer_node_type(self, node: Optional[Node], source: bytes) -> Optional[str]:
        """Best-effort type of an arbitrary receiver expression node.

        Handles `this_expression`, a bare `identifier`, a constructor
        `call_expression`, and a `navigation_expression` (field access chain).
        Returns None for anything else.
        """
        if node is None:
            return None
        if node.type == "this_expression":
            return self._scope[-1] if self._scope else None
        if node.type in ("simple_identifier", "identifier"):
            name = self._node_text(node, source).strip()
            return self._resolve_bare_name_type(name)
        if node.type == "call_expression":
            # Constructor call `Foo(...)` -> type is `Foo`. A call through a
            # navigation chain (`factory.build()`) isn't a constructor call in
            # any obvious way here, so we abstain rather than guess a return
            # type.
            if node.children and node.children[0].type in ("simple_identifier", "identifier"):
                text = self._node_text(node.children[0], source).strip()
                if text[:1].isupper():
                    return text
            return None
        if node.type == "navigation_expression":
            # `a.b` (as a value, not a call): type is the type of field `b`
            # on the type of `a`. The receiver is the first child; the member
            # sits inside the trailing navigation_suffix.
            member_node = self._nav_member_identifier(node)
            if member_node is None:
                return None
            member_name = self._node_text(member_node, source).strip()
            recv_type = self._infer_node_type(node.children[0], source)
            if not recv_type:
                return None
            return self._field_types.get(recv_type, {}).get(member_name)
        return None

    def _resolve_bare_name_type(self, name: str) -> Optional[str]:
        """Type of a bare identifier: local/param (innermost scope first),
        else an implicit `this.<name>` field on the enclosing type, else -- if
        capitalized -- a reference to that type itself (static/companion call
        receiver, e.g. `Profile.create()`).
        """
        for scope in reversed(self._var_types):
            if name in scope:
                return scope[name]
        if self._scope:
            field_type = self._field_types.get(self._scope[-1], {}).get(name)
            if field_type:
                return field_type
        if name[:1].isupper():
            return name
        return None

    def _nav_member_identifier(self, nav: Node) -> Optional[Node]:
        """Final member identifier of a navigation_expression: the
        simple_identifier inside the trailing navigation_suffix (the receiver
        is the first child)."""
        for c in reversed(nav.children):
            if c.type in ("simple_identifier", "identifier"):
                return c
            if c.type == "navigation_suffix":
                for sc in reversed(c.children):
                    if sc.type in ("simple_identifier", "identifier"):
                        return sc
        return None

    def _infer_call_receiver_type(self, node: Node, source: bytes) -> Optional[str]:
        """Receiver type for a call_expression, if the call is `X.method()`.

        Bare calls (`method()`, no receiver) return None. Also handles the
        property-invoke shape `this.prop(c)` / `obj.prop(c)` where the invoked
        thing is the property itself (commonly a Kotlin ``operator fun invoke``
        UseCase); in that case the receiver type is the declared type of the
        whole nav expr, letting the rewrite fire.
        """
        nav = None
        for child in node.children:
            if child.type == "navigation_expression":
                nav = child
        if nav is None or not nav.children:
            return None
        # Identify the final identifier segment of the nav (the callee name).
        member_node = self._nav_member_identifier(nav)
        member_name = self._node_text(member_node, source).strip() if member_node else ""

        # Property-invoke shape: the final segment is the property being
        # invoked (lowercase value name) AND the whole nav resolves to a
        # declared field type.
        if member_name and not member_name[:1].isupper():
            whole_type = self._infer_node_type(nav, source)
            if whole_type:
                return whole_type

        # Standard `recv.method()` call: receiver is everything before the
        # final segment.
        receiver_expr = nav.children[0]
        return self._infer_node_type(receiver_expr, source)

    def _bare_callee_identifier(self, node: Node, source: bytes) -> Optional[str]:
        """The callee identifier text iff ``node`` is a truly bare call
        `name(...)` -- its first child is a plain identifier. Returns None
        otherwise, so the operator-invoke rewrite only fires for this shape.
        """
        if not node.children:
            return None
        lead = node.children[0]
        if lead.type in ("simple_identifier", "identifier"):
            return self._node_text(lead, source).strip()
        return None

    def _extract_call_target_name(self, node: Node, source: bytes) -> Optional[str]:
        """Extract the called name from a call_expression.

        The called name is the last simple_identifier before the value_arguments.
        """
        last_id = None
        for child in node.children:
            if child.type in ("simple_identifier", "identifier"):
                last_id = self._node_text(child, source).strip()
            if child.type == "navigation_expression":
                last_id = self._tail_identifier(child, source) or last_id
            if child.type == "call_suffix":
                break
        # If it's like `Retrofit.Builder().build()`, prefer the tail (build).
        if last_id is None:
            last_id = self._tail_identifier(node, source)
        return last_id

    def _extract_usertype_name(self, node: Node, source: bytes) -> Optional[str]:
        for child in node.children:
            if child.type in ("type_identifier", "identifier"):
                return self._node_text(child, source).strip()
        return None

    def _tail_identifier(self, node: Node, source: bytes) -> Optional[str]:
        """Last simple_identifier in a subtree (the called property)."""
        last = None
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type in ("simple_identifier", "identifier"):
                last = self._node_text(n, source).strip()
            stack.extend(reversed(n.children))
        return last
