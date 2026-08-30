"""Tree-sitter TypeScript / TSX / JavaScript parser.

Covers `.ts`/`.mts`/`.cts` (TypeScript grammar), `.tsx` (TSX grammar), and
`.js`/`.jsx`/`.mjs`/`.cjs` (JavaScript grammar, which already understands JSX).
tree-sitter-typescript's two dialects and tree-sitter-javascript share almost
all node types (TS/TSX are supersets of the JS grammar), so a single traversal
(``_JSFamilyParser``) drives all three; only grammar *selection* differs, and
only TS adds a few extra declaration kinds (interface/type/enum).

Node-type reference:
  import_statement                              -> Import
  decorator (`@Controller('users')` etc.)       -> appended to the NEXT
    declaration's modifiers (full decorator text, e.g. "@Get(':id')");
    parameter-position decorators are dropped (parameters aren't tracked as
    symbols). NOT walked into as a normal call -- `Controller(...)` is a
    decorator invocation, not a call edge.
  class_declaration / abstract_class_declaration -> Symbol(class)
  interface_declaration                         -> Symbol(interface)
  type_alias_declaration                        -> Symbol(type)
  enum_declaration                              -> Symbol(enum)
  internal_module (namespace/module)            -> Symbol(namespace)
  function_declaration                          -> Symbol(function)
  method_definition                             -> Symbol(method)
  public_field_definition                       -> Symbol(property)
  lexical_declaration / variable_declaration     -> Symbol(variable), or
    (top-level only; block-scoped locals are skipped)  Symbol(function) if the
    initializer is an arrow_function/function_expression
  call_expression                               -> Edge(calls)
  class_heritage (extends_clause/implements_clause),
  extends_type_clause (interface extends)       -> Edge(extends|implements)
  jsx_opening_element / jsx_self_closing_element -> Edge(references)
    (capitalized name only; lowercase `<div>`/`<span>` are HTML host tags and
    skipped). `<UserCard/>` records a reference from the enclosing component to
    UserCard, so `cairn callers UserCard` finds JSX usage -- the largest source
    of inter-component relationships in React/RN code. `references` resolves
    through the same tiers as `calls` but is excluded from
    STRUCTURAL_EDGE_KINDS, so impact_analysis/trace_flow won't traverse it
    (a JSX ref isn't a transitive call).

Decorator modifiers are what route detection (src/parsers/routes.py) reads
to recognize NestJS `@Controller`/`@Get`/`@Post`/etc.

qualified_name: TS/JS has no canonical FQN the way Kotlin/Java packages do, so
each symbol is prefixed with the file's stem (basename minus extension) --
e.g. ``router.Router.handle``. `resolve_relative_import` returns the resolved
import's path with its extension stripped too, so its last path segment is
*identical* to the target file's stem; that is what lets the import-aware
resolver (src/graph/resolver.py) connect ``import { Router } from './router'``
to the `Router` class defined in `router.ts` via its dot-splitting tail-match.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List, Optional

from tree_sitter import Node

from ._registry import get_parser as _get_ts_parser
from .base import BaseParser, Edge, Import, ParsedFile, Symbol, TreeSitterParserBase

TS_JS_MODIFIERS = {
    "public", "private", "protected", "static", "readonly",
    "abstract", "async", "override", "declare",
}

# Node types that declare a named type containing members (class-like).
TYPE_DECL_NODES = {"class_declaration", "abstract_class_declaration", "interface_declaration"}

# Declaration nodes whose own children include their `decorator`(s). In
# tree-sitter-typescript, a class/method/function decorator is a CHILD of the
# decorated node, so these nodes collect their decorators directly via
# _own_decorators rather than through the pending queue.
DECL_NODES_WITH_OWN_DECORATORS = TYPE_DECL_NODES | {
    "method_definition",
    "function_declaration",
}

# Node types treated as top-level-or-nested variable declarations.
VAR_DECL_NODES = {"lexical_declaration", "variable_declaration"}

# Extensions tried when resolving a relative import to a file on disk.
_RESOLUTION_EXTS = ("", ".ts", ".tsx", ".d.ts", ".js", ".jsx")


def resolve_relative_import(importer: Path, spec: str) -> Optional[str]:
    """Resolve `./foo` or `../bar` relative to `importer`'s directory.

    Tries `spec` directly and `spec/index`, across `_RESOLUTION_EXTS`. Returns
    the resolved path with its extension stripped (so its last segment is a
    bare file stem -- see the module docstring for why that matters), or
    `None` if `spec` is a bare/package import (doesn't start with '.') or no
    candidate file exists on disk.
    """
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

        self._walk(tree.root_node, source, pf)
        pf.edges.extend(self._pending_edges)
        return pf

    # --- traversal -----------------------------------------------------

    def _walk(self, node: Node, source: bytes, pf: ParsedFile):
        for child in node.children:
            self._visit(child, source, pf)

    def _visit(self, node: Node, source: bytes, pf: ParsedFile):
        t = node.type

        if t == "import_statement":
            imp = self._parse_import(node, source)
            if imp:
                pf.imports.append(imp)
            return  # don't descend; nothing else useful inside

        if t == "decorator":
            # `@Controller('users')` etc. The full decorator text attaches to
            # its target declaration. In tree-sitter-typescript a decorator is
            # a CHILD of the node it decorates (class_declaration /
            # method_definition / function_declaration), so the declaration's
            # own _parse_* collects it via _own_decorators and we must NOT
            # also stash it on the pending queue (that would glom it onto the
            # next method/sibling).
            #
            # Decorators whose parent is NOT a declaration node (e.g. a
            # standalone export-list decorator) fall through to the pending
            # queue. Parameter-position decorators
            # (`getUser(@Param('id') id: ...)`) are dropped: parameters aren't
            # tracked as symbols.
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
                self._walk(node, source, pf)
                self._func_depth -= 1
                self._callable_scope.pop()
            return

        if t == "method_definition":
            sym = self._parse_function(node, source, "method")
            if sym:
                pf.symbols.append(sym)
                self._callable_scope.append(sym.name)
                self._func_depth += 1
                self._walk(node, source, pf)
                self._func_depth -= 1
                self._callable_scope.pop()
            return

        if t == "public_field_definition":
            sym = self._parse_field(node, source)
            if sym:
                pf.symbols.append(sym)
            # Descend into the initializer so call edges in field initializers
            # (e.g. `defaultRepo = createRepo()`) are emitted. function_declaration
            # and method_definition call _walk for the same reason; a field
            # initializer is not a new function scope, so _callable_scope /
            # _func_depth are left untouched. Without this, every call inside a
            # class-field initializer was silently dropped.
            self._walk(node, source, pf)
            return

        if t in VAR_DECL_NODES:
            self._handle_var_decl(node, source, pf)
            return

        if t in ("arrow_function", "function_expression"):
            # Anonymous function (e.g. a callback argument): not a named
            # symbol, but its body is no longer "top-level" for var purposes.
            self._func_depth += 1
            self._walk(node, source, pf)
            self._func_depth -= 1
            return

        if t in ("call_expression", "new_expression"):
            edge = self._parse_call(node, source)
            if edge:
                pf.edges.append(edge)
            self._walk(node, source, pf)
            return

        # JSX element usage: <UserCard/> or <UserCard>...</UserCard>. The
        # opening element is visited for both forms (a jsx_element's open_tag
        # child descends here); the closing element is skipped to avoid
        # duplicate edges. Lowercase-first names are HTML host tags, not
        # components.
        if t in ("jsx_opening_element", "jsx_self_closing_element"):
            edge = self._parse_jsx_ref(node, source)
            if edge:
                pf.edges.append(edge)
            self._walk(node, source, pf)
            return

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
        )

    def _parse_field(self, node: Node, source: bytes) -> Optional[Symbol]:
        decorators = self._take_pending_decorators()
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
        """lexical_declaration (const/let) / variable_declaration (var).

        Only recorded as a Symbol when at module top level (``_func_depth ==
        0``); block-scoped locals inside a function body are skipped. A
        const/let initialized to an arrow/function expression becomes a
        Symbol(kind="function") so calls inside it attribute correctly;
        otherwise it's a Symbol(kind="variable").
        """
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
                        )
                    )
                    self._callable_scope.append(name)
                self._func_depth += 1
                self._walk(value, source, pf)
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
                    # Visit the value node itself (not just its children) so a
                    # call_expression / new_expression / jsx_*_element that IS
                    # the initializer dispatches through _visit and emits its
                    # edge. _walk only visits children, which would skip the
                    # value node's own type and silently drop the edge (e.g.
                    # `const x = getUser()` lost the calls edge).
                    self._visit(value, source, pf)

    def _parse_import(self, node: Node, source: bytes) -> Optional[Import]:
        spec = None
        for child in node.children:
            if child.type == "string":
                for gc in child.children:
                    if gc.type == "string_fragment":
                        spec = self._node_text(gc, source).strip()
                break
        if spec is None:
            return None
        resolved = resolve_relative_import(self._path, spec)
        imported_path = resolved if resolved else spec
        return Import(imported_path=imported_path, line=node.start_point[0] + 1)

    # --- edges ---------------------------------------------------------

    def _parse_heritage(self, node: Node, source: bytes, owner: str):
        """extends/implements for class-likes (class_heritage) and interfaces
        (extends_type_clause -- interfaces can `extends` multiple others).

        The TS grammar wraps each clause in its own extends_clause/
        implements_clause node inside class_heritage; the plain JS grammar
        flattens `extends Base` directly into class_heritage's own children
        (no wrapper, and no `implements` at all). Handle both shapes.
        """
        for child in node.children:
            if child.type == "class_heritage":
                found_wrapper = False
                for hchild in child.children:
                    if hchild.type == "extends_clause":
                        found_wrapper = True
                        for c in hchild.children:
                            if c.type in ("identifier", "type_identifier"):
                                self._pending_edges.append(
                                    Edge(owner, "extends", self._node_text(c, source).strip(),
                                         node.start_point[0] + 1)
                                )
                    elif hchild.type == "implements_clause":
                        found_wrapper = True
                        for c in hchild.children:
                            if c.type in ("identifier", "type_identifier"):
                                self._pending_edges.append(
                                    Edge(owner, "implements", self._node_text(c, source).strip(),
                                         node.start_point[0] + 1)
                                )
                if not found_wrapper:
                    # JS grammar: class_heritage's own children are directly
                    # 'extends' + identifier, with no wrapper node.
                    for c in child.children:
                        if c.type in ("identifier", "type_identifier"):
                            self._pending_edges.append(
                                Edge(owner, "extends", self._node_text(c, source).strip(),
                                     node.start_point[0] + 1)
                            )
            elif child.type == "extends_type_clause":
                for c in child.children:
                    if c.type in ("identifier", "type_identifier"):
                        self._pending_edges.append(
                            Edge(owner, "extends", self._node_text(c, source).strip(),
                                 node.start_point[0] + 1)
                        )

    def _parse_call(self, node: Node, source: bytes) -> Optional[Edge]:
        if not node.children:
            return None
        # call_expression: callee is the first child. new_expression: first
        # child is the literal 'new' keyword, callee is the second.
        callee_idx = 1 if node.type == "new_expression" else 0
        if len(node.children) <= callee_idx:
            return None
        target = self._extract_callee(node.children[callee_idx], source)
        if not target:
            return None
        return Edge(
            source_name=self._current_edge_owner(),
            kind="calls",
            target_name=target,
            line=node.start_point[0] + 1,
        )

    def _extract_callee(self, node: Node, source: bytes) -> Optional[str]:
        if node.type == "identifier":
            return self._node_text(node, source).strip()
        if node.type == "member_expression":
            for child in reversed(node.children):
                if child.type == "property_identifier":
                    return self._node_text(child, source).strip()
        return None

    def _parse_jsx_ref(self, node: Node, source: bytes) -> Optional[Edge]:
        """jsx_opening_element / jsx_self_closing_element -> Edge(references).

        The component name lives in the ``name`` field (an ``identifier`` for
        ``<UserCard/>``, a ``member_expression`` for ``<UI.Card/>``, or a
        ``jsx_namespace_name`` for ``<foo:Bar/>``). Lowercase-first names are
        HTML host tags (``<div>``, ``<span>``) and are skipped -- only
        Capitalized names are React component references.
        """
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
