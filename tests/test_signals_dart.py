"""Parser-signal tests for Dart: import alias, receiver type, arity.

tree-sitter-dart 0.1.0 exposes no field labels on ``import_specification``
or ``selector`` nodes, so every signal is positional: the import alias is
the ``identifier`` child following the ``as`` token, and a call's receiver
is the prefix identifier of a selector chain. Wherever the positional
evidence is ambiguous (combinator identifiers, deep property chains,
untyped locals, spread arguments) the parser must abstain (None), never
guess.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cairn.parsers.dart import DartParser


def _parse(source: bytes):
    """Parse ``source`` with DartParser via a temp file. Returns ParsedFile."""
    with tempfile.NamedTemporaryFile(suffix=".dart", delete=False, mode="wb") as f:
        f.write(source)
        path = f.name
    try:
        return DartParser().parse(path)
    finally:
        Path(path).unlink(missing_ok=True)


def _calls(pf, target):
    return [e for e in pf.edges if e.kind == "calls" and e.target_name == target]


def _symbol(pf, name):
    return next(s for s in pf.symbols if s.name == name)


# ---------------------------------------------------------------------------
# Import alias -- positional: identifier child after the `as` token
# ---------------------------------------------------------------------------

class TestImportAlias:
    def test_import_alias_recorded(self):
        pf = _parse(b"import 'package:a/a.dart' as a;\n")
        assert len(pf.imports) == 1
        assert pf.imports[0].imported_path == "package:a/a.dart"
        assert pf.imports[0].local_alias == "a"

    def test_deferred_import_alias_recorded(self):
        pf = _parse(b"import 'package:a/a.dart' deferred as x;\n")
        assert pf.imports[0].local_alias == "x"

    def test_alias_survives_show_combinator(self):
        pf = _parse(b"import 'package:a/a.dart' as a show foo;\n")
        assert pf.imports[0].local_alias == "a"

    def test_show_combinator_is_not_an_alias(self):
        pf = _parse(b"import 'package:a/a.dart' show foo, bar;\n")
        assert pf.imports[0].local_alias is None

    def test_hide_combinator_is_not_an_alias(self):
        pf = _parse(b"import 'package:a/a.dart' hide bar;\n")
        assert pf.imports[0].local_alias is None

    def test_plain_import_has_no_alias(self):
        pf = _parse(b"import 'dart:math';\n")
        assert pf.imports[0].local_alias is None

    def test_export_has_no_alias(self):
        pf = _parse(b"export 'package:a/a.dart' show foo;\n")
        assert pf.imports[0].local_alias is None


# ---------------------------------------------------------------------------
# Receiver type -- prefix identifier of the positional selector chain
# ---------------------------------------------------------------------------

class TestReceiverType:
    def test_capitalized_prefix_identifier_is_receiver(self):
        pf = _parse(b"void t() { Foo.staticM(); }\n")
        assert len(_calls(pf, "staticM")) == 1
        assert _calls(pf, "staticM")[0].receiver_type == "Foo"

    def test_typed_local_variable_is_receiver(self):
        pf = _parse(b"void t() { Foo f = Foo(); f.bar(1); }\n")
        assert _calls(pf, "bar")[0].receiver_type == "Foo"

    def test_typed_parameter_is_receiver(self):
        pf = _parse(b"void t(Foo p) { p.m(3); }\n")
        assert _calls(pf, "m")[0].receiver_type == "Foo"

    def test_this_resolves_to_enclosing_class(self):
        pf = _parse(b"class C { void m() { this.helper(1, 2); } }\n")
        assert _calls(pf, "helper")[0].receiver_type == "C"

    def test_this_in_extension_is_none(self):
        pf = _parse(b"extension E on Foo { void m() { this.x(); } }\n")
        assert _calls(pf, "x")[0].receiver_type is None

    def test_untracked_lowercase_receiver_is_none(self):
        pf = _parse(b"void t() { obj.method(x); }\n")
        assert _calls(pf, "method")[0].receiver_type is None

    def test_deep_property_chain_is_none(self):
        pf = _parse(b"void t() { a.b.c(x); }\n")
        assert _calls(pf, "c")[0].receiver_type is None

    def test_conflicting_redeclaration_poisons_receiver(self):
        pf = _parse(b"void t() { Foo f = Foo(); Bar f = Bar(); f.x(); }\n")
        assert _calls(pf, "x")[0].receiver_type is None

    def test_live_shadow_poisons_until_scope_pops(self):
        pf = _parse(
            b"void t(bool c) { Foo f = Foo(); if (c) { Bar f = Bar(); f.x(); } }"
        )
        assert _calls(pf, "x")[0].receiver_type is None

    def test_scope_pop_restores_outer_binding(self):
        pf = _parse(
            b"void t(bool c) { Foo f = Foo(); if (c) { Bar f = Bar(); } f.y(); }"
        )
        assert _calls(pf, "y")[0].receiver_type == "Foo"

    def test_bare_call_has_no_receiver(self):
        pf = _parse(b"void t() { g(1); }\n")
        assert _calls(pf, "g")[0].receiver_type is None


# ---------------------------------------------------------------------------
# Call arity -- argument count at the call site
# ---------------------------------------------------------------------------

class TestCallArity:
    def test_positional_argument_count(self):
        pf = _parse(b"void t() { g(1, 2, 3); }\n")
        assert _calls(pf, "g")[0].call_arity == 3

    def test_zero_argument_call(self):
        pf = _parse(b"void t() { g(); }\n")
        assert _calls(pf, "g")[0].call_arity == 0

    def test_named_argument_counts_as_one(self):
        pf = _parse(b"void t() { g(a, b: 2); }\n")
        assert _calls(pf, "g")[0].call_arity == 2

    def test_spread_argument_degrades_to_none(self):
        pf = _parse(b"void t(List a) { g(1, ...a); }\n")
        assert _calls(pf, "g")[0].call_arity is None

    def test_constructor_call_carries_arity(self):
        pf = _parse(b"void t() { Foo f = Foo(1, 2); }\n")
        assert _calls(pf, "Foo")[0].call_arity == 2


# ---------------------------------------------------------------------------
# Symbol arity -- parameter count at definition sites
# ---------------------------------------------------------------------------

class TestSymbolArity:
    def test_function_parameter_count(self):
        pf = _parse(b"int add(int a, int b) { return a + b; }\n")
        assert _symbol(pf, "add").arity == 2

    def test_optional_parameter_group_counted(self):
        pf = _parse(b"void opt(int a, [int b = 1]) {}\n")
        assert _symbol(pf, "opt").arity == 2

    def test_named_parameter_group_counted(self):
        pf = _parse(b"void named({int x, int y = 2}) {}\n")
        assert _symbol(pf, "named").arity == 2

    def test_function_typed_parameter_not_double_counted(self):
        pf = _parse(b"void f(void cb(int x), int y) {}\n")
        assert _symbol(pf, "f").arity == 2

    def test_zero_parameter_function(self):
        pf = _parse(b"void z() {}\n")
        assert _symbol(pf, "z").arity == 0

    def test_method_and_constructor_arity(self):
        pf = _parse(b"class C { int m(int a, int b) { return a; } C(int q, {int r}); }\n")
        ctor = next(s for s in pf.symbols if s.name == "C" and s.kind == "constructor")
        assert _symbol(pf, "m").arity == 2
        assert ctor.arity == 2

    def test_getter_has_no_parameter_list(self):
        pf = _parse(b"class C { int get x => 1; }\n")
        assert _symbol(pf, "x").arity is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
