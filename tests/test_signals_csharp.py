"""Signal tests for the C# parser.

Covers the parser-side signals csharp's grammar exposes (FR-006/FR-007):

- ``Import.local_alias`` — using-alias detection is structural at the pinned
  grammar version (F2.1/G2: no ``name_equals`` node exists in 0.23.1; the
  ``=`` token with an identifier-plus-qualified_name pair is the signal).
  ``using Foo = Bar.Baz;`` stores the target path with ``local_alias="Foo"``;
  plain and ``using static`` directives carry no alias.
- ``Edge.receiver_type`` — member-access receivers resolved through the
  shared scope-ordered var->type tracker (locals from ``var``/explicit
  declarations, fields, parameters, ``foreach`` iteration variables), with
  the capitalized heuristic for static-class receivers. Reassignment to a
  different type and live shadowing poison the name to None (D-006);
  unresolvable receivers abstain to None (FR-009).
- ``Symbol.arity`` / ``Edge.call_arity`` — parameter counts on method and
  constructor declarations, argument counts on invocations and
  ``new`` expressions. Default-valued, ``params`` and extension-``this``
  parameters degrade the declared arity to None (D-005: a fixed count can
  never match those call sites).
"""
from __future__ import annotations

from pathlib import Path
import tempfile

import pytest

from cairn.parsers.csharp import CSharpParser


def _parse(source: bytes):
    """Parse ``source`` with CSharpParser via a temp file. Returns ParsedFile."""
    with tempfile.NamedTemporaryFile(suffix=".cs", delete=False, mode="wb") as f:
        f.write(source)
        path = f.name
    try:
        return CSharpParser().parse(path)
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
# Import signals — G2 structural alias rule on using directives.
# ---------------------------------------------------------------------------

class TestImportAlias:
    def test_alias_directive_records_target_path_and_alias(self):
        # `using Foo = Bar.Baz;` -> the resolver rewrites bare `Foo` hits to
        # the final segment of the stored path, so the path must be the
        # TARGET namespace, never the alias (D-003).
        pf = _parse(b"using Foo = Bar.Baz;\n")
        assert len(pf.imports) == 1
        imp = pf.imports[0]
        assert imp.imported_path == "Bar.Baz"
        assert imp.local_alias == "Foo"

    def test_alias_to_single_name(self):
        # Same `=` token shape with a bare-identifier target.
        pf = _parse(b"using X = Y;\n")
        assert len(pf.imports) == 1
        imp = pf.imports[0]
        assert imp.imported_path == "Y"
        assert imp.local_alias == "X"

    def test_multi_segment_alias_target_keeps_full_path(self):
        pf = _parse(b"using Project = Acme.Crm.Services;\n")
        imp = pf.imports[0]
        assert imp.imported_path == "Acme.Crm.Services"
        assert imp.local_alias == "Project"

    def test_plain_qualified_using_has_no_alias(self):
        # A qualified plain directive is NOT an alias: the qualified_name is
        # the imported namespace itself.
        pf = _parse(b"using System.Collections.Generic;\n")
        assert len(pf.imports) == 1
        imp = pf.imports[0]
        assert imp.imported_path == "System.Collections.Generic"
        assert imp.local_alias is None

    def test_plain_single_name_using_has_no_alias(self):
        pf = _parse(b"using System;\n")
        assert len(pf.imports) == 1
        imp = pf.imports[0]
        assert imp.imported_path == "System"
        assert imp.local_alias is None

    def test_static_using_is_not_an_alias(self):
        # `using static` has a qualified_name child but no `=`: structurally
        # a static import, never an alias binding.
        pf = _parse(b"using static System.Math;\n")
        assert len(pf.imports) == 1
        imp = pf.imports[0]
        assert imp.imported_path == "System.Math"
        assert imp.local_alias is None


# ---------------------------------------------------------------------------
# Receiver types — tracker-backed inference on member-access receivers.
# ---------------------------------------------------------------------------

