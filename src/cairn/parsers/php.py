"""Tree-sitter PHP parser.

Extracts classes, interfaces, traits, enums, enum cases, functions, methods,
properties, call edges, inheritance edges, and imports (require/include + use)
into the shared ParsedFile model.

Uses the ``php_only`` grammar (registered under the ``"php"`` key in
``_registry._SPECIAL_LOADERS``), which yields pure PHP AST nodes without the
HTML wrapper that the full ``language_php()`` grammar would add around inline
PHP blocks.

Node-type reference (tree-sitter-php, php_only grammar):

- ``class_declaration`` -> Symbol(class). ``base_clause`` -> Edge(extends);
  ``class_interface_clause`` -> Edge(implements).
- ``interface_declaration`` -> Symbol(interface). ``base_clause`` (extends
  on interfaces) -> Edge(extends).
- ``trait_declaration`` -> Symbol(trait).
- ``enum_declaration`` (PHP 8.1+) -> Symbol(enum). ``enum_case`` children ->
  Symbol(enum_case).
- ``anonymous_class`` (``new class { ... }``) -> Symbol(class) synthesized
  with a generated name, so its methods don't leak into the enclosing scope.
- ``function_definition`` -> Symbol(function).
- ``method_declaration`` -> Symbol(method). Methods inside a class/interface/
  trait/enum body are classified as ``method``; the FQN is scope-qualified via
  ``_scope``. ``property_promotion_parameter`` children (PHP 8.0 constructor
  property promotion) -> Symbol(property).
- ``property_declaration`` -> Symbol(property) for each ``property_element``.
- ``function_call_expression`` (name/qualified-name call) -> Edge(calls).
- ``member_call_expression`` (``$obj->method()``) and
  ``nullsafe_member_call_expression`` (``$obj?->method()``) -> Edge(calls).
- ``scoped_call_expression`` (``Class::method()`` / ``$inst::method()`` /
  ``Foo\\Bar::baz()``) -> Edge(calls), target is the trailing ``name`` child.
- ``require_*_expression`` / ``include_*_expression`` -> Import. The argument
  is often a ``binary_expression`` (``__DIR__ . "/path"``); captured verbatim.
- ``namespace_use_declaration`` (``use``) -> Import per clause, including
  grouped ``use Foo\\{A, B};``. ``namespace_definition`` (bracketed form)
  scopes declarations via ``_scope``; unbracketed form is ignored (it applies
  file-wide and cairn doesn't model namespaces in FQNs beyond ``_scope``).

PHP name nodes are plain ``name`` children (not ``identifier``). Leading
backslashes on fully-qualified names (``\array_map``) are stripped, and
multi-segment qualified names in call targets/receivers
(``App\\Utils\\sanitize``, ``App\\Models\\User::find``) are reduced to their
last ``\``-segment so edges can resolve against bare symbol names.
"""
from __future__ import annotations

import hashlib
from typing import List, Optional

from tree_sitter import Node

from ._registry import get_parser as _get_ts_parser
from .base import BaseParser, Edge, Import, ParsedFile, Symbol, TreeSitterParserBase

# Call node types that produce a `calls` edge.
_CALL_NODES = frozenset(
    {
        "function_call_expression",
        "member_call_expression",
        "nullsafe_member_call_expression",  # $obj?->method()
        "scoped_call_expression",           # Class::method() / $inst::method()
        "object_creation_expression",       # new Foo() -- constructor call
    }
)
# Declaration node types that introduce a named type (class/interface/trait/enum).
_TYPE_DECL_NODES = frozenset(
    {
        "class_declaration",
        "interface_declaration",
        "trait_declaration",
        "enum_declaration",
        "anonymous_class",
    }
)
# Require/include expression node types -> Import.
_REQUIRE_NODES = frozenset(
    {
        "require_expression",
        "require_once_expression",
        "include_expression",
        "include_once_expression",
    }
)


