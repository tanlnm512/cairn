"""Signal enrichment tests for the JS-family parsers (typescript.py).

One module for both languages the parser file serves: TypeScriptParser
(.ts) and JavaScriptParser (.js). Covers the three parser-side signals —
Import.local_alias, Edge.receiver_type / Edge.call_arity, Symbol.arity —
asserting the parsed dataclass fields directly on small source snippets.
Abstention pins (shapes that must stay None) are part of the contract: a
signal the grammar cannot express unambiguously degrades to None, never a
guess.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from cairn.parsers.typescript import JavaScriptParser, TypeScriptParser


def _parse(parser_cls, source: bytes, suffix: str):
    """Parse ``source`` with ``parser_cls`` via a temp file. Returns ParsedFile."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="wb") as f:
        f.write(source)
        path = f.name
    try:
        return parser_cls().parse(path)
    finally:
        Path(path).unlink(missing_ok=True)


def _symbol(pf, name: str):
    return next(s for s in pf.symbols if s.name == name)


def _call_edges(pf):
    return [e for e in pf.edges if e.kind == "calls"]


def _edge(pf, target: str):
    return next(e for e in _call_edges(pf) if e.target_name == target)


# ---------------------------------------------------------------------------
# Import.local_alias
# ---------------------------------------------------------------------------

class TestImportAliasTS:
    def test_aliased_named_import_records_alias(self):
        pf = _parse(
            TypeScriptParser,
            b"import { helper as h } from './util';\n",
            ".ts",
        )
        assert pf.imports[0].local_alias == "h"

    def test_namespace_import_records_alias(self):
        pf = _parse(TypeScriptParser, b"import * as ns from './mod';\n", ".ts")
        assert pf.imports[0].local_alias == "ns"

    def test_mixed_default_and_single_alias(self):
        pf = _parse(
            TypeScriptParser, b"import d, { a as x } from './mod';\n", ".ts"
        )
        assert pf.imports[0].local_alias == "x"

    def test_unaliased_named_import_records_no_alias(self):
        pf = _parse(TypeScriptParser, b"import { helper } from './util';\n", ".ts")
        assert pf.imports[0].local_alias is None

    def test_default_import_records_no_alias(self):
        pf = _parse(TypeScriptParser, b"import d from './mod';\n", ".ts")
        assert pf.imports[0].local_alias is None

    def test_two_aliases_in_one_statement_abstain(self):
        pf = _parse(
            TypeScriptParser, b"import { a as x, b as y } from './mod';\n", ".ts"
        )
        assert pf.imports[0].local_alias is None

    def test_export_reexport_records_no_alias(self):
        """Re-exports carry no import-side alias signal (D-003 abstention)."""
        pf = _parse(
            TypeScriptParser,
            b"export { a as re } from './x';\nimport { b as y } from './m';\n",
            ".ts",
        )
        assert len(pf.imports) == 1
        assert pf.imports[0].local_alias == "y"


class TestImportAliasJS:
    def test_aliased_named_import_records_alias(self):
        pf = _parse(
            JavaScriptParser, b"import { helper as h } from './util';\n", ".js"
        )
        assert pf.imports[0].local_alias == "h"

    def test_namespace_import_records_alias(self):
        pf = _parse(JavaScriptParser, b"import * as ns from './mod';\n", ".js")
        assert pf.imports[0].local_alias == "ns"


# ---------------------------------------------------------------------------
# Edge.receiver_type
# ---------------------------------------------------------------------------

