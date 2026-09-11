"""C parser signal tests: ``Edge.call_arity`` / ``Edge.receiver_type`` /
``Symbol.arity``.

Plain C has no imports (F2.1), so there is no alias signal to assert. Per
research F2.2, tree-sitter-c 0.23.4 ``call_expression.function`` is a plain
expression with no receiver field — the only member-call shape is a callee
``field_expression`` (fields ``argument``/``field``), as in ``p->cb(1)`` or
``obj.fn(1, 2)``. Receiver typing runs through the shared scope-ordered
tracker (params + locals), shadow-abstain per D-006; definition arity counts
``function_declarator`` -> ``parameter_list`` parameters, with varargs and
C's unspecified ``()`` staying None (D-005).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from cairn.parsers.c_family import CParser


def _parse(source: bytes, suffix: str = ".c"):
    """Parse ``source`` with CParser via a temp file. Returns ParsedFile."""
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False, mode="wb") as f:
        f.write(source)
        path = f.name
    try:
        return CParser().parse(path)
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
    def test_bare_call_arity(self):
        pf = _parse(b'int main(void) {\n  printf("hi");\n  pair(1, 2);\n}\n')
        assert _edge(pf, "printf").call_arity == 1
        assert _edge(pf, "pair").call_arity == 2

    def test_member_call_arity(self):
        pf = _parse(b"void run(struct CB *p) {\n  p->cb(1);\n}\n")
        assert _edge(pf, "cb").call_arity == 1

    def test_function_pointer_call_arity(self):
        pf = _parse(b"void g(int (*cb)(int)) {\n  cb(1);\n}\n")
        assert _edge(pf, "cb").call_arity == 1


class TestReceiverType:
    def test_member_call_receiver_from_struct_parameter(self):
        pf = _parse(b"void run(struct CB *p) {\n  p->cb(1);\n}\n")
        edge = _edge(pf, "cb")
        assert edge.receiver_type == "CB"
        assert edge.call_arity == 1

    def test_member_call_receiver_from_local_struct(self):
        pf = _parse(b"void run(void) {\n  struct Point p;\n  p.fn(1, 2);\n}\n")
        edge = _edge(pf, "fn")
        assert edge.receiver_type == "Point"
        assert edge.call_arity == 2

    def test_untracked_receiver_is_none(self):
        pf = _parse(b"void run(void) {\n  obj.fn(1);\n}\n")
        edge = _edge(pf, "fn")
        assert edge.receiver_type is None
        assert edge.call_arity == 1

    def test_bare_call_has_no_receiver(self):
        pf = _parse(b"void run(void) {\n  helper(1);\n}\n")
        assert _edge(pf, "helper").receiver_type is None

    def test_chained_call_receiver_is_none(self):
        """``c->inner()->go(1, 2)`` — the outer receiver is a call, not a name."""
        pf = _parse(b"void run(struct Cfg *c) {\n  c->inner()->go(1, 2);\n}\n")
        assert _edge(pf, "inner").receiver_type == "Cfg"
        outer = _edge(pf, "go")
        assert outer.receiver_type is None
        assert outer.call_arity == 2

    def test_shadowed_receiver_abstains_then_recovers(self):
        """D-006: an inner shadow poisons the name until its scope pops."""
        pf = _parse(
            b"void run(struct CB *p) {\n"
            b"  if (1) { struct Other p; p->shadowed(1); }\n"
            b"  p->outer(2);\n"
            b"}\n"
        )
        assert _edge(pf, "shadowed").receiver_type is None
        assert _edge(pf, "outer").receiver_type == "CB"


class TestDefinitionArity:
    def test_function_arities(self):
        pf = _parse(
            b"int add(int a, int b) { return a + b; }\n"
            b"int h(int a, ...) {}\n"
            b"int k(void) {}\n"
            b"int m() {}\n"
        )
        assert _symbol(pf, "add").arity == 2
        assert _symbol(pf, "h").arity is None  # varargs -> unknown
        assert _symbol(pf, "k").arity == 0  # (void) is zero parameters
        assert _symbol(pf, "m").arity is None  # C () leaves the count unspecified

    def test_struct_symbols_have_no_arity(self):
        pf = _parse(b"struct Point {\n    int x;\n};\nint use(void) { return 1; }\n")
        assert _symbol(pf, "Point").arity is None
