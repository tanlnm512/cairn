"""Kotlin parser signal extraction: import aliases, definition/call arity,
and receiver pins.

Parsed by hand from small snippets (the golden snapshots are REGENERATED from
parser output, so a systematic drop self-validates — cf.
tests/test_parser_audit_fixes.py). Receiver behavior predates the signal
substrate; its tests here are regression pins.

Grammar shapes were probed empirically against the vendored in-tree grammar
(cairn._tree_sitter_kotlin): ``import a.b.C as D`` carries the alias as an
``import_alias`` child (a ``type_identifier``); definition parameters are
direct ``parameter`` children of ``function_value_parameters``; call arguments
are ``value_argument`` children of ``value_arguments`` inside ``call_suffix``.

Contracts:
- ``import a.b.C as D`` records ``imported_path='a.b.C', local_alias='D'``;
  plain and wildcard imports record no alias.
- ``Symbol.arity`` counts ``parameter`` children of
  ``function_value_parameters`` (vararg and default values don't change the
  count; an extension receiver is not a parameter); non-function kinds stay
  None.
- ``Edge.call_arity`` counts the call's arguments; a trailing lambda
  (``f(x) { ... }``) counts as one argument, matching the callee's parameter
  count in Kotlin semantics.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cairn.parsers.kotlin import KotlinParser


def _parse(source: bytes):
    """Parse ``source`` with KotlinParser via a temp file. Returns ParsedFile."""
    with tempfile.NamedTemporaryFile(suffix=".kt", delete=False, mode="wb") as f:
        f.write(source)
        path = f.name
    try:
        return KotlinParser().parse(path)
    finally:
        Path(path).unlink(missing_ok=True)


def _symbol(pf, name: str):
    return next(s for s in pf.symbols if s.name == name)


def _call_edges(pf, target: str):
    return [e for e in pf.edges if e.kind == "calls" and e.target_name == target]


# ---------------------------------------------------------------------------
# Import aliases
# ---------------------------------------------------------------------------

class TestImportAlias:
    def test_aliased_import_records_alias(self):
        pf = _parse(b"import a.b.C as D\n")
        assert [(i.imported_path, i.local_alias) for i in pf.imports] == [
            ("a.b.C", "D")
        ]

    def test_plain_import_records_no_alias(self):
        pf = _parse(b"import java.util.List\n")
        assert [(i.imported_path, i.local_alias) for i in pf.imports] == [
            ("java.util.List", None)
        ]

    def test_wildcard_import_records_no_alias(self):
        pf = _parse(b"import a.b.*\n")
        assert [(i.local_alias) for i in pf.imports] == [None]

    def test_mixed_import_list(self):
        pf = _parse(b"import java.util.List\nimport a.b.C as D\nimport e.F\n")
        assert {(i.imported_path, i.local_alias) for i in pf.imports} == {
            ("java.util.List", None),
            ("a.b.C", "D"),
            ("e.F", None),
        }


# ---------------------------------------------------------------------------
# Definition-site arity
# ---------------------------------------------------------------------------

class TestDefinitionArity:
    def test_two_typed_params(self):
        pf = _parse(b"fun add(a: Int, b: Int): Int { return a + b }\n")
        assert _symbol(pf, "add").arity == 2

    def test_zero_params(self):
        pf = _parse(b"fun zero() {}\n")
        assert _symbol(pf, "zero").arity == 0

    def test_vararg_and_default_do_not_change_count(self):
        pf = _parse(b'fun f(vararg xs: Int, y: String = "s") {}\n')
        assert _symbol(pf, "f").arity == 2

    def test_function_typed_param_counts(self):
        pf = _parse(b"class A { fun m(p: String, q: (Int) -> Unit = {}) {} }\n")
        assert _symbol(pf, "m").arity == 2

    def test_extension_receiver_is_not_a_parameter(self):
        pf = _parse(b"fun String.double(): String { return this }\n")
        assert _symbol(pf, "double").arity == 0

    def test_non_function_kinds_have_no_arity(self):
        pf = _parse(
            b"class Repo(val id: Int) { val name: String = \"\" }\n"
        )
        assert _symbol(pf, "Repo").arity is None
        assert _symbol(pf, "name").arity is None
        assert _symbol(pf, "id").arity is None


# ---------------------------------------------------------------------------
# Call-site arity
# ---------------------------------------------------------------------------

class TestCallArity:
    def test_two_args(self):
        pf = _parse(b"fun main() { foo(1, 2) }\n")
        assert _call_edges(pf, "foo")[0].call_arity == 2

    def test_zero_args(self):
        pf = _parse(b"fun main() { foo() }\n")
        assert _call_edges(pf, "foo")[0].call_arity == 0

    def test_args_on_receiver_call(self):
        pf = _parse(b"fun main() { obj.method(1) }\n")
        assert _call_edges(pf, "method")[0].call_arity == 1

    def test_named_args_count(self):
        pf = _parse(b"fun main() { bar(x = 1, y = 2) }\n")
        assert _call_edges(pf, "bar")[0].call_arity == 2

    def test_trailing_lambda_counts_as_one_argument(self):
        pf = _parse(b"fun main() { map(1) { it + 1 } }\n")
        assert _call_edges(pf, "map")[0].call_arity == 2

    def test_lambda_only_call_is_arity_one(self):
        pf = _parse(b"fun main() { foo { it } }\n")
        assert _call_edges(pf, "foo")[0].call_arity == 1

    def test_type_arguments_do_not_count(self):
        pf = _parse(b"fun main() { foo<Int>(1) }\n")
        assert _call_edges(pf, "foo")[0].call_arity == 1

    def test_spread_argument_counts_as_one(self):
        pf = _parse(b"fun main() { foo(*args) }\n")
        assert _call_edges(pf, "foo")[0].call_arity == 1


# ---------------------------------------------------------------------------
# Receiver pins (behavior predates the signal substrate — regression guard)
# ---------------------------------------------------------------------------

class TestReceiverPins:
    def test_local_var_receiver_type(self):
        pf = _parse(
            b"class Repo {\n"
            b"  fun local() {\n"
            b"    val c: Client = Client()\n"
            b"    c.get()\n"
            b"  }\n"
            b"}\n"
        )
        assert _call_edges(pf, "get")[0].receiver_type == "Client"

    def test_this_receiver_is_enclosing_type(self):
        pf = _parse(
            b"class Repo {\n"
            b"  fun local() { this.helper() }\n"
            b"  fun helper() {}\n"
            b"}\n"
        )
        assert _call_edges(pf, "helper")[0].receiver_type == "Repo"

    def test_capitalized_bare_receiver_is_the_type(self):
        pf = _parse(b"class Repo { fun local() { Client.static() } }\n")
        edge = _call_edges(pf, "static")[0]
        assert edge.receiver_type == "Client"

    def test_unknown_receiver_abstains(self):
        pf = _parse(b"class Repo { fun local() { repo.query() } }\n")
        assert _call_edges(pf, "query")[0].receiver_type is None

    def test_operator_invoke_bare_call_rewrites_to_declared_type(self):
        pf = _parse(
            b"class FetchUseCase { operator fun invoke(id: Int): String = \"\" }\n"
            b"class Repo {\n"
            b"  val fetch: FetchUseCase = FetchUseCase()\n"
            b"  fun local() { fetch(42) }\n"
            b"}\n"
        )
        assert _call_edges(pf, "FetchUseCase")[0].target_name == "FetchUseCase"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
