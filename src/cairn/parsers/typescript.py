"""Tree-sitter TypeScript / TSX / JavaScript parser."""
from __future__ import annotations

import hashlib
from pathlib import Path
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

TS_JS_MODIFIERS = {
    "public", "private", "protected", "static", "readonly",
    "abstract", "async", "override", "declare",
}

# Node types that declare a named type containing members (class-like).
TYPE_DECL_NODES = {"class_declaration", "abstract_class_declaration", "interface_declaration"}

# Decorators are children of their target declaration in the TS grammar.
DECL_NODES_WITH_OWN_DECORATORS = TYPE_DECL_NODES | {
    "method_definition",
    "function_declaration",
    "public_field_definition",
}

# Node types treated as top-level-or-nested variable declarations.
VAR_DECL_NODES = {"lexical_declaration", "variable_declaration"}

# Extensions tried when resolving a relative import to a file on disk.
_RESOLUTION_EXTS = ("", ".ts", ".tsx", ".d.ts", ".js", ".jsx")


def resolve_relative_import(importer: Path, spec: str) -> Optional[str]:
    """Return an existing extension-stripped path for a relative TS import."""
    if not spec.startswith("."):
        return None  # package import (e.g. "react") -> left unresolved/external
    base = (importer.parent / spec).resolve()
    for candidate_base in (base, base / "index"):
        for ext in _RESOLUTION_EXTS:
            cand = Path(str(candidate_base) + ext) if ext else candidate_base
            if cand.is_file():
                return str(candidate_base)
    return None