class PhpParser(BaseParser, TreeSitterParserBase):
    language = "php"

    def __init__(self):
        super().__init__()
        self._parser = _get_ts_parser("php")
        self._pending_edges: List[Edge] = []
        self._anon_counter = 0

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
        # Parsers are cached singletons reused across files, so reset all
        # per-file accumulators here.
        self._pending_edges = []
        self._scope = []
        self._callable_scope = []
        self._anon_counter = 0
        self._walk(tree.root_node, source, pf)
        pf.edges.extend(self._pending_edges)
        return pf

    def _walk(self, node: Node, source: bytes, pf: ParsedFile):
        for child in node.children:
            self._visit(child, source, pf)

    def _visit(self, node: Node, source: bytes, pf: ParsedFile):
        t = node.type

        # Namespace: bracketed form scopes its body; unbracketed form applies
        # file-wide and isn't modeled in _scope (cairn FQNs stop at _scope).
        if t == "namespace_definition":
            if self._child_of_type(node, ("compound_statement",)) is not None:
                self._walk(node, source, pf)
            return

        # Imports: use statements and require/include expressions.
        if t == "namespace_use_declaration":
            for imp in self._parse_use_imports(node, source):
                pf.imports.append(imp)
            return
        if t in _REQUIRE_NODES:
            req = self._parse_require_import(node, source)
            if req:
                pf.imports.append(req)
            return

        # Type declarations: class / interface / trait / enum / anonymous_class.
        if t in _TYPE_DECL_NODES:
            sym = self._parse_type_decl(node, source)
            if sym:
                pf.symbols.append(sym)
                self._scope.append(sym.name)
                self._walk(node, source, pf)
                self._scope.pop()
            return

        # Free-standing function definition.
        if t == "function_definition":
            sym = self._parse_function(node, source)
            if sym:
                pf.symbols.append(sym)
                self._callable_scope.append(sym.name)
                self._walk(node, source, pf)
                self._callable_scope.pop()
            return

        # Methods inside a class/interface/trait/enum body.
        if t == "method_declaration":
            sym = self._parse_method(node, source, pf)
            if sym:
                pf.symbols.append(sym)
                self._callable_scope.append(sym.name)
                self._walk(node, source, pf)
                self._callable_scope.pop()
            return

        # Enum cases: `case Hearts;` inside an enum body.
        if t == "enum_case":
            sym = self._parse_enum_case(node, source)
            if sym:
                pf.symbols.append(sym)
            return

        if t == "property_declaration":
            for sym in self._parse_property(node, source):
                pf.symbols.append(sym)
            self._walk(node, source, pf)
            return

        if t in _CALL_NODES:
            edge = self._parse_call(node, source)
            if edge:
                pf.edges.append(edge)
            self._walk(node, source, pf)
            return

        self._walk(node, source, pf)

    # -------------------------------------------------------- declaration parse

    def _parse_type_decl(self, node: Node, source: bytes) -> Optional[Symbol]:
        """class/interface/trait/enum/anonymous_class declaration -> Symbol."""
        kind, name = self._type_decl_kind_name(node, source)
        if not name:
            return None
        sym = Symbol(
            name=name,
            kind=kind,
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
        )
        # Inheritance / implementation edges (deferred; source_name uses the
        # bare name so the builder's same-file lookup resolves source_id).
        for clause_kind, clause_node in self._heritage_clauses(node):
            for target in self._clause_names(clause_node, source):
                self._pending_edges.append(
                    Edge(name, clause_kind, target, node.start_point[0] + 1)
                )
        return sym

    def _type_decl_kind_name(
        self, node: Node, source: bytes
    ) -> tuple[str, Optional[str]]:
        t = node.type
        if t == "class_declaration":
            return "class", self._decl_name(node, source)
        if t == "interface_declaration":
            return "interface", self._decl_name(node, source)
        if t == "trait_declaration":
            return "trait", self._decl_name(node, source)
        if t == "enum_declaration":
            return "enum", self._decl_name(node, source)
        if t == "anonymous_class":
            # No name in source; synthesize one so inner methods don't leak.
            self._anon_counter += 1
            return "class", f"__anon_class_{self._anon_counter}"
        return "class", None

    def _heritage_clauses(self, node: Node):
        """Yield (edge_kind, clause_node) for extends/implements."""
        for child in node.children:
            if child.type == "base_clause":  # class extends / interface extends
                yield "extends", child
            elif child.type == "class_interface_clause":  # class implements
                yield "implements", child

    def _clause_names(self, clause: Node, source: bytes) -> List[str]:
        """All bare names in an extends/implements clause."""
        return [
            self._strip_leading_backslash(self._node_text(c, source).strip())
            for c in clause.children
            if c.type == "name"
        ]

    def _parse_function(self, node: Node, source: bytes) -> Optional[Symbol]:
        name = self._decl_name(node, source)
        if not name:
            return None
        return Symbol(
            name=name,
            kind="function",
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
        )

    def _parse_method(
        self, node: Node, source: bytes, pf: ParsedFile
    ) -> Optional[Symbol]:
        name = self._decl_name(node, source)
        if not name:
            return None
        # PHP 8.0 constructor property promotion: parameters that are also
        # properties (``public float $x``) appear as property_promotion_parameter
        # children under formal_parameters. Capture them as property symbols.
        params = self._child_of_type(node, ("formal_parameters",))
        if params is not None:
            for p in params.children:
                if p.type == "property_promotion_parameter":
                    var = self._child_of_type(p, ("variable_name",))
                    if var is not None:
                        pname = self._decl_name(var, source)
                        if pname:
                            pf.symbols.append(
                                Symbol(
                                    name=pname,
                                    kind="property",
                                    qualified_name=self._qualified_name(pname),
                                    line_start=p.start_point[0] + 1,
                                    line_end=p.end_point[0] + 1,
                                    column_start=p.start_point[1],
                                    column_end=p.end_point[1],
                                )
                            )
        return Symbol(
            name=name,
            kind="method",
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
        )

    def _parse_enum_case(self, node: Node, source: bytes) -> Optional[Symbol]:
        name = self._decl_name(node, source)
        if not name:
            return None
        return Symbol(
            name=name,
            kind="enum_case",
            qualified_name=self._qualified_name(name),
            line_start=node.start_point[0] + 1,
            line_end=node.end_point[0] + 1,
            column_start=node.start_point[1],
            column_end=node.end_point[1],
        )

    def _parse_property(self, node: Node, source: bytes) -> List[Symbol]:
        """property_declaration -> one Symbol per property_element child."""
        out: List[Symbol] = []
        for child in node.children:
            if child.type != "property_element":
                continue
            var = self._child_of_type(child, ("variable_name",))
            if var is None:
                continue
            nm = self._decl_name(var, source)
            if not nm:
                continue
            out.append(
                Symbol(
                    name=nm,
                    kind="property",
                    qualified_name=self._qualified_name(nm),
                    line_start=node.start_point[0] + 1,
                    line_end=node.end_point[0] + 1,
                    column_start=node.start_point[1],
                    column_end=node.end_point[1],
                )
            )
        return out

    # ------------------------------------------------------------ call parsing

    def _parse_call(self, node: Node, source: bytes) -> Optional[Edge]:
        """function/member/nullsafe/scoped call -> Edge(calls)."""
        if node.type == "function_call_expression":
            callee, receiver = self._split_function_call(node, source)
        elif node.type in ("member_call_expression", "nullsafe_member_call_expression"):
            callee, receiver = self._split_member_call(node, source)
        elif node.type == "object_creation_expression":
            callee, receiver = self._split_object_creation(node, source)
        else:  # scoped_call_expression: Class::method() / $inst::method()
            callee, receiver = self._split_scoped_call(node, source)
        if not callee:
            return None
        return Edge(
            source_name=self._current_edge_owner(),
            kind="calls",
            target_name=callee,
            line=node.start_point[0] + 1,
            receiver_type=self._infer_receiver_type(receiver),
        )

    def _split_object_creation(self, node: Node, source: bytes):
        # object_creation_expression: 'new' (name | qualified_name) arguments.
        # The constructed class may be qualified (``new App\Models\User``);
        # store the last ``\``-segment so the edge can match the class's bare
        # symbol name. (new Foo() -> Foo; new A\B\Foo() -> Foo.)
        for child in node.children:
            if child.type in ("name", "qualified_name"):
                return self._bare_ns_name(
                    self._node_text(child, source).strip()
                ), None
        return None, None

    def _split_function_call(self, node: Node, source: bytes):
        # function_call_expression: (name | qualified_name) arguments.
        # A qualified call (``App\Utils\sanitize(...)``) stores the LAST
        # `\`-segment as the target: the resolver keys on bare symbol names
        # (symbols.name) and its import tier splits `/` and `.` but never `\`,
        # so the full namespace path could never match the callee's stored
        # name (cf. Java's ``com.example.Bar`` -> ``Bar`` for `new`).
        for child in node.children:
            if child.type in ("name", "qualified_name"):
                return self._bare_ns_name(
                    self._node_text(child, source).strip()
                ), None
        return None, None

    def _split_member_call(self, node: Node, source: bytes):
        # member_call_expression: (variable_name | member_call_expression |
        # function_call_expression) '->' name arguments
        callee = None
        receiver = None
        for child in node.children:
            if child.type == "name" and callee is None:
                callee = self._node_text(child, source).strip()
            elif child.type in (
                "variable_name",
                "member_call_expression",
                "function_call_expression",
            ):
                if receiver is None:
                    receiver = self._node_text(child, source).strip()
        return callee, receiver

    def _split_scoped_call(self, node: Node, source: bytes):
        # scoped_call_expression: (name | qualified_name | variable_name) '::'
        # name arguments. The trailing name is the method; the scope qualifier
        # is the receiver.
        names = [c for c in node.children if c.type == "name"]
        if names:
            callee = self._node_text(names[-1], source).strip()
            # Receiver: whatever appears before '::'. May be a name,
            # qualified_name, or variable_name. Reduce a qualified receiver
            # (``App\Models\User::find``) to its last ``\``-segment so the
            # inferred receiver_type can match the class's bare symbol name.
            receiver_text = None
            for child in node.children:
                if child.type == "::":
                    break
                if child.type in ("name", "qualified_name", "variable_name"):
                    receiver_text = self._node_text(child, source).strip()
            if receiver_text is not None:
                receiver_text = self._bare_ns_name(receiver_text)
            return callee, receiver_text
        return None, None

    # ------------------------------------------------------------- import parse

    def _parse_use_imports(self, node: Node, source: bytes) -> List[Import]:
        """namespace_use_declaration -> Import per clause.

        Handles single (``use Foo\\A;``), multi (``use Foo\\A, Bar\\B;``), and
        grouped (``use Foo\\{A, B};``) forms. For grouped, the prefix
        namespace_name is prepended to each inner clause name.
        """
        imports: List[Import] = []
        # Grouped form: namespace_use_group contains namespace_use_clause children.
        group = self._child_of_type(node, ("namespace_use_group",))
        if group is not None:
            prefix = ""
            ns = self._child_of_type(node, ("namespace_name",))
            if ns is not None:
                prefix = self._node_text(ns, source).strip()
            for clause in group.children:
                if clause.type != "namespace_use_clause":
                    continue
                inner = self._child_of_type(clause, ("qualified_name", "name"))
                if inner is None:
                    continue
                inner_name = self._node_text(inner, source).strip()
                path = f"{prefix}\\{inner_name}" if prefix else inner_name
                imports.append(Import(imported_path=path, line=node.start_point[0] + 1))
            return imports
        # Ungrouped form: one or more namespace_use_clause children directly.
        for clause in node.children:
            if clause.type != "namespace_use_clause":
                continue
            qn = self._child_of_type(clause, ("qualified_name",))
            if qn is not None:
                imports.append(
                    Import(
                        imported_path=self._node_text(qn, source).strip(),
                        line=node.start_point[0] + 1,
                    )
                )
        return imports

    def _parse_require_import(self, node: Node, source: bytes) -> Optional[Import]:
        # require/include expression nodes wrap the path argument verbatim.
        return Import(
            imported_path=self._node_text(node, source).strip(),
            line=node.start_point[0] + 1,
        )

    # ---------------------------------------------------------------- helpers

    def _decl_name(self, node: Node, source: bytes) -> Optional[str]:
        """First ``name`` child text (PHP uses ``name`` nodes, not ``identifier``)."""
        for child in node.children:
            if child.type == "name":
                return self._strip_leading_backslash(
                    self._node_text(child, source).strip()
                )
        return None

    @staticmethod
    def _strip_leading_backslash(name: str) -> str:
        """Strip a leading ``\\`` from a fully-qualified PHP name.

        ``\\array_map`` and ``App\\Lib\\foo`` are FQN forms; the leading global
        namespace separator would break bare-name resolution against a symbol
        defined as ``array_map``.
        """
        return name.lstrip("\\")

    @classmethod
    def _bare_ns_name(cls, name: str) -> str:
        """Reduce a PHP name to its last ``\\``-segment (the bare symbol name).

        The graph resolver keys call edges on bare symbol names (``symbols.name``)
        and its import-aware tier splits import paths on ``/`` and ``.`` but
        never ``\\``, so a multi-segment qualified name (``App\\Utils\\sanitize``,
        ``App\\Models\\User``) can never match a callee stored as ``sanitize`` /
        ``User``. Call edges and inferred receiver types therefore use the last
        segment; the qualified form stays available on the symbol's
        qualified_name for navigation.
        """
        return cls._strip_leading_backslash(name).rsplit("\\", 1)[-1]
