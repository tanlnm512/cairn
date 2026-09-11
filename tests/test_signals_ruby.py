"""Ruby parser signal extraction: call/definition arity and receiver pins.

Parsed by hand from small snippets (the golden snapshots are REGENERATED from
parser output, so a systematic drop self-validates — cf.
tests/test_parser_audit_fixes.py). Receiver behavior predates the signal
substrate; its tests here are regression pins.

Contracts:
- Ruby has no named-import syntax: ``require`` loads a file and binds no
  local name, so ``Import.local_alias`` is always None (module nesting
  ``module A::B`` is a scope_resolution, not an import).
- ``Symbol.arity`` counts ``method_parameters`` children: required,
  optional, rest, keyword, hash-splat and destructured params each occupy
  one slot; ``&blk`` (block_parameter) passes the block, not an argument;
  a missing parameter list means zero.
- ``Edge.call_arity`` counts ``argument_list`` children; a ``do``/``{}``
  block is a sibling of the argument_list (never counted) and ``&blk``
  (block_argument) is a block pass-through, not a positional argument.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from cairn.parsers.ruby import RubyParser


def _parse(source: bytes):
    """Parse ``source`` with RubyParser via a temp file. Returns ParsedFile."""
    with tempfile.NamedTemporaryFile(suffix=".rb", delete=False, mode="wb") as f:
        f.write(source)
        path = f.name
    try:
        return RubyParser().parse(path)
    finally:
        Path(path).unlink(missing_ok=True)


def _symbol(pf, name: str):
    return next(s for s in pf.symbols if s.name == name)


def _call_edges(pf, target: str):
    return [e for e in pf.edges if e.kind == "calls" and e.target_name == target]


# ---------------------------------------------------------------------------
# Import aliases (Ruby: none ever — require binds no local name)
# ---------------------------------------------------------------------------

class TestImportAlias:
    def test_require_binds_no_local_alias(self):
        pf = _parse(b'require "set"\n')
        assert [(i.imported_path, i.local_alias) for i in pf.imports] == [("set", None)]


# ---------------------------------------------------------------------------
# Definition-site arity
# ---------------------------------------------------------------------------

class TestDefinitionArity:
    def test_required_params_count(self):
        pf = _parse(b"def combine(a, b)\n  a\nend\n")
        assert _symbol(pf, "combine").arity == 2

    def test_optional_rest_keyword_hash_splat_each_count_one(self):
        pf = _parse(b"def opt(a, b = 1, *rest, key:, **opts)\n  a\nend\n")
        assert _symbol(pf, "opt").arity == 5

    def test_block_parameter_does_not_count(self):
        pf = _parse(b"def with_block(a, &handler)\n  a\nend\n")
        assert _symbol(pf, "with_block").arity == 1

    def test_no_parameter_list_is_zero(self):
        pf = _parse(b"def bare\nend\n")
        assert _symbol(pf, "bare").arity == 0

    def test_destructured_parameter_binds_one_slot(self):
        pf = _parse(b"def point((x, y))\n  x\nend\n")
        assert _symbol(pf, "point").arity == 1

    def test_singleton_method_params_count(self):
        pf = _parse(b"def self.helper(x)\n  x\nend\n")
        assert _symbol(pf, "helper").arity == 1


# ---------------------------------------------------------------------------
# Call-site arity
# ---------------------------------------------------------------------------

class TestCallArity:
    def test_positional_and_keyword_args(self):
        pf = _parse(b"repo.find(1, verbose: true)\n")
        assert _call_edges(pf, "find")[0].call_arity == 2

    def test_parenless_args(self):
        pf = _parse(b'puts "a", "b"\n')
        assert _call_edges(pf, "puts")[0].call_arity == 2

    def test_zero_arg_call(self):
        pf = _parse(b"class App\n  def make\n    Logger.new\n  end\nend\n")
        assert _call_edges(pf, "new")[0].call_arity == 0

    def test_nested_call_args_count_their_own(self):
        pf = _parse(b"foo(bar(1), 2)\n")
        assert _call_edges(pf, "foo")[0].call_arity == 2
        assert _call_edges(pf, "bar")[0].call_arity == 1

    def test_do_block_is_not_an_argument(self):
        pf = _parse(b"items.each do |item|\n  process(item)\nend\n")
        assert _call_edges(pf, "each")[0].call_arity == 0

    def test_brace_block_is_not_an_argument(self):
        pf = _parse(b"list.map { |y| y * 2 }\n")
        assert _call_edges(pf, "map")[0].call_arity == 0

    def test_block_pass_is_not_positional(self):
        pf = _parse(b"run(a, &blk)\n")
        assert _call_edges(pf, "run")[0].call_arity == 1

    def test_splat_and_hash_splat_occupy_one_slot_each(self):
        pf = _parse(b"run(*args, **opts)\n")
        assert _call_edges(pf, "run")[0].call_arity == 2


# ---------------------------------------------------------------------------
# Receiver pins (behavior predates the signal substrate — regression guard)
# ---------------------------------------------------------------------------

class TestReceiverPins:
    def test_constant_receiver_infers_type(self):
        pf = _parse(b"class App\n  def make\n    Logger.new\n  end\nend\n")
        edge = _call_edges(pf, "new")[0]
        assert edge.receiver_type == "Logger"

    def test_lowercase_receiver_abstains(self):
        pf = _parse(b"def run(user)\n  user.reload\nend\n")
        edge = _call_edges(pf, "reload")[0]
        assert edge.receiver_type is None

    def test_chained_call_records_every_link(self):
        pf = _parse(b"repo.find(1).update(attrs)\n")
        find = _call_edges(pf, "find")[0]
        update = _call_edges(pf, "update")[0]
        assert (find.receiver_type, find.call_arity) == (None, 1)
        assert (update.receiver_type, update.call_arity) == (None, 1)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
