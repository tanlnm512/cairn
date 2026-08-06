"""Tree-sitter Objective-C parser.

Unlike Dart, tree-sitter-objc is a conventionally *nested* C-family grammar
(protocol/interface/implementation bodies properly contain their members), so
this parser uses the same recursive per-child `_visit` dispatch as
kotlin.py/java.py/typescript.py rather than Dart's flat sibling-list approach.

Node-type reference:
  preproc_include                          -> Import (#import/#include)
  protocol_declaration                     -> Symbol(protocol);
                                               protocol_reference_list -> Edge(extends)
  class_interface (@interface)             -> Symbol(class), or Symbol(category)
                                               for the `@interface Name (Category)`
                                               form; superclass -> Edge(extends);
                                               parameterized_arguments (adopted
                                               protocols) -> Edge(implements)
  class_implementation (@implementation)   -> Symbol(class) / Symbol(category_impl)
  property_declaration                     -> Symbol(property)
  method_declaration (stub) /
  method_definition (with body)            -> Symbol(method)
  message_expression ([obj sel:arg])       -> Edge(calls)  (target = first
                                               selector keyword only -- see below)
  call_expression (plain C call, e.g. NSLog(...)) -> Edge(calls)

Selector simplification: a multi-keyword Objective-C selector like
`doThing:withOption:` is recorded under just its FIRST keyword (`doThing`),
both at the method definition site and at each call site, so the two stay
consistent and name-resolvable. This loses the full selector signature but is
enough for "where is this method defined / who calls it" queries.

qualified_name follows the same file-stem-prefix convention used by
src/parsers/typescript.py and src/parsers/dart.py.

Known limitation -- header imports aren't indexed: `#import "Foo.h"` /
`#import <Framework/Foo.h>` point at `.h` files, and `.h` is deliberately NOT
in scanner.py's EXTENSION_MAP (headers are ambiguous across C/C++/Objective-C,
and C/C++ isn't implemented yet -- see the scanner.py comment). Since
`@interface` declarations conventionally live in the header and only
`@implementation` is in the indexed `.m`/`.mm` file, the import-aware resolver
tier will rarely have a matching indexed file to pin to for Objective-C; the
resolver's same-file and same-repo/global tiers still work normally.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import List, Optional

from tree_sitter import Node

from ._registry import get_parser as _get_ts_parser
from .base import BaseParser, Edge, Import, ParsedFile, Symbol, TreeSitterParserBase


def resolve_relative_objc_import(importer: Path, spec: str) -> Optional[str]:
    """Resolve a quoted `#import "Foo.h"` to an absolute, extension-stripped
    path if the target file exists on disk relative to the importer's
    directory. Angle-bracket framework imports (`<Foundation/Foundation.h>`)
    are not attempted here -- callers pass only the quoted form."""
    base = (importer.parent / spec).resolve()
    if not base.is_file():
        return None
    return str(base.parent / base.stem) if base.suffix else str(base)


class ObjCParser(BaseParser, TreeSitterParserBase):
    language = "objc"

    def __init__(self):
        super().__init__()
        self._parser = _get_ts_parser("objc")
        self._file_stem = None
        self._path = None
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
        # Parsers are cached singletons reused across files, so reset all
        # per-file accumulators here -- otherwise scope/edges from file N
        # bleed into file N+1's ParsedFile.
        self._pending_edges: List[Edge] = []
        self._scope = []
        self._callable_scope = []

        self._walk(tree.root_node, source, pf)
        pf.edges.extend(self._pending_edges)
        return pf

    # --- traversal -------------------------------------------------------

    def _walk(self, node: Node, source: bytes, pf: ParsedFile):
        for child in node.children:
            self._visit(child, source, pf)

    def _visit(self, node: Node, source: bytes, pf: ParsedFile):
        t = node.type

        if t == "preproc_include":
            imp = self._parse_import(node, source)
            if imp:
                pf.imports.append(imp)
            return

        if t == "protocol_declaration":
            sym = self._parse_protocol(node, source)
            if sym:
                pf.symbols.append(sym)
                self._scope.append(sym.name)
                self._walk(node, source, pf)
                self._scope.pop()
            else:
                self._walk(node, source, pf)
            return

        if t == "class_interface":
            sym, scope_name = self._parse_class_interface(node, source)
            if sym:
                pf.symbols.append(sym)
            if scope_name:
                self._scope.append(scope_name)
                self._walk(node, source, pf)
                self._scope.pop()
            else:
                self._walk(node, source, pf)
            return

        if t == "class_implementation":
            sym, scope_name = self._parse_class_implementation(node, source)
            if sym:
                pf.symbols.append(sym)
            if scope_name:
                self._scope.append(scope_name)
                self._walk(node, source, pf)
                self._scope.pop()
            else:
                self._walk(node, source, pf)
            return

        if t == "property_declaration":
            sym = self._parse_property(node, source)
            if sym:
                pf.symbols.append(sym)
            return

        if t in ("method_declaration", "method_definition"):
            sym = self._parse_method(node, source)
            if sym:
                pf.symbols.append(sym)
                self._callable_scope.append(sym.name)
                self._walk(node, source, pf)
                self._callable_scope.pop()
            else:
                self._walk(node, source, pf)
            return

        if t == "message_expression":
            edge = self._parse_message(node, source)
            if edge:
                pf.edges.append(edge)
            self._walk(node, source, pf)
            return

        if t == "call_expression":
            edge = self._parse_call(node, source)
            if edge:
                pf.edges.append(edge)
            self._walk(node, source, pf)
            return

        self._walk(node, source, pf)

    # --- helpers -----------------------------------------------------------

    def _qualified_name(self, name: str) -> str:
        """ObjC uses file-stem prefix like TypeScript."""
        return ".".join([self._file_stem] + self._scope + [name])

    def _iter_adopted_protocol_names(self, params_node: Node, source: bytes) -> List[str]:
        names = []
        for c in params_node.children:
            if c.type == "type_name":
                tid = self._child_of_type(c, ("type_identifier",))
                if tid is not None:
                    names.append(self._node_text(tid, source).strip())
        return names

    # --- declarations --------------------------------------------------

    def _parse_protocol(self, node: Node, source: bytes) -> Optional[Symbol]:
        name = self._find_name(node, source)
        if not name:
            return None
        sym = Symbol(
            name=name, kind="protocol", qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1, line_end=node.end_point[0] + 1,
            column_start=node.start_point[1], column_end=node.end_point[1],
        )
        ref_list = self._child_of_type(node, ("protocol_reference_list",))
        if ref_list is not None:
            for c in ref_list.children:
                if c.type == "identifier":
                    self._pending_edges.append(
                        Edge(name, "extends", self._node_text(c, source).strip(),
                             node.start_point[0] + 1)
                    )
        return sym

    def _parse_class_interface(self, node: Node, source: bytes):
        is_category = any(c.type == "(" for c in node.children)
        idents = [c for c in node.children if c.type == "identifier"]
        if not idents:
            return None, None
        class_name = self._node_text(idents[0], source).strip()

        if is_category and len(idents) > 1:
            cat_name = self._node_text(idents[1], source).strip()
            sym_name = f"{class_name}+{cat_name}"
            kind = "category"
        else:
            sym_name = class_name
            kind = "class"
            # superclass: ':' identifier
            prev_colon = False
            for c in node.children:
                if c.type == ":":
                    prev_colon = True
                    continue
                if prev_colon and c.type == "identifier":
                    self._pending_edges.append(
                        Edge(class_name, "extends", self._node_text(c, source).strip(),
                             node.start_point[0] + 1)
                    )
                    prev_colon = False
            # adopted protocols: <Proto1, Proto2>
            params = self._child_of_type(node, ("parameterized_arguments",))
            if params is not None:
                for pname in self._iter_adopted_protocol_names(params, source):
                    self._pending_edges.append(
                        Edge(class_name, "implements", pname, node.start_point[0] + 1)
                    )

        sym = Symbol(
            name=sym_name, kind=kind, qualified_name=self._qualified_name(sym_name),
            line_start=node.start_point[0] + 1, line_end=node.end_point[0] + 1,
            column_start=node.start_point[1], column_end=node.end_point[1],
        )
        return sym, class_name

    def _parse_class_implementation(self, node: Node, source: bytes):
        is_category = any(c.type == "(" for c in node.children)
        idents = [c for c in node.children if c.type == "identifier"]
        if not idents:
            return None, None
        class_name = self._node_text(idents[0], source).strip()
        if is_category and len(idents) > 1:
            cat_name = self._node_text(idents[1], source).strip()
            sym_name = f"{class_name}+{cat_name}"
            kind = "category_impl"
        else:
            sym_name = class_name
            kind = "class"
        sym = Symbol(
            name=sym_name, kind=kind, qualified_name=self._qualified_name(sym_name),
            line_start=node.start_point[0] + 1, line_end=node.end_point[0] + 1,
            column_start=node.start_point[1], column_end=node.end_point[1],
        )
        return sym, class_name

    def _parse_property(self, node: Node, source: bytes) -> Optional[Symbol]:
        struct_decl = self._child_of_type(node, ("struct_declaration",))
        if struct_decl is None:
            return None
        name = None
        for c in struct_decl.children:
            if c.type == "struct_declarator":
                ptr = self._child_of_type(c, ("pointer_declarator",))
                name = self._find_name(ptr if ptr is not None else c, source)
            elif c.type == "identifier" and name is None:
                name = self._node_text(c, source).strip()
        if not name:
            return None
        return Symbol(
            name=name, kind="property", qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1, line_end=node.end_point[0] + 1,
            column_start=node.start_point[1], column_end=node.end_point[1],
        )

    def _parse_method(self, node: Node, source: bytes) -> Optional[Symbol]:
        name = self._find_name(node, source)
        if not name:
            return None
        mods = []
        for c in node.children:
            if c.type == "+":
                mods.append("class_method")
                break
            if c.type == "-":
                mods.append("instance_method")
                break
        return Symbol(
            name=name, kind="method", qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1, line_end=node.end_point[0] + 1,
            column_start=node.start_point[1], column_end=node.end_point[1],
            modifiers=mods,
        )

    def _parse_import(self, node: Node, source: bytes) -> Optional[Import]:
        for c in node.children:
            if c.type == "string_literal":
                content = self._child_of_type(c, ("string_content",))
                text = (
                    self._node_text(content, source).strip()
                    if content is not None
                    else self._node_text(c, source).strip().strip('"')
                )
                if not text:
                    return None
                resolved = resolve_relative_objc_import(self._path, text)
                imported_path = resolved if resolved else text
                return Import(imported_path=imported_path, line=node.start_point[0] + 1)
            if c.type == "system_lib_string":
                text = self._node_text(c, source).strip()
                if text.startswith("<") and text.endswith(">"):
                    text = text[1:-1]
                return Import(imported_path=text, line=node.start_point[0] + 1)
        return None

    # --- edges -----------------------------------------------------------

    def _parse_message(self, node: Node, source: bytes) -> Optional[Edge]:
        children = node.children
        if len(children) < 3:
            return None
        # children[0] = '[', children[1] = receiver (one node, whatever its
        # own internal shape). The selector run starts at children[2]; we
        # only need its first keyword (see module docstring).
        for c in children[2:]:
            if c.type == "identifier":
                return Edge(
                    self._current_edge_owner(), "calls",
                    self._node_text(c, source).strip(), node.start_point[0] + 1,
                )
        return None

    def _parse_call(self, node: Node, source: bytes) -> Optional[Edge]:
        for c in node.children:
            if c.type == "identifier":
                return Edge(
                    self._current_edge_owner(), "calls",
                    self._node_text(c, source).strip(), node.start_point[0] + 1,
                )
        return None
