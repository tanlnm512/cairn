"""C++ parser signal tests: ``Edge.call_arity`` / ``Edge.receiver_type`` /
``Symbol.arity``.

Pins the tree-sitter-cpp 0.23.4 ``call_expression`` contract (research F2.2):
``function`` is a ``field_expression`` (fields ``argument``/``field``) for
member calls, or a ``qualified_identifier`` (fields ``scope``/``name``) for
namespace-qualified calls — ``scope`` is the receiver signal. ``arguments``
is the ``argument_list`` whose named children are the call-site arguments.
Definition arity counts ``function_declarator`` -> ``parameter_list``
parameters; defaults, varargs, and C-style unspecified ``()`` stay None
(D-005). Receiver typing runs through the shared scope-ordered tracker with
shadow-abstain (D-006).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from cairn.parsers.c_family import CppParser


def _parse(source: bytes, suffix: str = ".cpp"):
    """Parse ``source`` with CppParser via a temp file. Returns ParsedFile."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="wb") as f:
        f.write(source)
        path = f.name
    try:
        return CppParser().parse(path)
    finally:
        Path(path).unlink(missing_ok=True)


def _calls(pf):
    return [e for e in pf.edges if e.kind == "calls"]


def _edge(pf, target):
    matches = [e for e in _calls(pf) if e.target_name == target]
    assert len(matches) == 1, f"expected exactly one '{target}' calls edge, got {matches}"
    return matches[0]


def _symbol(pf, name):
    matches = [s for s in pf.symbols if s.name == name]
    assert len(matches) == 1, f"expected exactly one '{name}' symbol, got {matches}"
    return matches[0]


class TestCallArity:
    def test_bare_call_zero_and_two_args(self):
        pf = _parse(b"void run() {\n  helper();\n  pair(1, 2);\n}\n")
        assert _edge(pf, "helper").call_arity == 0
        assert _edge(pf, "pair").call_arity == 2

    def test_qualified_and_template_call_arity(self):
        pf = _parse(
            b"void run() {\n"
            b"  Math::abs(-1);\n"
            b"  max_val<int>(1, 2);\n"
            b"  std::make_unique<Engine>(5);\n"
            b"}\n"
        )
        assert _edge(pf, "abs").call_arity == 1
        assert _edge(pf, "max_val").call_arity == 2
        assert _edge(pf, "make_unique").call_arity == 1


class TestReceiverType:
    def test_member_call_receiver_from_parameter(self):
        pf = _parse(b"void run(Engine *e) {\n  e->getPower(1, 2);\n}\n")
        edge = _edge(pf, "getPower")
        assert edge.receiver_type == "Engine"
        assert edge.call_arity == 2

    def test_member_call_receiver_from_local_declaration(self):
        pf = _parse(b"void run() {\n  Engine obj;\n  obj.method(1);\n}\n")
        assert _edge(pf, "method").receiver_type == "Engine"

    def test_qualified_call_scope_receiver(self):
        """``Math::abs(-1)`` -> receiver ``Math``; lowercase namespaces stay None."""
        pf = _parse(b"void run() {\n  Math::abs(-1);\n  app::helper(1, 2);\n}\n")
        assert _edge(pf, "abs").receiver_type == "Math"
        qualified = _edge(pf, "helper")
        assert qualified.receiver_type is None
        assert qualified.call_arity == 2

    def test_bare_and_template_calls_have_no_receiver(self):
        pf = _parse(b"void run() {\n  helper();\n  max_val<int>(1, 2);\n}\n")
        assert _edge(pf, "helper").receiver_type is None
        assert _edge(pf, "max_val").receiver_type is None

    def test_this_call_receiver_is_enclosing_class(self):
        pf = _parse(
            b"class Bot {\n"
            b"public:\n"
            b"    void go() { this->step(1); }\n"
            b"};\n"
        )
        edge = _edge(pf, "step")
        assert edge.receiver_type == "Bot"
        assert edge.call_arity == 1

    def test_untracked_lowercase_receiver_is_none(self):
        pf = _parse(b"void run() {\n  cfg.helper(1);\n}\n")
        edge = _edge(pf, "helper")
        assert edge.receiver_type is None
        assert edge.call_arity == 1

    def test_chained_call_receiver_is_none(self):
        """``c->inner()->go(1, 2)`` — the outer receiver is a call, not a name."""
        pf = _parse(b"void run(Cfg *c) {\n  c->inner()->go(1, 2);\n}\n")
        assert _edge(pf, "inner").receiver_type == "Cfg"
        outer = _edge(pf, "go")
        assert outer.receiver_type is None
        assert outer.call_arity == 2

    def test_shadowed_receiver_abstains_then_recovers(self):
        """D-006: an inner shadow poisons the name until its scope pops."""
        pf = _parse(
            b"void run(Engine *e) {\n"
            b"  if (1) { Other e; e->shadowed(1); }\n"
            b"  e->outer(2);\n"
            b"}\n"
        )
        assert _edge(pf, "shadowed").receiver_type is None
        assert _edge(pf, "outer").receiver_type == "Engine"


class TestDefinitionArity:
    def test_function_arities(self):
        pf = _parse(
            b"int add(int a, int b) { return a + b; }\n"
            b"int g(int a, int b = 0) {}\n"
            b"int h(int a, ...) {}\n"
            b"int k(void) {}\n"
            b"int m() {}\n"
        )
        assert _symbol(pf, "add").arity == 2
        assert _symbol(pf, "g").arity is None  # default argument -> unknown
        assert _symbol(pf, "h").arity is None  # varargs -> unknown
        assert _symbol(pf, "k").arity == 0  # (void) is zero parameters
        assert _symbol(pf, "m").arity == 0  # C++ () is zero parameters

    def test_method_arity(self):
        pf = _parse(b"class A {\npublic:\n    void m(int x, int y) {}\n};\n")
        sym = _symbol(pf, "m")
        assert sym.kind == "method"
        assert sym.arity == 2

    def test_template_function_arity(self):
        pf = _parse(
            b"template<typename T>\nT max_val(T a, T b) { return a > b ? a : b; }\n"
        )
        assert _symbol(pf, "max_val").arity == 2
