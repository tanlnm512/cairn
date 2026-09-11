"""Swift parser signal extraction: import alias contract, receiver type,
definition/call arity.

Parsed by hand from small snippets (golden snapshots are REGENERATED from
parser output, so a systematic drop self-validates — cf.
tests/test_parser_audit_fixes.py).

Contracts:
- Swift has no import aliasing: ``import Foo`` and member imports
  (``import struct Foo.Bar``) bind the path's final segment under its own
  name, so ``local_alias`` is None for every form (FR-007 — never a guess).
- ``Edge.receiver_type`` reads the ``navigation_expression`` ``target`` field
  one level down (G1: swift 0.7.3 ``call_expression`` has no field labels,
  so any ``call_expression.function`` read fails silently), resolved through
  the var→type tracker — locals (``let x: T`` / ``let x = T()`` with a
  capitalized initializer callee), parameters, and ``self`` → enclosing
  type — then the capitalized-type heuristic. Bare calls and untracked
  lowercase receivers stay None.
- ``Symbol.arity`` counts ``parameter`` children; a variadic parameter
  (``...``) makes the declared count unknowable → None. External argument
  labels and default values do not change the count.
- ``Edge.call_arity`` counts ``value_argument`` children plus each trailing
  closure (each is one argument); a ``call_expression`` without a
  ``call_suffix`` degrades to None.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cairn.parsers.swift import SwiftParser


def _parse(source: bytes):
    """Parse ``source`` with SwiftParser via a temp file. Returns ParsedFile."""
    with tempfile.NamedTemporaryFile(suffix=".swift", delete=False, mode="wb") as f:
        f.write(source)
        path = f.name
    try:
        return SwiftParser().parse(path)
    finally:
        Path(path).unlink(missing_ok=True)


def _calls(pf, target: str):
    return [e for e in pf.edges if e.kind == "calls" and e.target_name == target]


def _symbol(pf, name: str):
    return next(s for s in pf.symbols if s.name == name)


# ---------------------------------------------------------------------------
# Import aliases — swift has no aliasing construct
# ---------------------------------------------------------------------------

class TestImportAlias:
    def test_plain_import_records_no_alias(self):
        pf = _parse(b"import Foundation\n")
        assert [(i.imported_path, i.local_alias) for i in pf.imports] == [
            ("Foundation", None)
        ]

    def test_member_import_records_path_without_alias(self):
        pf = _parse(b"import struct Foo.Bar\n")
        assert [(i.imported_path, i.local_alias) for i in pf.imports] == [
            ("Foo.Bar", None)
        ]


# ---------------------------------------------------------------------------
# Receiver type — navigation_expression target one level down (G1)
# ---------------------------------------------------------------------------

class TestReceiverType:
    def test_local_annotation_feeds_receiver(self):
        pf = _parse(
            b"class C {\n"
            b"    func go() {\n"
            b"        let user: User = acquire()\n"
            b"        user.greet()\n"
            b"    }\n"
            b"}\n"
        )
        assert _calls(pf, "greet")[0].receiver_type == "User"

    def test_local_initializer_call_feeds_receiver(self):
        pf = _parse(
            b"class C {\n"
            b"    func go() {\n"
            b"        let svc = Service()\n"
            b"        svc.handle()\n"
            b"    }\n"
            b"}\n"
        )
        assert _calls(pf, "handle")[0].receiver_type == "Service"

    def test_lowercase_initializer_callee_is_not_a_type(self):
        pf = _parse(
            b"class C {\n"
            b"    func go() {\n"
            b"        let thing = makeThing()\n"
            b"        thing.tap()\n"
            b"    }\n"
            b"}\n"
        )
        assert _calls(pf, "tap")[0].receiver_type is None

    def test_param_type_feeds_receiver(self):
        pf = _parse(
            b"class C {\n"
            b"    func go(item: Item) {\n"
            b"        item.use()\n"
            b"    }\n"
            b"}\n"
        )
        assert _calls(pf, "use")[0].receiver_type == "Item"

    def test_self_receiver_resolves_to_enclosing_class(self):
        pf = _parse(
            b"class Server {\n"
            b"    func go() {\n"
            b"        self.helper()\n"
            b"    }\n"
            b"}\n"
        )
        assert _calls(pf, "helper")[0].receiver_type == "Server"

    def test_untracked_lowercase_receiver_stays_none(self):
        pf = _parse(
            b"class C {\n"
            b"    func go() {\n"
            b"        foo.bar()\n"
            b"    }\n"
            b"}\n"
        )
        assert _calls(pf, "bar")[0].receiver_type is None

    def test_bare_call_has_no_receiver(self):
        pf = _parse(
            b"class C {\n"
            b"    func go() {\n"
            b"        bar(1)\n"
            b"    }\n"
            b"}\n"
        )
        assert _calls(pf, "bar")[0].receiver_type is None

    def test_qualified_type_receiver_uses_target_text(self):
        pf = _parse(
            b"class C {\n"
            b"    func go() {\n"
            b"        Foo.Bar.baz()\n"
            b"    }\n"
            b"}\n"
        )
        # The target field's text is "Foo.Bar"; the capitalized fallback
        # passes it through verbatim (a nested-nav receiver is not a local).
        assert _calls(pf, "baz")[0].receiver_type == "Foo.Bar"

    def test_live_shadow_poisons_until_scope_pops(self):
        # D-006: an inner redeclaration under a different type makes the
        # binding ambiguous for the inner scope; the outer binding survives.
        pf = _parse(
            b"class C {\n"
            b"    func go() {\n"
            b"        var a: Alpha\n"
            b"        if flag {\n"
            b"            var a: Beta\n"
            b"            a.tap()\n"
            b"        }\n"
            b"        a.tap()\n"
            b"    }\n"
            b"}\n"
        )
        taps = {e.line: e.receiver_type for e in _calls(pf, "tap")}
        assert taps == {6: None, 8: "Alpha"}


# ---------------------------------------------------------------------------
# Definition-site arity
# ---------------------------------------------------------------------------

class TestDefinitionArity:
    def test_counts_parameters(self):
        pf = _parse(b"func add(a: Int, b: Int) -> Int { return a }\n")
        assert _symbol(pf, "add").arity == 2

    def test_zero_parameters(self):
        pf = _parse(b"func zero() {}\n")
        assert _symbol(pf, "zero").arity == 0

    def test_external_labels_count_one_each(self):
        pf = _parse(b"func f(_ a: Int, ext b: Int) {}\n")
        assert _symbol(pf, "f").arity == 2

    def test_default_values_keep_count(self):
        pf = _parse(b"func f(a: Int = 1, b: Int = 2) {}\n")
        assert _symbol(pf, "f").arity == 2

    def test_variadic_parameter_abstains(self):
        pf = _parse(b"func v(rest: Int...) {}\n")
        assert _symbol(pf, "v").arity is None

    def test_method_arity(self):
        pf = _parse(
            b"class C {\n"
            b"    func process(input: String) -> String { return input }\n"
            b"}\n"
        )
        assert _symbol(pf, "process").arity == 1


# ---------------------------------------------------------------------------
# Call-site arity
# ---------------------------------------------------------------------------

class TestCallArity:
    def test_counts_value_arguments(self):
        pf = _parse(
            b"class C {\n"
            b"    func go() {\n"
            b"        let svc = Service()\n"
            b"        svc.handle(a: 1, b: 2)\n"
            b"    }\n"
            b"}\n"
        )
        assert _calls(pf, "handle")[0].call_arity == 2

    def test_zero_arguments(self):
        pf = _parse(
            b"class C {\n"
            b"    func go() {\n"
            b"        foo.bar()\n"
            b"    }\n"
            b"}\n"
        )
        assert _calls(pf, "bar")[0].call_arity == 0

    def test_bare_call_counts_arguments(self):
        pf = _parse(
            b"class C {\n"
            b"    func go() {\n"
            b"        bar(1, 2)\n"
            b"    }\n"
            b"}\n"
        )
        assert _calls(pf, "bar")[0].call_arity == 2

    def test_trailing_closure_counts_as_one(self):
        pf = _parse(
            b"class C {\n"
            b"    func go() {\n"
            b"        list.forEach { $0 }\n"
            b"    }\n"
            b"}\n"
        )
        assert _calls(pf, "forEach")[0].call_arity == 1

    def test_parens_plus_trailing_closure(self):
        pf = _parse(
            b"class C {\n"
            b"    func go() {\n"
            b"        user.greet(1) { x in x }\n"
            b"    }\n"
            b"}\n"
        )
        assert _calls(pf, "greet")[0].call_arity == 2

    def test_multiple_trailing_closures(self):
        pf = _parse(
            b"class C {\n"
            b"    func go() {\n"
            b"        foo.perform { $0 } onFailure: { e in e }\n"
            b"    }\n"
            b"}\n"
        )
        assert _calls(pf, "perform")[0].call_arity == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