class TestReceiverTypeTS:
    def test_receiver_from_new_initializer(self):
        pf = _parse(
            TypeScriptParser,
            b"const u = new User();\nu.save(1);\n",
            ".ts",
        )
        assert _edge(pf, "save").receiver_type == "User"

    def test_receiver_from_type_annotation(self):
        pf = _parse(
            TypeScriptParser,
            b"let u: User = load();\nu.save();\n",
            ".ts",
        )
        assert _edge(pf, "save").receiver_type == "User"

    def test_receiver_from_typed_parameter(self):
        pf = _parse(
            TypeScriptParser,
            b"function f(u: User) { u.save(); }\n",
            ".ts",
        )
        assert _edge(pf, "save").receiver_type == "User"

    def test_this_receiver_is_enclosing_class(self):
        pf = _parse(
            TypeScriptParser,
            b"class A {\n  m() { this.helper(1); }\n}\n",
            ".ts",
        )
        assert _edge(pf, "helper").receiver_type == "A"

    def test_capitalized_receiver_is_static_call(self):
        pf = _parse(TypeScriptParser, b"Foo.create();\n", ".ts")
        assert _edge(pf, "create").receiver_type == "Foo"

    def test_reassignment_to_different_type_abstains(self):
        pf = _parse(
            TypeScriptParser,
            b"let u = new User();\nu = new Admin();\nu.go();\n",
            ".ts",
        )
        assert _edge(pf, "go").receiver_type is None

    def test_shadowed_parameter_abstains(self):
        pf = _parse(
            TypeScriptParser,
            b"function f(u: User) {\n  const u = new Admin();\n  u.go();\n}\n",
            ".ts",
        )
        assert _edge(pf, "go").receiver_type is None

    def test_chained_call_inner_only(self):
        pf = _parse(
            TypeScriptParser,
            b"const u = new User();\nu.fetch().done(2);\n",
            ".ts",
        )
        assert _edge(pf, "fetch").receiver_type == "User"
        assert _edge(pf, "done").receiver_type is None

    def test_optional_chain_receiver(self):
        pf = _parse(
            TypeScriptParser,
            b"const u = new User();\nu?.save(1);\n",
            ".ts",
        )
        assert _edge(pf, "save").receiver_type == "User"

    def test_untracked_lowercase_receiver_is_none(self):
        pf = _parse(TypeScriptParser, b"helper.fetch(1);\n", ".ts")
        assert _edge(pf, "fetch").receiver_type is None


class TestReceiverTypeJS:
    def test_receiver_from_new_initializer(self):
        pf = _parse(
            JavaScriptParser,
            b"const s = new Store();\ns.fetch(1);\n",
            ".js",
        )
        assert _edge(pf, "fetch").receiver_type == "Store"

    def test_this_receiver_is_enclosing_class(self):
        pf = _parse(
            JavaScriptParser,
            b"class B {\n  run(a) { this.step(a); }\n}\n",
            ".js",
        )
        assert _edge(pf, "step").receiver_type == "B"


# ---------------------------------------------------------------------------
# Symbol.arity / Edge.call_arity
# ---------------------------------------------------------------------------

class TestArityTS:
    def test_function_arity(self):
        pf = _parse(TypeScriptParser, b"function add(a: number, b: number) {}\n", ".ts")
        assert _symbol(pf, "add").arity == 2

    def test_method_zero_arity(self):
        pf = _parse(TypeScriptParser, b"class C {\n  process(): string { return ''; }\n}\n", ".ts")
        assert _symbol(pf, "process").arity == 0

    def test_arrow_function_arity(self):
        pf = _parse(TypeScriptParser, b"const mul = (a, b) => a * b;\n", ".ts")
        assert _symbol(pf, "mul").arity == 2

    def test_arrow_shorthand_arity(self):
        pf = _parse(TypeScriptParser, b"const id = x => x;\n", ".ts")
        assert _symbol(pf, "id").arity == 1

    def test_rest_parameter_abstains(self):
        pf = _parse(TypeScriptParser, b"function f(a, ...rest) {}\n", ".ts")
        assert _symbol(pf, "f").arity is None

    def test_default_parameter_abstains(self):
        pf = _parse(TypeScriptParser, b"function g(a, b = 1) {}\n", ".ts")
        assert _symbol(pf, "g").arity is None

    def test_optional_parameter_abstains(self):
        pf = _parse(TypeScriptParser, b"function h(a?: string) {}\n", ".ts")
        assert _symbol(pf, "h").arity is None

    def test_call_arity(self):
        pf = _parse(TypeScriptParser, b"add(1, 2);\nnoop();\n", ".ts")
        assert _edge(pf, "add").call_arity == 2
        assert _edge(pf, "noop").call_arity == 0

    def test_new_expression_call_arity(self):
        pf = _parse(TypeScriptParser, b"new User(1);\n", ".ts")
        assert _edge(pf, "User").call_arity == 1

    def test_spread_argument_call_arity_abstains(self):
        pf = _parse(TypeScriptParser, b"f(...xs);\n", ".ts")
        assert _edge(pf, "f").call_arity is None


class TestArityJS:
    def test_constructor_arity(self):
        pf = _parse(
            JavaScriptParser,
            b"class D {\n  constructor(a, b) { this.run(a); }\n  run(a) {}\n}\n",
            ".js",
        )
        assert _symbol(pf, "constructor").arity == 2
        assert _symbol(pf, "run").arity == 1

    def test_call_arity(self):
        pf = _parse(JavaScriptParser, b"store.fetch(1, 2);\n", ".js")
        assert _edge(pf, "fetch").call_arity == 2
