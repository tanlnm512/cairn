"""Go parser signal extraction: import aliases, definition/call arity, and
receiver pins.

Parsed by hand from small snippets (the golden snapshots are REGENERATED from
parser output, so a systematic drop self-validates — cf.
tests/test_parser_audit_fixes.py). Receiver behavior predates the signal
substrate; its tests here are regression pins.

Contracts:
- ``import qux "path"`` records ``local_alias='qux'``; blank (``_``) and dot
  (``.``) imports record no alias — a dot import binds unqualified names (an
  alias would misdescribe it) and a blank import binds nothing.
- ``Symbol.arity`` counts parameters (receiver excluded; multi-name
  declarations count each name; unnamed count 1); a variadic parameter makes
  the count unknowable → None (abstain).
- ``Edge.call_arity`` counts the call's arguments; a spread argument
  (``f(xs...)``) → None.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cairn.parsers.go import GoParser


def _parse(source: bytes):
    """Parse ``source`` with GoParser via a temp file. Returns ParsedFile."""
    with tempfile.NamedTemporaryFile(suffix=".go", delete=False, mode="wb") as f:
        f.write(source)
        path = f.name
    try:
        return GoParser().parse(path)
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
    def test_named_import_records_alias(self):
        pf = _parse(b'package m\n\nimport qux "example.com/qux"\n')
        assert [(i.imported_path, i.local_alias) for i in pf.imports] == [
            ("example.com/qux", "qux")
        ]

    def test_plain_import_records_no_alias(self):
        pf = _parse(b'package m\n\nimport "fmt"\n')
        assert [(i.imported_path, i.local_alias) for i in pf.imports] == [
            ("fmt", None)
        ]

    def test_blank_import_records_no_alias(self):
        pf = _parse(b'package m\n\nimport _ "example.com/driver"\n')
        assert [(i.imported_path, i.local_alias) for i in pf.imports] == [
            ("example.com/driver", None)
        ]

    def test_dot_import_records_no_alias(self):
        pf = _parse(b'package m\n\nimport . "example.com/lib"\n')
        assert [(i.imported_path, i.local_alias) for i in pf.imports] == [
            ("example.com/lib", None)
        ]

    def test_grouped_import_block(self):
        pf = _parse(
            b"""package m

import (
\t"fmt"
\tqux "example.com/qux"
\t_ "example.com/blank"
\t. "example.com/dot"
)
"""
        )
        assert {(i.imported_path, i.local_alias) for i in pf.imports} == {
            ("fmt", None),
            ("example.com/qux", "qux"),
            ("example.com/blank", None),
            ("example.com/dot", None),
        }


# ---------------------------------------------------------------------------
# Definition-site arity
# ---------------------------------------------------------------------------

class TestDefinitionArity:
    def test_one_param_per_declaration(self):
        pf = _parse(b"package m\n\nfunc add(a int, b string) {}\n")
        assert _symbol(pf, "add").arity == 2

    def test_multi_name_declaration_counts_each_name(self):
        pf = _parse(b"package m\n\nfunc pair(a, b int, c string) {}\n")
        assert _symbol(pf, "pair").arity == 3

    def test_unnamed_params_count_one_each(self):
        pf = _parse(b"package m\n\nfunc two(int, string) {}\n")
        assert _symbol(pf, "two").arity == 2

    def test_no_params_is_zero(self):
        pf = _parse(b"package m\n\nfunc zero() {}\n")
        assert _symbol(pf, "zero").arity == 0

    def test_variadic_param_abstains(self):
        pf = _parse(b"package m\n\nfunc v(prefix string, rest ...int) {}\n")
        assert _symbol(pf, "v").arity is None

    def test_func_type_param_counts_one(self):
        pf = _parse(b"package m\n\nfunc withfn(cb func(int) int, n int) {}\n")
        assert _symbol(pf, "withfn").arity == 2

    def test_method_arity_excludes_receiver(self):
        pf = _parse(
            b"package m\n\ntype Server struct{}\n\n"
            b"func (s *Server) Handle(req string, n int) {}\n"
        )
        assert _symbol(pf, "Handle").arity == 2

    def test_method_variadic_abstains(self):
        pf = _parse(
            b"package m\n\ntype Server struct{}\n\n"
            b"func (s *Server) V(rest ...int) {}\n"
        )
        assert _symbol(pf, "V").arity is None


# ---------------------------------------------------------------------------
# Call-site arity
# ---------------------------------------------------------------------------

class TestCallArity:
    def test_zero_args(self):
        pf = _parse(b"package m\n\nfunc f() {}\n\nfunc use() { f() }\n")
        assert _call_edges(pf, "f")[0].call_arity == 0

    def test_two_args(self):
        pf = _parse(b'package m\n\nfunc use() { f(1, "x") }\n')
        assert _call_edges(pf, "f")[0].call_arity == 2

    def test_nested_call_is_one_argument(self):
        pf = _parse(b"package m\n\nfunc use() { inner(outer(1), 2) }\n")
        assert _call_edges(pf, "inner")[0].call_arity == 2
        assert _call_edges(pf, "outer")[0].call_arity == 1

    def test_spread_argument_abstains(self):
        pf = _parse(
            b"""package m

func use() {
\txs := []int{1, 2}
\tf(xs...)
}
"""
        )
        assert _call_edges(pf, "f")[0].call_arity is None

    def test_explicit_variadic_call_counts_arguments(self):
        pf = _parse(b'package m\n\nfunc use() { f("p", 1, 2, 3) }\n')
        assert _call_edges(pf, "f")[0].call_arity == 4


# ---------------------------------------------------------------------------
# Receiver pins (behavior predates the signal substrate — regression guard)
# ---------------------------------------------------------------------------

class TestReceiverPins:
    def test_method_parent_scope_is_receiver_type(self):
        pf = _parse(
            b"package m\n\ntype Server struct{}\n\n"
            b"func (s *Server) Handle(req string) {}\n"
        )
        sym = _symbol(pf, "Handle")
        assert sym.kind == "method"
        assert sym.parent_scope == "Server"

    def test_capitalized_call_receiver_emits_type(self):
        pf = _parse(b"package m\n\nfunc use() { Server.New() }\n")
        edge = _call_edges(pf, "New")[0]
        assert edge.receiver_type == "Server"

    def test_lowercase_receiver_stays_none(self):
        pf = _parse(b"package m\n\ntype Server struct{}\n\nfunc use() { s := &Server{}; s.Handle() }\n")
        assert _call_edges(pf, "Handle")[0].receiver_type is None

    def test_package_qualified_call_target_is_member(self):
        pf = _parse(b'package m\n\nfunc use() { fmt.Println("a", "b") }\n')
        edge = _call_edges(pf, "Println")[0]
        assert edge.receiver_type is None
        assert edge.call_arity == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