class TestReceiverType:
    def test_var_local_from_constructor(self):
        pf = _parse(
            b"class C {\n"
            b"    void Go() {\n"
            b"        var c = new Client();\n"
            b"        c.Send(1);\n"
            b"    }\n"
            b"}\n"
        )
        assert _call_edge(pf, "Send").receiver_type == "Client"

    def test_explicit_type_local(self):
        pf = _parse(
            b"class C {\n"
            b"    void Go() {\n"
            b"        Client c2 = new Client();\n"
            b"        c2.Send();\n"
            b"    }\n"
            b"}\n"
        )
        assert _call_edge(pf, "Send").receiver_type == "Client"

    def test_field_receiver(self):
        pf = _parse(
            b"class C {\n"
            b"    private readonly ILogger _logger;\n"
            b"    void Go() { _logger.Info(\"x\"); }\n"
            b"}\n"
        )
        assert _call_edge(pf, "Info").receiver_type == "ILogger"

    def test_parameter_receiver(self):
        pf = _parse(
            b"class C {\n"
            b"    void Go(ILogger logger) { logger.Info(\"x\"); }\n"
            b"}\n"
        )
        assert _call_edge(pf, "Info").receiver_type == "ILogger"

    def test_var_local_from_typed_local(self):
        # `var b = a;` inherits a's recorded type through the tracker.
        pf = _parse(
            b"class C {\n"
            b"    void Go() {\n"
            b"        Client a = new Client();\n"
            b"        var b = a;\n"
            b"        b.Send();\n"
            b"    }\n"
            b"}\n"
        )
        assert _call_edge(pf, "Send").receiver_type == "Client"

    def test_nullable_generic_and_qualified_local_types(self):
        pf = _parse(
            b"using App.Crm;\n"
            b"class C {\n"
            b"    void Go() {\n"
            b"        Client? nc = null;\n"
            b"        nc.Check();\n"
            b"        System.Collections.Generic.List<int> xs = null;\n"
            b"        xs.Add(1);\n"
            b"        var d = new Deep.Nested();\n"
            b"        d.Go();\n"
            b"    }\n"
            b"}\n"
        )
        assert _call_edge(pf, "Check").receiver_type == "Client"
        assert _call_edge(pf, "Add").receiver_type == "List"
        assert _call_edge(pf, "Go").receiver_type == "Nested"

    def test_foreach_explicit_type_iteration_variable(self):
        pf = _parse(
            b"class C {\n"
            b"    void Go() {\n"
            b"        foreach (User u in users) { u.Save(); }\n"
            b"    }\n"
            b"}\n"
        )
        assert _call_edge(pf, "Save").receiver_type == "User"

    def test_foreach_var_degrades_to_none(self):
        # `var` over an untyped collection expression cannot be inferred
        # without type checking -- abstain (FR-007: never a guess).
        pf = _parse(
            b"class C {\n"
            b"    void Go() {\n"
            b"        foreach (var u in users) { u.Save(); }\n"
            b"    }\n"
            b"}\n"
        )
        assert _call_edge(pf, "Save").receiver_type is None

    def test_capitalized_static_receiver(self):
        # Unrecorded capitalized receivers fall through to the shared
        # heuristic: `Client.Static()` reads as a static call on Client.
        pf = _parse(b"class C {\n    void Go() { Client.Static(); }\n}\n")
        assert _call_edge(pf, "Static").receiver_type == "Client"

    def test_this_and_bare_and_lowercase_receivers_abstain(self):
        pf = _parse(
            b"class C {\n"
            b"    void Go() {\n"
            b"        this.Go();\n"
            b"        Plain(3);\n"
            b"        helper.run(1);\n"
            b"    }\n"
            b"}\n"
        )
        assert _call_edge(pf, "Go").receiver_type is None
        assert _call_edge(pf, "Plain").receiver_type is None
        assert _call_edge(pf, "run").receiver_type is None

    def test_chained_receiver_abstains(self):
        # `repo.find(1).update(2)`: the outer receiver is a call expression,
        # not a name -- no type can be claimed for it.
        pf = _parse(
            b"class C {\n"
            b"    void Go() { repo.find(1).update(2); }\n"
            b"}\n"
        )
        assert _call_edge(pf, "update").receiver_type is None

    def test_reassignment_to_different_type_poisons(self):
        # D-006: after `c = new Other();` the local's type is conflicting;
        # it must resolve to None, never either guess.
        pf = _parse(
            b"class C {\n"
            b"    void Go() {\n"
            b"        var c = new Client();\n"
            b"        c = new Other();\n"
            b"        c.Send();\n"
            b"    }\n"
            b"}\n"
        )
        assert _call_edge(pf, "Send").receiver_type is None

    def test_shadowing_different_type_poisons_until_scope_pops(self):
        # An inner redeclaration of a live, differently-typed name poisons it
        # for the inner scope only; the outer binding survives the pop.
        pf = _parse(
            b"class C {\n"
            b"    void Go() {\n"
            b"        var c = new Client();\n"
            b"        {\n"
            b"            var c = new Other();\n"
            b"            c.Send();\n"
            b"        }\n"
            b"        c.Send();\n"
            b"    }\n"
            b"}\n"
        )
        edges = [e for e in pf.edges if e.kind == "calls" and e.target_name == "Send"]
        assert len(edges) == 2
        assert edges[0].receiver_type is None  # inner shadowed use
        assert edges[1].receiver_type == "Client"  # outer use after pop


