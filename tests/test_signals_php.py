"""Signal tests for the PHP parser.

Covers the parser-side signals php's grammar exposes (FR-006/FR-007):

- ``Import.local_alias`` — the ``alias`` field on ``namespace_use_clause``
  (F2.1, field-labeled at tree-sitter-php 0.24.1) across plain, multi-clause,
  grouped, ``use function``/``use const``, and single-segment forms; plain
  ``use`` clauses and require/include carry ``local_alias=None``.
- ``Symbol.arity`` / ``Edge.call_arity`` — ``formal_parameters`` count on
  ``function_definition`` / ``method_declaration`` (promoted constructor
  parameters count; variadic and defaulted parameters degrade to None per
  D-005: a declared count can never match a varying call-site count) and
  ``arguments`` count on the five call node types (spread unpacking and the
  first-class-callable ``...`` placeholder degrade to None; ``new Foo;``
  passes zero arguments).
- ``Edge.receiver_type`` — pins the existing capitalized-receiver heuristic:
  a qualified scoped-call receiver is reduced to its last ``\\``-segment and
  pinned; lowercase ``$var`` receivers abstain to None (FR-009).

Closures (``anonymous_function`` / ``arrow_function``) produce no Symbol
(unnamed), so their parameter lists carry no arity; ``use (...)`` captures
are never parameters.
"""
from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from cairn.parsers.php import PhpParser


def _parse(source: bytes):
    """Parse ``source`` with PhpParser via a temp file. Returns ParsedFile."""
    with tempfile.NamedTemporaryFile(suffix=".php", delete=False, mode="wb") as f:
        f.write(source)
        path = f.name
    try:
        return PhpParser().parse(path)
    finally:
        Path(path).unlink(missing_ok=True)


def _call_edge(pf, target: str):
    edges = [e for e in pf.edges if e.kind == "calls" and e.target_name == target]
    assert edges, f"no calls edge to {target!r} in {[e.target_name for e in pf.edges]}"
    return edges[0]


def _symbol(pf, name: str, kind: str):
    syms = [s for s in pf.symbols if s.name == name and s.kind == kind]
    assert syms, f"no {kind} symbol {name!r} in {[(s.kind, s.name) for s in pf.symbols]}"
    return syms[0]


# ---------------------------------------------------------------------------
# Import signals — local_alias on the namespace_use_clause `alias` field.
# ---------------------------------------------------------------------------

class TestImportAlias:
    def test_plain_use_records_no_alias(self):
        # The imported tail already binds locally; no alias to record.
        pf = _parse(b"<?php\nuse Foo\\Bar;\n")
        assert len(pf.imports) == 1
        imp = pf.imports[0]
        assert imp.imported_path == "Foo\\Bar"
        assert imp.local_alias is None

    def test_aliased_use_records_alias(self):
        pf = _parse(b"<?php\nuse Foo\\Bar as B;\n")
        assert len(pf.imports) == 1
        imp = pf.imports[0]
        assert imp.imported_path == "Foo\\Bar"
        assert imp.local_alias == "B"

    def test_single_segment_aliased_use_is_emitted(self):
        # `use Logger as L;` carries a plain `name` child (no qualified_name);
        # the import and its alias must not be dropped.
        pf = _parse(b"<?php\nuse Logger as L;\n")
        assert len(pf.imports) == 1
        imp = pf.imports[0]
        assert imp.imported_path == "Logger"
        assert imp.local_alias == "L"

    def test_multi_clause_use_records_each_alias(self):
        pf = _parse(b"<?php\nuse Foo\\A, Foo\\B as BB;\n")
        assert [(i.imported_path, i.local_alias) for i in pf.imports] == [
            ("Foo\\A", None),
            ("Foo\\B", "BB"),
        ]

    def test_grouped_use_clauses_carry_aliases(self):
        pf = _parse(b"<?php\nuse App\\{A as AA, B, C as CC};\n")
        assert [(i.imported_path, i.local_alias) for i in pf.imports] == [
            ("App\\A", "AA"),
            ("App\\B", None),
            ("App\\C", "CC"),
        ]

    def test_use_function_alias(self):
        pf = _parse(b"<?php\nuse function Foo\\bar as baz;\n")
        assert len(pf.imports) == 1
        imp = pf.imports[0]
        assert imp.imported_path == "Foo\\bar"
        assert imp.local_alias == "baz"

    def test_use_const_alias(self):
        pf = _parse(b"<?php\nuse const Foo\\X as Y;\n")
        assert len(pf.imports) == 1
        imp = pf.imports[0]
        assert imp.imported_path == "Foo\\X"
        assert imp.local_alias == "Y"

    def test_require_records_no_alias(self):
        pf = _parse(b'<?php\nrequire_once __DIR__ . "/helpers.php";\n')
        assert len(pf.imports) == 1
        assert pf.imports[0].local_alias is None