class _JSFamilyParser(BaseParser, TreeSitterParserBase):
    """Shared traversal for the TypeScript/TSX/JavaScript grammars."""

    def _select_ts_parser(self, file_path: Path):
        raise NotImplementedError

    def parse(self, path: str) -> ParsedFile:
        file_path = Path(path)
        source = file_path.read_bytes()
        ts_parser = self._select_ts_parser(file_path)
        tree = ts_parser.parse(source)

        pf = ParsedFile(
            path=path,
            language=self.language,
            hash=hashlib.sha256(source).hexdigest(),
            line_count=source.count(b"\n") + 1,
        )

        self._path = file_path
        self._file_stem = file_path.stem
        self._pending_edges: List[Edge] = []
        self._pending_decorators: List[str] = []
        self._func_depth = 0
        self._types = ScopeTypeTracker()

        self._walk(tree.root_node, source, pf)
        pf.edges.extend(self._pending_edges)
        return pf

    # --- traversal -----------------------------------------------------

    def _visit(self, node: Node, source: bytes, pf: ParsedFile):
        t = node.type

        if t == "import_statement":
            imp = self._parse_import(node, source)
            if imp:
                pf.imports.append(imp)
            return  # don't descend; nothing else useful inside

        if t == "decorator":
            # Target-owned decorators bypass the pending queue; parameter decorators are dropped.
            parent_type = node.parent.type if node.parent is not None else None
            if parent_type in ("required_parameter", "optional_parameter"):
                return
            if parent_type in DECL_NODES_WITH_OWN_DECORATORS:
                # The declaration visitor collects its own decorator children.
                return
            self._pending_decorators.append(self._node_text(node, source).strip())
            return

        if t in TYPE_DECL_NODES:
            sym = self._parse_type_decl(node, source)
            if sym:
                pf.symbols.append(sym)
                self._scope.append(sym.name)
                self._walk(node, source, pf)
                self._scope.pop()
            return

        if t == "type_alias_declaration":
            sym = self._parse_simple_decl(node, source, "type", ("type_identifier",))
            if sym:
                pf.symbols.append(sym)
            return

        if t == "enum_declaration":
            sym = self._parse_simple_decl(node, source, "enum", ("identifier",))
            if sym:
                pf.symbols.append(sym)
            return

        if t == "internal_module":
            sym = self._parse_simple_decl(
                node, source, "namespace", ("identifier", "nested_identifier")
            )
            if sym:
                pf.symbols.append(sym)
                self._scope.append(sym.name)
                self._walk(node, source, pf)
                self._scope.pop()
            return

        if t == "function_declaration":
            sym = self._parse_function(node, source, "function")
            if sym:
                pf.symbols.append(sym)
                self._callable_scope.append(sym.name)
                self._func_depth += 1
                self._enter_function_scope(node, source)
                self._walk(node, source, pf)
                self._exit_function_scope()
                self._func_depth -= 1
                self._callable_scope.pop()
            return

        if t == "method_definition":
            sym = self._parse_function(node, source, "method")
            if sym:
                pf.symbols.append(sym)
                self._callable_scope.append(sym.name)
                self._func_depth += 1
                self._enter_function_scope(node, source)
                self._walk(node, source, pf)
                self._exit_function_scope()
                self._func_depth -= 1
                self._callable_scope.pop()
            return

        if t == "public_field_definition":
            sym = self._parse_field(node, source)
            if sym:
                pf.symbols.append(sym)
            # Field initializers can emit calls without opening a function scope.
            self._walk(node, source, pf)
            return

        if t in VAR_DECL_NODES:
            self._handle_var_decl(node, source, pf)
            return

        if t in ("arrow_function", "function_expression"):
            # Anonymous function (e.g. a callback argument): not a named
            # symbol, but its body is no longer "top-level" for var purposes.
            self._func_depth += 1
            self._enter_function_scope(node, source)
            self._walk(node, source, pf)
            self._exit_function_scope()
            self._func_depth -= 1
            return

        if t == "statement_block":
            # Block scope for let/const: recorded var types pop with the block.
            self._types.push()
            self._walk(node, source, pf)
            self._types.pop()
            return

        if t in ("call_expression", "new_expression"):
            edge = self._parse_call(node, source)
            if edge:
                pf.edges.append(edge)
            self._walk(node, source, pf)
            return

        # Opening JSX elements emit one component reference; closing tags do not.
        if t in ("jsx_opening_element", "jsx_self_closing_element"):
            edge = self._parse_jsx_ref(node, source)
            if edge:
                pf.edges.append(edge)
            self._walk(node, source, pf)
            return

        if t == "assignment_expression":
            # `x = new T()` re-binds x; a conflicting recorded type poisons
            # the name to unknown for receiver inference.
            self._record_assignment(node, source)

        self._walk(node, source, pf)

    # --- name & modifier helpers -----------------------------------------

    # _find_name inherited from TreeSitterParserBase.

    def _collect_modifiers(self, node: Node, source: bytes) -> List[str]:
        mods = []
        for child in node.children:
            if child.type == "accessibility_modifier":
                mods.append(self._node_text(child, source).strip())
            else:
                txt = self._node_text(child, source).strip()
                if txt in TS_JS_MODIFIERS:
                    mods.append(txt)
        return mods

    def _qualified_name(self, name: str) -> str:
        """TypeScript/JS use file-stem prefix for qualified names."""
        return ".".join([self._file_stem] + self._scope + [name])

    def _take_pending_decorators(self) -> List[str]:
        """Consume (and clear) decorators accumulated since the last
        declaration, so they attach to exactly one symbol and don't leak
        forward to the next one."""
        decorators = self._pending_decorators
        self._pending_decorators = []
        return decorators

    def _own_decorators(self, node: Node, source: bytes) -> List[str]:
        """Decorators that are direct children of ``node`` (e.g. a class's own
        ``@Controller(...)``)."""
        return [
            self._node_text(c, source).strip()
            for c in node.children
            if c.type == "decorator"
        ]

    # --- declarations ------------------------------------------------------

    def _classify_type_decl(self, node: Node) -> str:
        if node.type == "interface_declaration":
            return "interface"
        return "class"  # class_declaration, abstract_class_declaration

    def _parse_type_decl(self, node: Node, source: bytes) -> Optional[Symbol]:
        decorators = self._take_pending_decorators() + self._own_decorators(node, source)
        name = self._find_name(node, source, ("type_identifier", "identifier"))
        if not name:
            return None
        kind = self._classify_type_decl(node)
        mods = self._collect_modifiers(node, source) + decorators
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
        self._parse_heritage(node, source, name)
        return sym

    def _parse_simple_decl(
        self, node: Node, source: bytes, kind: str, name_types
    ) -> Optional[Symbol]:
        name = self._find_name(node, source, name_types)
        if not name:
            return None
        return Symbol(
            name=name,
            kind=kind,
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
        )

    def _parse_function(self, node: Node, source: bytes, kind: str) -> Optional[Symbol]:
        decorators = self._take_pending_decorators() + self._own_decorators(node, source)
        name_types = ("property_identifier",) if node.type == "method_definition" else ("identifier",)
        name = self._find_name(node, source, name_types)
        if not name:
            return None
        mods = self._collect_modifiers(node, source) + decorators
        return Symbol(
            name=name,
            kind=kind,
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
            modifiers=mods,
            arity=self._callable_arity(node),
        )

    def _parse_field(self, node: Node, source: bytes) -> Optional[Symbol]:
        decorators = self._take_pending_decorators() + self._own_decorators(node, source)
        name = self._find_name(node, source, ("property_identifier",))
        if not name:
            return None
        mods = self._collect_modifiers(node, source) + decorators
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

    def _handle_var_decl(self, node: Node, source: bytes, pf: ParsedFile):
        """Record top-level declarations and function-valued variables as Symbols."""
        is_top = self._func_depth == 0
        for child in node.children:
            if child.type != "variable_declarator":
                continue
            name = None
            value = None
            seen_eq = False
            for c in child.children:
                if c.type == "identifier" and name is None:
                    name = self._node_text(c, source).strip()
                elif c.type == "=":
                    seen_eq = True
                elif seen_eq and value is None:
                    value = c
            is_func_value = value is not None and value.type in (
                "arrow_function", "function_expression"
            )
            self._record_variable(name, child, source)
            if is_func_value and value is not None:
                if is_top and name:
                    pf.symbols.append(
                        Symbol(
                            name=name,
                            kind="function",
                            qualified_name=self._qualified_name(name),
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            column_start=child.start_point[1],
                            column_end=child.end_point[1],
                            arity=self._callable_arity(value),
                        )
                    )
                    self._callable_scope.append(name)
                self._func_depth += 1
                self._enter_function_scope(value, source)
                self._walk(value, source, pf)
                self._exit_function_scope()
                self._func_depth -= 1
                if is_top and name:
                    self._callable_scope.pop()
            else:
                if is_top and name:
                    pf.symbols.append(
                        Symbol(
                            name=name,
                            kind="variable",
                            qualified_name=self._qualified_name(name),
                            line_start=child.start_point[0] + 1,
                            line_end=child.end_point[0] + 1,
                            column_start=child.start_point[1],
                            column_end=child.end_point[1],
                        )
                    )
                if value is not None:
                    # Visit initializer nodes directly so their own expression type emits an edge.
                    self._visit(value, source, pf)

    def _parse_import(self, node: Node, source: bytes) -> Optional[Import]:
        spec = None
        alias = None
        for child in node.children:
            if child.type == "string":
                for gc in child.children:
                    if gc.type == "string_fragment":
                        spec = self._node_text(gc, source).strip()
                break
            if child.type == "import_clause":
                alias = self._import_clause_alias(child, source)
        if spec is None:
            return None
        resolved = resolve_relative_import(self._path, spec)
        imported_path = resolved if resolved else spec
        return Import(
            imported_path=imported_path,
            line=node.start_point[0] + 1,
            local_alias=alias,
        )

    # --- edges ---------------------------------------------------------

    def _heritage_target_name(self, node: Node, source: bytes) -> Optional[str]:
        """Return the bare identifier from a plain or generic heritage target."""
        if node.type in ("identifier", "type_identifier"):
            return self._node_text(node, source).strip()
        if node.type == "generic_type":
            inner = node.child_by_field_name("name")
            if inner is not None:
                return self._node_text(inner, source).strip()
        return None

    def _parse_heritage(self, node: Node, source: bytes, owner: str):
        """Return extends and implements Edges for TS and JS heritage shapes."""
        for child in node.children:
            if child.type == "class_heritage":
                found_wrapper = False
                for hchild in child.children:
                    if hchild.type == "extends_clause":
                        found_wrapper = True
                        self._emit_heritage_edges(hchild, "extends", owner, source, node)
                    elif hchild.type == "implements_clause":
                        found_wrapper = True
                        self._emit_heritage_edges(hchild, "implements", owner, source, node)
                if not found_wrapper:
                    # JS grammar: class_heritage's own children are directly
                    # 'extends' + identifier, with no wrapper node.
                    self._emit_heritage_edges(child, "extends", owner, source, node)
            elif child.type == "extends_type_clause":
                self._emit_heritage_edges(child, "extends", owner, source, node)

    def _emit_heritage_edges(self, clause: Node, kind: str, owner: str,
                             source: bytes, decl: Node) -> None:
        """One heritage edge per named target child of a heritage clause."""
        for c in clause.children:
            name = self._heritage_target_name(c, source)
            if name:
                self._pending_edges.append(
                    Edge(owner, kind, name, decl.start_point[0] + 1)
                )

    def _parse_call(self, node: Node, source: bytes) -> Optional[Edge]:
        if not node.children:
            return None
        # call_expression: callee is the first child. new_expression: first
        # child is the literal 'new' keyword, callee is the second.
        callee_idx = 1 if node.type == "new_expression" else 0
        if len(node.children) <= callee_idx:
            return None
        callee = node.children[callee_idx]
        target = self._extract_callee(callee, source)
        if not target:
            return None
        receiver = None
        if node.type == "call_expression" and callee.type == "member_expression":
            receiver = self._member_receiver_type(callee, source)
        return Edge(
            source_name=self._current_edge_owner(),
            kind="calls",
            target_name=target,
            line=node.start_point[0] + 1,
            receiver_type=receiver,
            call_arity=self._call_arity(node),
        )

    # --- receiver-type & arity signals -----------------------------------

    def _enter_function_scope(self, fn_node: Node, source: bytes) -> None:
        self._types.push()
        self._record_params(fn_node, source)

    def _exit_function_scope(self) -> None:
        self._types.pop()

    def _record_params(self, fn_node: Node, source: bytes) -> None:
        """Record annotated function parameters (TS) into the fresh scope."""
        params = fn_node.child_by_field_name("parameters")
        if params is None or params.type != "formal_parameters":
            return
        for child in params.children:
            if child.type not in ("required_parameter", "optional_parameter"):
                continue
            pattern = child.child_by_field_name("pattern")
            annotation = child.child_by_field_name("type")
            if pattern is None or pattern.type != "identifier" or annotation is None:
                continue
            name = self._node_text(pattern, source).strip()
            self._types.record(name, self._annotation_type_name(annotation, source))

    def _record_variable(self, name: Optional[str], declarator: Node, source: bytes) -> None:
        """Record a declarator's type: annotation first, else the constructed
        type of a ``new`` initializer."""
        if not name:
            return
        annotation = declarator.child_by_field_name("type")
        if annotation is not None:
            self._types.record(name, self._annotation_type_name(annotation, source))
            return
        value = declarator.child_by_field_name("value")
        if value is not None and value.type == "new_expression":
            self._types.record(name, self._new_callee_name(value, source))

    def _record_assignment(self, node: Node, source: bytes) -> None:
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or left.type != "identifier" or right is None:
            return
        if right.type == "new_expression":
            self._types.record(
                self._node_text(left, source).strip(),
                self._new_callee_name(right, source),
            )

    def _new_callee_name(self, new_node: Node, source: bytes) -> Optional[str]:
        ctor = new_node.child_by_field_name("constructor")
        if ctor is None:
            return None
        return self._extract_callee(ctor, source)

    def _annotation_type_name(self, annotation: Node, source: bytes) -> Optional[str]:
        """Bare type name of a type_annotation, or None for non-simple shapes
        (arrays, unions, ...) whose members would not resolve to the named
        type."""
        type_node = None
        for child in annotation.children:
            if child.is_named:
                type_node = child
                break
        if type_node is None:
            return None
        if type_node.type in ("nested_type_identifier", "generic_type"):
            type_node = type_node.child_by_field_name("name")
        if type_node is not None and type_node.type == "type_identifier":
            return self._node_text(type_node, source).strip()
        return None

    def _member_receiver_type(self, member: Node, source: bytes) -> Optional[str]:
        """Return a member-call receiver type for tracked, this, or static objects."""
        obj = member.child_by_field_name("object")
        if obj is None:
            return None
        if obj.type == "identifier":
            name = self._node_text(obj, source).strip()
            return self._types.resolve(name) or self._infer_receiver_type(name)
        if obj.type == "this":
            return self._scope[-1] if self._scope else None
        return None

    def _formal_arity(self, params: Node) -> Optional[int]:
        """Formal parameter count; None when any parameter is optional,
        defaulted, or rest (no single arity to match a call against)."""
        count = 0
        for child in params.children:
            if not child.is_named:
                continue
            if child.type in ("optional_parameter", "rest_pattern", "assignment_pattern"):
                return None
            if child.type == "required_parameter":
                if child.child_by_field_name("value") is not None:
                    return None
                pattern = child.child_by_field_name("pattern")
                if pattern is not None and pattern.type == "rest_pattern":
                    return None
            count += 1
        return count

    def _callable_arity(self, fn_node: Node) -> Optional[int]:
        """Arity of a function/method/function-expression definition node."""
        params = fn_node.child_by_field_name("parameters")
        if params is not None:
            return self._formal_arity(params)
        if fn_node.child_by_field_name("parameter") is not None:
            return 1  # arrow shorthand: `x => ...`
        return None

    def _call_arity(self, node: Node) -> Optional[int]:
        """Argument count at a call/new site; None when the argument list is
        absent or contains a spread."""
        args = node.child_by_field_name("arguments")
        if args is None:
            return None
        count = 0
        for child in args.children:
            if not child.is_named:
                continue
            if child.type == "spread_element":
                return None
            count += 1
        return count

    def _import_clause_alias(self, clause: Node, source: bytes) -> Optional[str]:
        """Return the sole local alias when an import has exactly one binding."""
        aliases: List[str] = []
        for child in clause.children:
            if child.type == "namespace_import":
                for c in child.children:
                    if c.type == "identifier":
                        aliases.append(self._node_text(c, source).strip())
            elif child.type == "named_imports":
                for spec in child.children:
                    if spec.type != "import_specifier":
                        continue
                    alias = spec.child_by_field_name("alias")
                    if alias is not None:
                        aliases.append(self._node_text(alias, source).strip())
        return aliases[0] if len(aliases) == 1 else None

    def _extract_callee(self, node: Node, source: bytes) -> Optional[str]:
        if node.type == "identifier":
            return self._node_text(node, source).strip()
        if node.type == "member_expression":
            for child in reversed(node.children):
                if child.type == "property_identifier":
                    return self._node_text(child, source).strip()
        return None

    def _parse_jsx_ref(self, node: Node, source: bytes) -> Optional[Edge]:
        """Return a component reference Edge for capitalized JSX tag names."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return None
        if name_node.type == "identifier":
            name: str | None = self._node_text(name_node, source).strip()
        elif name_node.type == "member_expression":
            # <UI.Card/> -> resolve to the property name "Card" (matches how
            # _extract_callee treats member_expression calls, and what the
            # resolver's bare-name index expects).
            name = self._extract_callee(name_node, source)
        elif name_node.type == "jsx_namespace_name":
            # <foo:Bar/> -> take the trailing identifier.
            name = None
            for child in reversed(name_node.children):
                if child.type == "identifier":
                    name = self._node_text(child, source).strip()
                    break
        else:
            return None
        if not name or not name[0].isupper():
            return None  # HTML host tag or unrecognizable name shape
        return Edge(
            source_name=self._current_edge_owner(),
            kind="references",
            target_name=name,
            line=node.start_point[0] + 1,
        )


class TypeScriptParser(_JSFamilyParser):
    """Handles .ts/.mts/.cts (TypeScript grammar) and .tsx (TSX grammar)."""

    language = "typescript"

    def _select_ts_parser(self, file_path: Path):
        grammar = "tsx" if file_path.suffix == ".tsx" else "typescript"
        return _get_ts_parser(grammar)


class JavaScriptParser(_JSFamilyParser):
    """Handles .js/.jsx/.mjs/.cjs. The JS grammar already understands JSX."""

    language = "javascript"

    def _select_ts_parser(self, file_path: Path):
        return _get_ts_parser("javascript")
