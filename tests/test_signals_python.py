"""Signal tests for the Python parser.

Covers the three enrichment signals on ParsedFile rows:
- Import.local_alias + normalized paths for aliased imports (D-003). Plain
  (non-aliased) imports keep the verbatim statement text: the builder's
  import-base parser (``_import_module_bases``) consumes that shape to derive
  module bases, so only the alias-bearing statements — whose raw text hides
  the alias from the resolver — are normalized to dotted paths.
- Edge.receiver_type for attribute calls via the scope-ordered var->type
  tracker (D-004/D-006): only bare-identifier receivers with a known in-file
  binding (class name, ``self``/``cls``, typed or constructor assignment,
  annotated parameter) get a type; everything else abstains to None.
- Symbol.arity at def sites and Edge.call_arity at call sites (D-005),
  conservative per D-005: defaults, varargs, and an unrecognized receiver-self
  parameter yield None instead of a count.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cairn.parsers.python_parser import PythonParser


def _parse(source: bytes):
    """Parse ``source`` with PythonParser via a temp file. Returns ParsedFile."""
    with tempfile.NamedTemporaryFile(suffix=".py", delete=False, mode="wb") as f:
        f.write(source)
        path = f.name
    try:
        return PythonParser().parse(path)
    finally:
        Path(path).unlink(missing_ok=True)


def _imports(pf):
    return [(i.imported_path, i.local_alias) for i in pf.imports]


def _call_edge(pf, target: str):
    edges = [e for e in pf.edges if e.kind == "calls" and e.target_name == target]
    assert edges, f"no calls edge to {target!r} in {[e.target_name for e in pf.edges]}"
    return edges[0]


def _symbol(pf, name: str):
    syms = [s for s in pf.symbols if s.name == name]
    assert syms, f"no symbol {name!r} in {[s.name for s in pf.symbols]}"
    return syms[0]


# ---------------------------------------------------------------------------
# Import signals — normalized dotted path + local_alias on alias-bearing
# statements (D-003); plain statements keep the statement text the builder's
# module-base parser consumes.
# ---------------------------------------------------------------------------

class TestImportSignals:
    def test_plain_imports_keep_statement_text(self):
        pf = _parse(b"import os\nfrom enum import Enum\nfrom util import helper\n")
        assert _imports(pf) == [
            ("import os", None),
            ("from enum import Enum", None),
            ("from util import helper", None),
        ]

    def test_aliased_plain_import_normalizes_path(self):
        pf = _parse(b"import x.y as z\n")
        assert _imports(pf) == [("x.y", "z")]

    def test_aliased_from_import_normalizes_path(self):
        pf = _parse(b"from util import helper as h\n")
        assert _imports(pf) == [("util.helper", "h")]

    def test_deep_module_alias_keeps_full_dotted_path(self):
        pf = _parse(b"from util.deep.mod import Thing as T\n")
        assert _imports(pf) == [("util.deep.mod.Thing", "T")]

    def test_mixed_name_list_splits_per_name(self):
        pf = _parse(b"from util import a, b as c\n")
        assert _imports(pf) == [("util.a", None), ("util.b", "c")]

    def test_wildcard_import_records_no_alias(self):
        # Re-export shape: no local binding exists, so no alias (D-003
        # abstention); the statement text stays as stored today.
        pf = _parse(b"from util import *\n")
        assert _imports(pf) == [("from util import *", None)]

    def test_relative_aliased_import_strips_dot_markers(self):
        pf = _parse(b"from .rel import name as rn\n")
        assert _imports(pf) == [("rel.name", "rn")]


# ---------------------------------------------------------------------------
# Receiver signals — tracker-backed inference on bare-identifier receivers.
# ---------------------------------------------------------------------------

class TestReceiverType:
    def test_self_receiver_resolves_to_enclosing_class(self):
        pf = _parse(
            b"class User:\n"
            b"    def process(self):\n"
            b"        return self.helper_call(1)\n"
        )
        assert _call_edge(pf, "helper_call").receiver_type == "User"

    def test_annotated_assignment_receiver(self):
        pf = _parse(
            b"class User:\n    pass\n\n\n"
            b"def f():\n    u: User = User()\n    return u.save()\n"
        )
        assert _call_edge(pf, "save").receiver_type == "User"

    def test_constructor_assignment_receiver(self):
        pf = _parse(
            b"class User:\n    pass\n\n\n"
            b"def f():\n    u = User()\n    return u.save()\n"
        )
        assert _call_edge(pf, "save").receiver_type == "User"

    def test_annotated_parameter_receiver(self):
        pf = _parse(
            b"class User:\n    pass\n\n\n"
            b"def f(u: User):\n    return u.save()\n"
        )
        assert _call_edge(pf, "save").receiver_type == "User"

    def test_class_name_receiver(self):
        pf = _parse(
            b"class Factory:\n"
            b"    @staticmethod\n"
            b"    def build():\n        pass\n\n\n"
            b"def f():\n    return Factory.build()\n"
        )
        assert _call_edge(pf, "build").receiver_type == "Factory"

    def test_unknown_receiver_abstains(self):
        pf = _parse(b"def f():\n    return helper.run()\n")
        assert _call_edge(pf, "run").receiver_type is None

    def test_external_capitalized_receiver_abstains(self):
        # Tracker-only contract: a receiver with no in-file binding is not a
        # type guess (FR-009).
        pf = _parse(b"def f():\n    return Client.get()\n")
        assert _call_edge(pf, "get").receiver_type is None

    def test_chained_receiver_abstains(self):
        pf = _parse(b"def f(a):\n    return a.b.c()\n")
        assert _call_edge(pf, "c").receiver_type is None

    def test_reassigned_receiver_abstains(self):
        # D-006: reassignment to a different type poisons the binding.
        pf = _parse(
            b"class User:\n    pass\n\n\nclass Order:\n    pass\n\n\n"
            b"def f():\n    u = User()\n    u = Order()\n    return u.save()\n"
        )
        assert _call_edge(pf, "save").receiver_type is None

    def test_scope_pop_restores_outer_binding(self):
        # A poisoned inner-scope binding must not leak past the function.
        pf = _parse(
            b"class User:\n"
            b"    def go(self):\n"
            b"        return self.save()\n\n\n"
            b"class Order:\n    pass\n\n\n"
            b"def poisoned():\n"
            b"    u = User()\n"
            b"    u = Order()\n"
            b"    return u.save()\n"
        )
        assert _call_edge(pf, "save").receiver_type == "User"


# ---------------------------------------------------------------------------
# Definition arity (Symbol.arity) — conservative counts (D-005).
# ---------------------------------------------------------------------------

class TestSymbolArity:
    def test_plain_function_params(self):
        pf = _parse(b"def f(a, b: int):\n    return a\n")
        assert _symbol(pf, "f").arity == 2

    def test_zero_params(self):
        pf = _parse(b"def f():\n    return 1\n")
        assert _symbol(pf, "f").arity == 0

    def test_default_parameter_yields_none(self):
        pf = _parse(b"def f(a, b=1):\n    return a\n")
        assert _symbol(pf, "f").arity is None

    def test_typed_default_parameter_yields_none(self):
        pf = _parse(b"def f(a: int = 1):\n    return a\n")
        assert _symbol(pf, "f").arity is None

    def test_varargs_yield_none(self):
        pf = _parse(b"def f(*args, **kw):\n    return 1\n")
        assert _symbol(pf, "f").arity is None

    def test_method_excludes_self(self):
        pf = _parse(b"class C:\n    def m(self, x):\n        return x\n")
        assert _symbol(pf, "m").arity == 1

    def test_classmethod_excludes_cls(self):
        pf = _parse(
            b"class C:\n"
            b"    @classmethod\n"
            b"    def k(cls, x):\n        return x\n"
        )
        assert _symbol(pf, "k").arity == 1

    def test_method_with_unrecognized_receiver_self_yields_none(self):
        pf = _parse(b"class C:\n    def m(ctx, x):\n        return x\n")
        assert _symbol(pf, "m").arity is None

    def test_staticmethod_counts_all_params(self):
        pf = _parse(
            b"class C:\n"
            b"    @staticmethod\n"
            b"    def s(a, b):\n        return a\n"
        )
        assert _symbol(pf, "s").arity == 2

    def test_class_symbol_has_no_arity(self):
        pf = _parse(b"class C:\n    pass\n")
        assert _symbol(pf, "C").arity is None


# ---------------------------------------------------------------------------
# Call-site arity (Edge.call_arity) — argument_list count; splats abstain.
# ---------------------------------------------------------------------------

class TestCallArity:
    def test_zero_argument_call(self):
        pf = _parse(b"def f():\n    return g()\n")
        assert _call_edge(pf, "g").call_arity == 0

    def test_positional_and_keyword_arguments(self):
        pf = _parse(b"def f(a):\n    return g(a, b=1)\n")
        assert _call_edge(pf, "g").call_arity == 2

    def test_nested_call_counts_as_one_argument(self):
        pf = _parse(b"def f(a):\n    return g(h(a))\n")
        assert _call_edge(pf, "g").call_arity == 1

    def test_splat_argument_yields_none(self):
        pf = _parse(b"def f(args):\n    return g(*args)\n")
        assert _call_edge(pf, "g").call_arity is None

    def test_attribute_call_arity(self):
        pf = _parse(
            b"class User:\n"
            b"    def process(self):\n"
            b"        return self.helper_call(1, 2)\n"
        )
        assert _call_edge(pf, "helper_call").call_arity == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