# ---------------------------------------------------------------------------
# Definition arity — formal_parameters count on functions and methods.
# ---------------------------------------------------------------------------

class TestSymbolArity:
    def test_function_parameter_count(self):
        pf = _parse(b"<?php\nfunction add(int $a, int $b): int { return $a + $b; }\n")
        assert _symbol(pf, "add", "function").arity == 2

    def test_by_ref_parameter_counts(self):
        pf = _parse(b"<?php\nfunction g(string $x, float &$y) {}\n")
        assert _symbol(pf, "g", "function").arity == 2

    def test_zero_parameter_function(self):
        pf = _parse(b"<?php\nfunction go(): void {}\n")
        assert _symbol(pf, "go", "function").arity == 0

    def test_interface_method_without_body_counts(self):
        pf = _parse(b"<?php\ninterface I { public function n(): string; }\n")
        assert _symbol(pf, "n", "method").arity == 0

    def test_promoted_constructor_parameters_count(self):
        # property_promotion_parameter children are real parameters.
        pf = _parse(
            b"<?php\nclass C { public function __construct(public string $n, private int $i) {} }\n"
        )
        assert _symbol(pf, "__construct", "method").arity == 2

    def test_default_value_degrades_to_none(self):
        # A defaulted parameter accepts a varying call-site count (D-005).
        pf = _parse(b"<?php\nfunction f($a, $b = 2) {}\n")
        assert _symbol(pf, "f", "function").arity is None

    def test_variadic_degrades_to_none(self):
        pf = _parse(b"<?php\nfunction f(...$rest) {}\n")
        assert _symbol(pf, "f", "function").arity is None

    def test_property_symbol_has_no_arity(self):
        pf = _parse(b"<?php\nclass C { public string \$role; }\n")
        assert _symbol(pf, "role", "property").arity is None


# ---------------------------------------------------------------------------
# Call-site arity — arguments count on the five call node types.
# ---------------------------------------------------------------------------

class TestCallArity:
    def test_function_call_argument_count(self):
        pf = _parse(b"<?php\nfunction c() { add(1, 2); }\n")
        assert _call_edge(pf, "add").call_arity == 2

    def test_zero_argument_call(self):
        pf = _parse(b"<?php\nfunction c() { go(); }\n")
        assert _call_edge(pf, "go").call_arity == 0

    def test_named_arguments_count(self):
        pf = _parse(b"<?php\nfunction c() { foo(name: 1, other: 2); }\n")
        assert _call_edge(pf, "foo").call_arity == 2

    def test_spread_argument_degrades_to_none(self):
        # `foo(...$args)` passes an unknown count of arguments.
        pf = _parse(b"<?php\nfunction c($args) { foo(...\$args); }\n")
        assert _call_edge(pf, "foo").call_arity is None

    def test_first_class_callable_degrades_to_none(self):
        # `strlen(...)` creates a closure; it is not a countable call.
        pf = _parse(b"<?php\nfunction c() { strlen(...); }\n")
        assert _call_edge(pf, "strlen").call_arity is None

    def test_member_call_arity(self):
        pf = _parse(b"<?php\nfunction c($o) { \$o->m(a: 1); }\n")
        assert _call_edge(pf, "m").call_arity == 1

    def test_nullsafe_member_call_arity(self):
        pf = _parse(b"<?php\nfunction c($o) { \$o?->m(); }\n")
        assert _call_edge(pf, "m").call_arity == 0

    def test_scoped_call_arity(self):
        pf = _parse(b"<?php\nfunction c() { Foo::bar(1); }\n")
        assert _call_edge(pf, "bar").call_arity == 1

    def test_constructor_call_arity(self):
        pf = _parse(b"<?php\nfunction c() { $u = new Foo(1, 2); }\n")
        assert _call_edge(pf, "Foo").call_arity == 2

    def test_new_without_parentheses_is_zero(self):
        # `new Foo;` invokes the constructor with zero arguments.
        pf = _parse(b"<?php\nfunction c() { $u = new Foo; }\n")
        assert _call_edge(pf, "Foo").call_arity == 0


# ---------------------------------------------------------------------------
# Receiver pins — existing behavior must not regress (FR-009: lowercase
# receivers abstain to None, never a guess).
# ---------------------------------------------------------------------------

class TestReceiverType:
    def test_qualified_scoped_receiver_reduced_and_pinned(self):
        # App\Utils\Formatter::format(...) -> receiver_type is the bare
        # class name, matching the class's bare symbol name.
        pf = _parse(b"<?php\nfunction c() { return App\\Utils\\Formatter::format(1); }\n")
        assert _call_edge(pf, "format").receiver_type == "Formatter"

    def test_local_variable_receiver_abstains(self):
        pf = _parse(b'<?php\nfunction c($logger) { $logger?->info("x"); }\n')
        assert _call_edge(pf, "info").receiver_type is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
