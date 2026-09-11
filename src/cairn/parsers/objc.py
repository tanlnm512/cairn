"""Tree-sitter Objective-C parser.

tree-sitter-objc is a conventionally *nested* C-family grammar
(protocol/interface/implementation bodies properly contain their members), so
this parser uses the recursive per-child `_visit` dispatch.

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
                                               selector keyword only -- see
                                               below); receiver typed via
                                               the scope-ordered tracker or
                                               the capitalized class-name
                                               heuristic; call_arity counts
                                               selector arguments
  call_expression (plain C call, e.g. NSLog(...)) -> Edge(calls) with
                                               call_arity from its
                                               argument_list
  method_declaration / method_definition   -> Symbol(method) with arity =
                                               method_parameter count
                                               (variadic `...` -> None)

Selector simplification: a multi-keyword Objective-C selector like
`doThing:withOption:` is recorded under just its FIRST keyword (`doThing`),
both at the method definition site and at each call site, so the two stay
consistent and name-resolvable.

Known limitation -- header imports aren't indexed: `#import "Foo.h"` /
`#import <Framework/Foo.h>` point at `.h` files, and `.h` is deliberately NOT
in scanner.py's EXTENSION_MAP. The resolver's same-file and same-repo/global
tiers still work normally.
"""
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
        # Scope-ordered var->type map for receiver inference (params + locals).
        self._types = ScopeTypeTracker()

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
        # per-file accumulators here.
        self._pending_edges = []
        self._scope = []
        self._callable_scope = []
        self._types.reset()

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
                self._types.push()
                self._record_method_params(node, source)
                self._walk(node, source, pf)
                self._types.pop()
                self._callable_scope.pop()
            else:
                self._walk(node, source, pf)
            return

        if t == "compound_statement":
            self._types.push()
            self._walk(node, source, pf)
            self._types.pop()
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

        if t == "declaration":
            self._record_declaration(node, source)

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
            arity=self._method_arity(node, source),
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
        keywords = node.children_by_field_name("method")
        if not keywords:
            return None
        receiver = node.child_by_field_name("receiver")
        args = [
            c for c in node.named_children if c != receiver and c not in keywords
        ]
        return Edge(
            self._current_edge_owner(), "calls",
            self._node_text(keywords[0], source).strip(), node.start_point[0] + 1,
            receiver_type=self._receiver_type(receiver, source),
            call_arity=len(args),
        )

    def _parse_call(self, node: Node, source: bytes) -> Optional[Edge]:
        for c in node.children:
            if c.type == "identifier":
                return Edge(
                    self._current_edge_owner(), "calls",
                    self._node_text(c, source).strip(), node.start_point[0] + 1,
                    call_arity=self._call_arity(node),
                )
        return None

    # -------------------------------------------------- arity + type tracking

    def _method_arity(self, node: Node, source: bytes) -> Optional[int]:
        """method_parameter count; None when variadic (`...`)."""
        if any(self._node_text(c, source) == "..." for c in node.children):
            return None
        return sum(1 for c in node.children if c.type == "method_parameter")

    def _call_arity(self, node: Node) -> Optional[int]:
        """Named-child count of the ``argument_list``."""
        args = node.child_by_field_name("arguments")
        if args is None:
            return None
        return sum(1 for c in args.children if c.is_named)

    def _receiver_type(self, receiver: Optional[Node], source: bytes) -> Optional[str]:
        """Type of a message receiver, or None when not inferable."""
        if receiver is None or receiver.type != "identifier":
            return None
        name = self._node_text(receiver, source).strip()
        if name == "self":
            return self._scope[-1] if self._scope else None
        if name == "super":
            return None  # the superclass is declared in the header, not here
        tracked = self._types.resolve(name)
        if tracked is not None:
            return tracked
        return self._infer_receiver_type(name)

    def _record_method_params(self, node: Node, source: bytes) -> None:
        """Record each typed method_parameter into the tracker's method scope."""
        for c in node.children:
            if c.type != "method_parameter":
                continue
            param_type = self._param_type_name(c, source)
            name = self._find_name(c, source)
            if param_type and name:
                self._types.record(name, param_type)

    def _param_type_name(self, param: Node, source: bytes) -> Optional[str]:
        """Bare class-like type of a method_parameter (``method_type ->
        type_name -> type_identifier``); primitives and protocol-qualified
        shapes stay None."""
        mt = self._child_of_type(param, ("method_type",))
        tn = self._child_of_type(mt, ("type_name",)) if mt is not None else None
        tid = self._child_of_type(tn, ("type_identifier",)) if tn is not None else None
        return self._node_text(tid, source).strip() if tid is not None else None

    def _record_declaration(self, node: Node, source: bytes) -> None:
        """Record each typed local declarator (``NSString *s = x, *t;``)."""
        type_node = node.child_by_field_name("type")
        if type_node is None or type_node.type != "type_identifier":
            return
        type_name = self._node_text(type_node, source).strip()
        for child in node.children:
            if child.is_named and child != type_node:
                name = self._declared_var_name(child, source)
                if name:
                    self._types.record(name, type_name)

    def _declared_var_name(self, node: Optional[Node], source: bytes) -> Optional[str]:
        """Identifier bound by a declarator chain (init/pointer/array)."""
        while node is not None:
            if node.type == "identifier":
                return self._node_text(node, source).strip()
            if node.type == "function_declarator":
                return None  # a function name, not a variable
            inner = node.child_by_field_name("declarator")
            if inner is None:
                inner = self._child_of_type(node, ("identifier",))
            node = inner
        return None
