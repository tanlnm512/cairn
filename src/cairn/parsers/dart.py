"""Tree-sitter Dart parser (covers Flutter widgets/mixins/extensions too).

Unlike Kotlin/Java/TypeScript, tree-sitter-dart represents two things as FLAT
SIBLING SEQUENCES rather than proper parent/child nesting:

  1. A function/method's signature and its body are siblings, not parent and
     child: `method_signature` is immediately followed by a sibling
     `function_body` in the same children list (e.g. inside `class_body`).
  2. Postfix call chains (`obj.method(args)`) are a flat run of siblings too:
     `identifier 'obj'`, `selector '.method'`, `selector '(args)'` all sit next
     to each other in the same parent's children, rather than one nesting
     inside a `call_expression` node.

So this parser processes each container's children as an indexed list
(``_process_siblings``) with lookahead, instead of the simple recursive
per-child dispatch the other parsers use.

Node-type reference:
  import_or_export                      -> Import
  class_definition / mixin_declaration   -> Symbol(class|mixin)
  enum_declaration                       -> Symbol(enum)
  extension_declaration                  -> Symbol(extension)
  function_signature / constructor_signature / method_signature
                                          -> Symbol(function|method|constructor)
  declaration (field, or abstract method stub) -> Symbol(property) or Symbol(method)
  identifier/this + selector(s) chain    -> Edge(calls)
  superclass (extends + with)            -> Edge(extends|with)
  interfaces (implements)                -> Edge(implements)

qualified_name follows the same file-stem-prefix convention as
src/parsers/typescript.py; imports of relative Dart specs (`./foo.dart`) are
resolved and extension-stripped for the same reason. `package:` imports are
left as opaque bare specs (external).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List, Optional

from tree_sitter import Node

from ._registry import get_parser as _get_ts_parser
from .base import BaseParser, Edge, Import, ParsedFile, Symbol, TreeSitterParserBase

TYPE_DECL_NODES = {"class_definition", "mixin_declaration"}
SIGNATURE_NODES = {"function_signature", "constructor_signature"}


def resolve_relative_dart_import(importer: Path, spec: str) -> Optional[str]:
    """Resolve a relative Dart import ('./foo.dart', '../bar.dart') to an
    absolute, extension-stripped path if the target file exists on disk.

    Bare/package specs (`package:foo/bar.dart`, `dart:core`) return `None` --
    callers store them unchanged and the resolver leaves them unresolved
    (external), same convention as TypeScript's package imports.
    """
    if not spec.startswith("."):
        return None
    base = (importer.parent / spec).resolve()
    if base.suffix == ".dart":
        base = base.with_suffix("")
    if Path(str(base) + ".dart").is_file():
        return str(base)
    return None


class DartParser(BaseParser, TreeSitterParserBase):
    language = "dart"

    def __init__(self):
        super().__init__()
        self._parser = _get_ts_parser("dart")
        self._file_stem = None
        self._path = None
        self._func_depth = 0
        self._pending_edges: List[Edge] = []

    def parse(self, path: str) -> ParsedFile:
        file_path = Path(path)
        source = file_path.read_bytes()
        tree = self._parser.parse(source)

        pf = ParsedFile(
            path=path,
            language=self.language,
            hash=hashlib.sha256(source).hexdigest(),
            line_count=source.count(b"\n") + 1,
        )

        self._path = file_path
        self._file_stem = file_path.stem
        self._func_depth = 0
        # Parsers are cached singletons reused across files, so reset all
        # per-file accumulators here -- otherwise scope/edges from file N
        # bleed into file N+1's ParsedFile.
        self._pending_edges = []
        self._scope = []
        self._callable_scope = []

        self._process_siblings(tree.root_node.children, source, pf)
        pf.edges.extend(self._pending_edges)
        return pf

    # --- core traversal: flat sibling lists with lookahead -----------------

    def _process_siblings(self, children: List[Node], source: bytes, pf: ParsedFile):
        self._scan_postfix_chain(children, source, pf)

        i = 0
        n = len(children)
        while i < n:
            node = children[i]
            t = node.type
            consumed = 1

            if t == "import_or_export":
                imp = self._parse_import(node, source)
                if imp:
                    pf.imports.append(imp)

            elif t in TYPE_DECL_NODES:
                sym = self._parse_type_decl(node, source)
                if sym:
                    pf.symbols.append(sym)
                    self._scope.append(sym.name)
                body = self._child_of_type(node, ("class_body",))
                if body is not None:
                    self._process_siblings(body.children, source, pf)
                if sym:
                    self._scope.pop()

            elif t == "enum_declaration":
                sym = self._parse_simple_decl(node, source, "enum")
                if sym:
                    pf.symbols.append(sym)

            elif t == "extension_declaration":
                sym = self._parse_simple_decl(node, source, "extension")
                if sym:
                    pf.symbols.append(sym)
                    self._scope.append(sym.name)
                body = self._child_of_type(node, ("extension_body",))
                if body is not None:
                    self._process_siblings(body.children, source, pf)
                if sym:
                    self._scope.pop()

            elif t in SIGNATURE_NODES:
                kind = "constructor" if t == "constructor_signature" else (
                    "method" if self._scope else "function"
                )
                sym = self._parse_function_sig(node, source, kind)
                body, extra = self._paired_body(children, i, n)
                consumed += extra
                self._emit_with_body(sym, body, source, pf)

            elif t == "method_signature":
                inner = self._child_of_type(
                    node, ("function_signature", "constructor_signature",
                           "getter_signature", "setter_signature")
                )
                sym = None
                if inner is not None:
                    kind = "constructor" if inner.type == "constructor_signature" else "method"
                    sym = self._parse_function_sig(inner, source, kind)
                body, extra = self._paired_body(children, i, n)
                consumed += extra
                self._emit_with_body(sym, body, source, pf)

            elif t == "declaration":
                inner = self._child_of_type(node, ("function_signature", "constructor_signature"))
                if inner is not None:
                    kind = "constructor" if inner.type == "constructor_signature" else (
                        "method" if self._scope else "function"
                    )
                    sym = self._parse_function_sig(inner, source, kind)
                    if sym:
                        pf.symbols.append(sym)
                else:
                    for fsym in self._parse_field_declaration(node, source):
                        pf.symbols.append(fsym)

            else:
                # Any other container (block, if/for/while bodies, argument
                # lists, parenthesized expressions, ...): recurse into its own
                # children as a fresh sibling list.
                self._process_siblings(node.children, source, pf)

            i += consumed

    def _paired_body(self, children: List[Node], i: int, n: int):
        """A signature's implementation is its NEXT sibling `function_body`
        (absent for interface/protocol stubs, where the next sibling is `;`).
        Returns (body_node_or_None, extra_children_consumed)."""
        if i + 1 < n and children[i + 1].type == "function_body":
            return children[i + 1], 1
        return None, 0

    def _emit_with_body(self, sym: Optional[Symbol], body: Optional[Node], source: bytes, pf: ParsedFile):
        if sym:
            pf.symbols.append(sym)
            self._callable_scope.append(sym.name)
        self._func_depth += 1
        if body is not None:
            self._process_siblings(body.children, source, pf)
        self._func_depth -= 1
        if sym:
            self._callable_scope.pop()

    # --- call-chain scanning (flat identifier + selector* siblings) --------

    def _scan_postfix_chain(self, children: List[Node], source: bytes, pf: ParsedFile):
        n = len(children)
        i = 0
        while i < n:
            node = children[i]
            if node.type in ("identifier", "this"):
                base_name = self._node_text(node, source).strip()
                line = node.start_point[0] + 1
                last_property = None
                j = i + 1
                while j < n and children[j].type == "selector":
                    sel = children[j]
                    prop = self._selector_property_name(sel, source)
                    if prop is not None:
                        last_property = prop
                    if self._selector_is_call(sel):
                        target = last_property if last_property is not None else base_name
                        pf.edges.append(Edge(self._current_edge_owner(), "calls", target, line))
                    j += 1
                i = j if j > i + 1 else i + 1
            else:
                i += 1

    def _selector_property_name(self, sel_node: Node, source: bytes) -> Optional[str]:
        for c in sel_node.children:
            if c.type == "unconditional_assignable_selector":
                for gc in c.children:
                    if gc.type == "identifier":
                        return self._node_text(gc, source).strip()
            elif c.type == "identifier":
                return self._node_text(c, source).strip()
        return None

    def _selector_is_call(self, sel_node: Node) -> bool:
        return any(c.type == "argument_part" for c in sel_node.children)

    # --- helpers -------------------------------------------------------

    def _find_descendant(self, node: Node, type_name: str) -> Optional[Node]:
        for c in node.children:
            if c.type == type_name:
                return c
            found = self._find_descendant(c, type_name)
            if found is not None:
                return found
        return None

    def _qualified_name(self, name: str) -> str:
        """Dart uses file-stem prefix like TypeScript."""
        return ".".join([self._file_stem] + self._scope + [name])

    # --- declarations --------------------------------------------------

    def _parse_type_decl(self, node: Node, source: bytes) -> Optional[Symbol]:
        name = self._find_name(node, source)
        if not name:
            return None
        kind = "mixin" if node.type == "mixin_declaration" else "class"
        mods = ["abstract"] if self._child_of_type(node, ("abstract",)) is not None else []
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

    def _parse_heritage(self, node: Node, source: bytes, owner: str):
        superclass = self._child_of_type(node, ("superclass",))
        if superclass is not None:
            ext_target = None
            for c in superclass.children:
                if c.type == "type_identifier" and ext_target is None:
                    ext_target = self._node_text(c, source).strip()
                elif c.type == "mixins":
                    for m in c.children:
                        if m.type == "type_identifier":
                            self._pending_edges.append(
                                Edge(owner, "with", self._node_text(m, source).strip(),
                                     node.start_point[0] + 1)
                            )
            if ext_target:
                self._pending_edges.append(
                    Edge(owner, "extends", ext_target, node.start_point[0] + 1)
                )
        interfaces = self._child_of_type(node, ("interfaces",))
        if interfaces is not None:
            for c in interfaces.children:
                if c.type == "type_identifier":
                    self._pending_edges.append(
                        Edge(owner, "implements", self._node_text(c, source).strip(),
                             node.start_point[0] + 1)
                    )

    def _parse_simple_decl(self, node: Node, source: bytes, kind: str) -> Optional[Symbol]:
        name = self._find_name(node, source)
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

    def _parse_function_sig(self, node: Node, source: bytes, kind: str) -> Optional[Symbol]:
        name = self._find_name(node, source)
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

    def _parse_field_declaration(self, node: Node, source: bytes) -> List[Symbol]:
        syms: List[Symbol] = []
        if self._func_depth != 0:
            return syms  # defensive; local vars use local_variable_declaration, not this
        id_list = self._child_of_type(node, ("initialized_identifier_list",))
        if id_list is None:
            return syms
        kind = "property" if self._scope else "variable"
        for child in id_list.children:
            if child.type == "initialized_identifier":
                name = self._find_name(child, source)
                if name:
                    syms.append(
                        Symbol(
                            name=name,
                            kind=kind,
                            qualified_name=self._qualified_name(name),
                            line_start=node.start_point[0] + 1,
                            line_end=node.end_point[0] + 1,
                            column_start=node.start_point[1],
                            column_end=node.end_point[1],
                        )
                    )
        return syms

    def _parse_import(self, node: Node, source: bytes) -> Optional[Import]:
        uri_node = self._find_descendant(node, "uri")
        if uri_node is None:
            return None
        text = self._node_text(uri_node, source).strip()
        if len(text) >= 2 and text[0] in ("'", '"') and text[-1] in ("'", '"'):
            text = text[1:-1]
        if not text:
            return None
        resolved = resolve_relative_dart_import(self._path, text)
        imported_path = resolved if resolved else text
        return Import(imported_path=imported_path, line=node.start_point[0] + 1)
