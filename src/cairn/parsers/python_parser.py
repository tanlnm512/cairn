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
from .base import (
    BaseParser,
    Edge,
    Import,
    ParsedFile,
    ScopeTypeTracker,
    Symbol,
    TreeSitterParserBase,
)

# Annotation tokens that never name a repo symbol; excluded from references
# edges so builtin typing shapes stay out of the graph.
_BUILTIN_TYPE_TOKENS = frozenset({
    "any", "bool", "bytes", "callable", "class", "cls", "dict", "final",
    "float", "frozenset", "int", "iterable", "list", "mapping", "none",
    "optional", "self", "sequence", "set", "str", "tuple", "type", "typing",
    "union",
})

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# Plain (possibly dotted) type names; subscripts, string forward-refs and
# unions are not single types and abstain.
_DOTTED_TYPE_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
)


class PythonParser(BaseParser, TreeSitterParserBase):
    language = "python"

    def __init__(self):
        super().__init__()
        self._parser = _get_ts_parser("python")
        self._pending_edges: List[Edge] = []
        # Scope-ordered var->type tracker feeding receiver-type inference.
        self._types = ScopeTypeTracker()
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
        self._types.reset()
        self._walk(tree.root_node, source, pf)
        pf.edges.extend(self._pending_edges)
        return pf

    def _walk(self, node: Node, source: bytes, pf: ParsedFile):
        for child in node.children:
            self._visit(child, source, pf)

    def _visit(self, node: Node, source: bytes, pf: ParsedFile):
        t = node.type

        if t == "import_statement" or t == "import_from_statement":
            pf.imports.extend(self._parse_imports(node, source))
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
                # The class name binds in the enclosing scope; nested scopes
                # resolve it innermost-first.
                self._types.record(sym.name, sym.name)
                self._types.push()
                self._walk(node, source, pf)
                self._types.pop()
                self._scope.pop()
            return

        if t == "function_definition":
            sym = self._parse_function(node, source)
            if sym:
                pf.symbols.append(sym)
                owning_class = (
                    self._scope[-1] if self._is_class_owned(node) else None
                )
                self._scope.append(sym.name)
                self._types.push()
                self._record_function_bindings(node, source, owning_class)
                self._walk(node, source, pf)
                self._types.pop()
                self._scope.pop()
            return

        if t == "assignment":
            self._record_assignment(node, source)

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
            arity=self._count_parameters(node, source),
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
            name = self._decorator_call_name(self._node_text(child, source))
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

    def _parse_imports(self, node: Node, source: bytes) -> List[Import]:
        """Imports for one import statement.

        Statements without an alias keep the verbatim statement text — the
        shape the builder's module-base parser consumes for plain imports.
        A statement carrying an `as`-alias emits one normalized dotted row
        per imported name (`import x.y as z` -> "x.y"/"z", `from m import n
        as k` -> "m.n"/"k") so the resolver can rewrite the local alias to
        the imported name. Relative markers drop out ("."-segments name
        nothing).
        """
        line = node.start_point[0] + 1
        names = [
            child
            for i, child in enumerate(node.children)
            if node.field_name_for_child(i) == "name"
        ]
        if not any(c.type == "aliased_import" for c in names):
            text = self._node_text(node, source).replace("\n", " ").strip()
            return [Import(imported_path=text, line=line)]
        module = self._from_module_text(node, source)
        imports: List[Import] = []
        for child in names:
            if child.type == "aliased_import":
                inner = child.child_by_field_name("name")
                alias = child.child_by_field_name("alias")
                if inner is None or alias is None:
                    continue
                imports.append(
                    Import(
                        imported_path=self._join_import_path(
                            module, self._node_text(inner, source)
                        ),
                        line=line,
                        local_alias=self._node_text(alias, source).strip(),
                    )
                )
            else:
                imports.append(
                    Import(
                        imported_path=self._join_import_path(
                            module, self._node_text(child, source)
                        ),
                        line=line,
                    )
                )
        return imports

    def _from_module_text(self, node: Node, source: bytes) -> str:
        mod = node.child_by_field_name("module_name")
        if mod is None:
            return ""
        segs = [s for s in self._node_text(mod, source).split(".") if s]
        return ".".join(segs)

    def _join_import_path(self, module: str, name: str) -> str:
        name = name.strip()
        if module and name:
            return f"{module}.{name}"
        return name

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
            receiver_type=self._infer_call_receiver_type(callee, source),
            call_arity=self._count_call_arguments(node),
        )

    def _infer_call_receiver_type(
        self, callee: Node, source: bytes
    ) -> Optional[str]:
        """Receiver type for ``obj.method()``: a bare-identifier object with
        an unambiguous in-file binding (class name, ``self``/``cls``, typed
        or constructor assignment, annotated parameter). Anything else —
        chained receivers, unknown or shadowed names — abstains to None.
        """
        if callee.type != "attribute":
            return None
        obj = callee.child_by_field_name("object")
        if obj is None or obj.type != "identifier":
            return None
        return self._types.resolve(self._node_text(obj, source).strip())

    def _count_call_arguments(self, node: Node) -> Optional[int]:
        """Argument count of the call's argument_list; splats expand to an
        unknown count, so they abstain to None."""
        args = node.child_by_field_name("arguments")
        if args is None:
            return None
        count = 0
        for child in args.children:
            ct = child.type
            if ct in ("(", ")", ","):
                continue
            if ct in ("list_splat", "dictionary_splat"):
                return None
            count += 1
        return count

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

    def _count_parameters(self, node: Node, source: bytes) -> Optional[int]:
        """Parameter count of a def, conservative per D-005: defaults and
        varargs make the accepted count a range, and a first parameter that
        is not a recognizable ``self``/``cls`` receiver makes the call-site
        comparison off by one — all three abstain to None.
        """
        params = node.child_by_field_name("parameters")
        if params is None:
            return None
        static = any(
            self._decorator_call_name(self._node_text(c, source)) == "staticmethod"
            for c in self._decorator_nodes(node)
        )
        skip_receiver = self._is_class_owned(node) and not static
        count = 0
        seen_param = False
        for child in params.children:
            ct = child.type
            if ct in (
                "default_parameter",
                "typed_default_parameter",
                "list_splat_pattern",
                "dictionary_splat_pattern",
            ):
                return None
            if ct not in ("identifier", "typed_parameter"):
                continue
            name = self._param_name(child, source)
            if not name:
                return None
            if not seen_param and skip_receiver:
                if name not in ("self", "cls"):
                    return None
                seen_param = True
                continue
            seen_param = True
            count += 1
        return count

    def _record_function_bindings(
        self, node: Node, source: bytes, owning_class: Optional[str]
    ) -> None:
        """Enter a function's bindings in the tracker: an implicit
        ``self``/``cls`` receiver binds the owning class, annotated
        parameters bind their annotation's type."""
        params = node.child_by_field_name("parameters")
        if params is None:
            return
        static = any(
            self._decorator_call_name(self._node_text(c, source)) == "staticmethod"
            for c in self._decorator_nodes(node)
        )
        first = True
        for child in params.children:
            if child.type not in (
                "identifier",
                "typed_parameter",
                "default_parameter",
                "typed_default_parameter",
            ):
                continue
            name = self._param_name(child, source)
            if name:
                if (
                    first
                    and owning_class
                    and not static
                    and name in ("self", "cls")
                ):
                    self._types.record(name, owning_class)
                else:
                    tname = self._annotation_type(
                        child.child_by_field_name("type"), source
                    )
                    if tname:
                        self._types.record(name, tname)
            first = False

    def _record_assignment(self, node: Node, source: bytes) -> None:
        """Bind a plain-name assignment in the tracker: the annotation when
        present, else a Capitalized constructor call on the right-hand side."""
        left = node.child_by_field_name("left")
        if left is None or left.type != "identifier":
            return
        name = self._node_text(left, source).strip()
        if not name:
            return
        tname = self._annotation_type(node.child_by_field_name("type"), source)
        if tname is None:
            tname = self._constructor_call_type(
                node.child_by_field_name("right"), source
            )
        if tname:
            self._types.record(name, tname)

    def _annotation_type(self, type_node: Optional[Node], source: bytes) -> Optional[str]:
        """Type name from an annotation node: only plain (possibly dotted)
        names count; subscripts, string forward-refs and unions abstain."""
        if type_node is None:
            return None
        text = self._node_text(type_node, source).strip()
        if not _DOTTED_TYPE_RE.fullmatch(text):
            return None
        return text.rsplit(".", 1)[-1]

    def _constructor_call_type(
        self, right: Optional[Node], source: bytes
    ) -> Optional[str]:
        """Type of a ``Foo(...)`` right-hand side (bare Capitalized callee)."""
        if right is None or right.type != "call":
            return None
        fn = right.child_by_field_name("function")
        if fn is None or fn.type != "identifier":
            return None
        text = self._node_text(fn, source).strip()
        return text if text[:1].isupper() else None

    def _param_name(self, param: Node, source: bytes) -> Optional[str]:
        """Name of one parameter node, whichever shape it takes."""
        if param.type == "identifier":
            return self._node_text(param, source).strip()
        named = param.child_by_field_name("name")
        if named is not None:
            return self._node_text(named, source).strip()
        for child in param.children:
            if child.type == "identifier":
                return self._node_text(child, source).strip()
        return None

    def _is_class_owned(self, node: Node) -> bool:
        """True when the def sits directly in a class body, seeing through
        the ``decorated_definition`` wrapper."""
        parent = node.parent
        if parent is not None and parent.type == "decorated_definition":
            parent = parent.parent
        return (
            parent is not None
            and parent.type == "block"
            and parent.parent is not None
            and parent.parent.type == "class_definition"
        )

    def _decorator_call_name(self, text: str) -> str:
        """Callable name of a decorator's text: ``@a.b(c)`` -> ``b``."""
        name = text.strip().lstrip("@").split("(", 1)[0].strip()
        if "." in name:
            name = name.rsplit(".", 1)[-1]
        return name
