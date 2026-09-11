"""Signal tests for the Java parser.

Covers the parser-side signals java's grammar exposes (FR-006/FR-007):

- ``Import.local_alias`` — Java has no import aliasing at all, so every
  import form must carry ``local_alias=None`` over a normalized path
  (F2.1: ``import_declaration`` children are ``scoped_identifier`` / bare
  ``identifier`` / trailing ``asterisk`` wildcard; no alias field exists).
- ``Symbol.arity`` / ``Edge.call_arity`` — parameter counts on
  ``method_declaration`` and argument counts on ``method_invocation`` /
  ``object_creation_expression``. Varargs degrades to None (D-005: a
  declared count can never match a varying call-site count).
- ``Edge.receiver_type`` — pins the existing capitalized-receiver
  heuristic: ``this`` and lowercase locals abstain to None (FR-009).
"""
from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from cairn.parsers.java import JavaParser


def _parse(source: bytes):
    """Parse ``source`` with JavaParser via a temp file. Returns ParsedFile."""
    with tempfile.NamedTemporaryFile(suffix=".java", delete=False, mode="wb") as f:
        f.write(source)
        path = f.name
    try:
        return JavaParser().parse(path)
    finally:
        Path(path).unlink(missing_ok=True)


def _call_edge(pf, target: str):
    edges = [e for e in pf.edges if e.kind == "calls" and e.target_name == target]
    assert edges, f"no calls edge to {target!r} in {[e.target_name for e in pf.edges]}"
    return edges[0]


def _method_symbol(pf, name: str):
    syms = [s for s in pf.symbols if s.kind == "method" and s.name == name]
    assert syms, f"no method symbol {name!r} in {[s.name for s in pf.symbols]}"
    return syms[0]


# ---------------------------------------------------------------------------
# Import signals — local_alias is always None in Java; path must normalize
# across every import form the grammar admits (F2.1).
# ---------------------------------------------------------------------------

class TestImportSignals:
    def test_plain_import_path_and_alias(self):
        pf = _parse(b"package p;\nimport java.util.List;\n")
        assert len(pf.imports) == 1
        imp = pf.imports[0]
        assert imp.imported_path == "java.util.List"
        assert imp.local_alias is None

    def test_single_name_import_is_emitted(self):
        # `import Foo;` parses as a bare identifier child -- it must not be
        # dropped, and it carries no alias.
        pf = _parse(b"package p;\nimport Foo;\n")
        assert len(pf.imports) == 1
        imp = pf.imports[0]
        assert imp.imported_path == "Foo"
        assert imp.local_alias is None

    def test_wildcard_import_path_keeps_asterisk(self):
        # The asterisk is a sibling of the scoped_identifier; the emitted
        # path must keep it so the wildcard is not silently mistaken for a
        # direct import of the exact dotted name.
        pf = _parse(b"package p;\nimport java.util.*;\n")
        assert len(pf.imports) == 1
        imp = pf.imports[0]
        assert imp.imported_path == "java.util.*"
        assert imp.local_alias is None

    def test_static_import_path_and_alias(self):
        pf = _parse(b"package p;\nimport static java.util.Arrays.asList;\n")
        assert len(pf.imports) == 1
        imp = pf.imports[0]
        assert imp.imported_path == "java.util.Arrays.asList"
        assert imp.local_alias is None


# ---------------------------------------------------------------------------
# Definition arity — formal_parameters count on method declarations.
# ---------------------------------------------------------------------------

class TestSymbolArity:
    def test_zero_parameter_method(self):
        pf = _parse(b"class A { void m() {} }\n")
        assert _method_symbol(pf, "m").arity == 0

    def test_two_parameter_method(self):
        pf = _parse(b"class A { void m(int a, String b) {} }\n")
        assert _method_symbol(pf, "m").arity == 2

    def test_generic_parameter_counts_as_one(self):
        pf = _parse(
            b"class A { void m(java.util.Map<String, java.util.List<Integer>> x) {} }\n"
        )
        assert _method_symbol(pf, "m").arity == 1

    def test_final_modifier_parameter_counts_once(self):
        pf = _parse(b"class A { void m(final int a) {} }\n")
        assert _method_symbol(pf, "m").arity == 1

    def test_receiver_parameter_not_counted(self):
        # `A this` is an explicit receiver, not a call-site argument.
        pf = _parse(b"class A { void m(A this, int x) {} }\n")
        assert _method_symbol(pf, "m").arity == 1

    def test_varargs_method_arity_is_none(self):
        # D-005: varargs call sites pass a varying count, so the declared
        # count must stay unknown rather than mismatch every call.
        pf = _parse(b"class A { void m(String... parts) {} }\n")
        assert _method_symbol(pf, "m").arity is None

    def test_class_and_field_symbols_have_no_arity(self):
        pf = _parse(b"class A { private int x; void m() {} }\n")
        cls = [s for s in pf.symbols if s.kind == "class"][0]
        field = [s for s in pf.symbols if s.kind == "property"][0]
        assert cls.arity is None
        assert field.arity is None


# ---------------------------------------------------------------------------
# Call-site arity — argument_list count on invocations and constructor calls.
# ---------------------------------------------------------------------------

class TestCallArity:
    def test_zero_argument_call(self):
        pf = _parse(b"class A { void x() { helper(); } }\n")
        assert _call_edge(pf, "helper").call_arity == 0

    def test_three_argument_call(self):
        pf = _parse(b"class A { void x() { helper(a, b, c); } }\n")
        assert _call_edge(pf, "helper").call_arity == 3

    def test_comment_between_arguments_not_counted(self):
        pf = _parse(b"class A { void x() { helper(1 /* one */, 2); } }\n")
        assert _call_edge(pf, "helper").call_arity == 2

    def test_qualified_call_arity(self):
        pf = _parse(b"class A { void x() { Foo.bar(1); } }\n")
        assert _call_edge(pf, "bar").call_arity == 1

    def test_nested_calls_counted_independently(self):
        pf = _parse(b"class A { void x() { outer(inner(1), 2); } }\n")
        assert _call_edge(pf, "outer").call_arity == 2
        assert _call_edge(pf, "inner").call_arity == 1

    def test_constructor_call_arity(self):
        pf = _parse(b"class A { void x() { Foo f = new Foo(1, 2); } }\n")
        assert _call_edge(pf, "Foo").call_arity == 2


# ---------------------------------------------------------------------------
# Receiver pins — existing behavior must not regress (FR-009: absent or
# lowercase receivers abstain to None, never a guess).
# ---------------------------------------------------------------------------

class TestReceiverType:
    def test_capitalized_receiver_is_pinned(self):
        pf = _parse(b"class A { void x() { Foo.bar(1); } }\n")
        assert _call_edge(pf, "bar").receiver_type == "Foo"

    def test_this_receiver_abstains(self):
        pf = _parse(b"class A { void x() { this.helper(1); } }\n")
        assert _call_edge(pf, "helper").receiver_type is None

    def test_bare_call_has_no_receiver(self):
        pf = _parse(b"class A { void x() { helper(a); } }\n")
        assert _call_edge(pf, "helper").receiver_type is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