# ---------------------------------------------------------------------------
# Arity — declared parameter counts and call-site argument counts.
# ---------------------------------------------------------------------------

class TestSymbolArity:
    def test_zero_parameter_method(self):
        pf = _parse(b"class C { void m() {} }\n")
        assert _method_symbol(pf, "m").arity == 0

    def test_two_parameter_method(self):
        pf = _parse(b"class C { void m(int a, string b) {} }\n")
        assert _method_symbol(pf, "m").arity == 2

    def test_constructor_arity(self):
        pf = _parse(b"class C { public C(ILogger logger, int n) { } }\n")
        ctors = [s for s in pf.symbols if s.kind == "method" and s.name == "C"]
        assert len(ctors) == 1
        assert ctors[0].arity == 2

    def test_modifier_parameters_count_once(self):
        # ref/out/in are modifiers, not extra parameters.
        pf = _parse(b"class C { void m(ref int a, out string b, in object c) {} }\n")
        assert _method_symbol(pf, "m").arity == 3

    def test_default_value_parameter_arity_is_none(self):
        # D-005: `b = 5` admits call sites passing 1 or 2 arguments, so the
        # declared count must stay unknown.
        pf = _parse(b"class C { void m(int a, int b = 5) {} }\n")
        assert _method_symbol(pf, "m").arity is None

    def test_params_array_arity_is_none(self):
        pf = _parse(b"class C { void m(params object[] rest) {} }\n")
        assert _method_symbol(pf, "m").arity is None

    def test_extension_this_parameter_arity_is_none(self):
        # Extension methods are called without their `this` argument.
        pf = _parse(b"static class E { static void M(this Client c) { } }\n")
        assert _method_symbol(pf, "M").arity is None

    def test_class_and_field_symbols_have_no_arity(self):
        pf = _parse(b"class C { private int x; void m() {} }\n")
        cls = [s for s in pf.symbols if s.kind == "class"][0]
        field = [s for s in pf.symbols if s.kind == "property"][0]
        assert cls.arity is None
        assert field.arity is None


class TestCallArity:
    def test_invocation_argument_count(self):
        pf = _parse(b"class C {\n    void Go() { c.Send(1, 2); }\n}\n")
        assert _call_edge(pf, "Send").call_arity == 2

    def test_zero_argument_invocation(self):
        pf = _parse(b"class C {\n    void Go() { Plain(); }\n}\n")
        assert _call_edge(pf, "Plain").call_arity == 0

    def test_object_creation_argument_count(self):
        pf = _parse(b"class C {\n    void Go() { var u = new User(null); }\n}\n")
        assert _call_edge(pf, "User").call_arity == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
