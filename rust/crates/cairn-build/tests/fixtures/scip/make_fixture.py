#!/usr/bin/env python3
"""Regenerate sample.scip: a small synthetic SCIP index exercising every
importer join shape. Dev-only; the checked-in bytes are produced by the
Python reference runtime (cairn.parsers._scip_pb2), so the Rust prost
decoder must consume them unchanged.

Run from the repo root:
    .venv/bin/python rust/crates/cairn-build/tests/fixtures/scip/make_fixture.py

Scenario (spans are 0-based SCIP ranges; the paired Rust test seeds the .kg
with 1-based symbol spans that join onto them):

    pkg/util.py   add(17)/helper(17) definitions at lines 1 and 5, plus a
                  local definition (dropped for other documents by the
                  local-symbol filter)
    pkg/app.py    main(17) def line 3; Klass(7) def line 7; method(26) def
                  line 8; local def line 4; references: add@4 (call),
                  helper@10 (call), method@8 (call), ghost@4 (unresolved
                  ref), local@4 (exact ref), add via deprecated 4-element
                  range@4 (call), no-range occurrence (unjoined),
                  out-of-symbol line 20 (unjoined), external symbol@11
                  (call, unresolved)
    gen/extra.py  not present in the graph -> skipped document
"""
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "src"))

from cairn.parsers import _scip_pb2 as scip  # noqa: E402

ROLE_DEFINITION = 1
KIND_FUNCTION = 17
KIND_METHOD = 26
KIND_CLASS = 7

ADD = "scip-python pypi util 1.0.0/pkg/util.py/add( f)."
HELPER = "scip-python pypi util 1.0.0/pkg/util.py/helper( f)."
MAIN = "scip-python pypi app 1.0.0/pkg/app.py/main( f)."
KLASS = "scip-python pypi app 1.0.0/pkg/app.py/Klass."
METHOD = "scip-python pypi app 1.0.0/pkg/app.py/Klass/method( self)."
GHOST = "scip-python pypi app 1.0.0/pkg/app.py/ghost( f)."
LOCAL = "local pkg/app.py:4:9-4:14"
EXTERNAL = "scip-npm @scope/ext 2.0.0/ext/external( f)."


def def_occ(symbol, line, start, end):
    occ = scip.Occurrence(symbol=symbol, symbol_roles=ROLE_DEFINITION)
    occ.single_line_range.line = line
    occ.single_line_range.start_character = start
    occ.single_line_range.end_character = end
    return occ


def ref_occ(symbol, line, start, end):
    occ = scip.Occurrence(symbol=symbol)
    occ.single_line_range.line = line
    occ.single_line_range.start_character = start
    occ.single_line_range.end_character = end
    return occ


def build_index():
    index = scip.Index()
    index.metadata.tool_info.name = "make_fixture.py"

    util = index.documents.add()
    util.language = "python"
    util.relative_path = "pkg/util.py"
    for symbol in (ADD, HELPER):
        info = util.symbols.add()
        info.symbol = symbol
        info.kind = KIND_FUNCTION
    util.occurrences.add().CopyFrom(def_occ(ADD, 1, 4, 7))
    util.occurrences.add().CopyFrom(def_occ(HELPER, 5, 4, 10))
    util.occurrences.add().CopyFrom(def_occ(LOCAL, 1, 0, 4))

    app = index.documents.add()
    app.language = "python"
    app.relative_path = "pkg/app.py"
    for symbol, kind in ((MAIN, KIND_FUNCTION), (KLASS, KIND_CLASS), (METHOD, KIND_METHOD)):
        info = app.symbols.add()
        info.symbol = symbol
        info.kind = kind
    app.occurrences.add().CopyFrom(def_occ(MAIN, 3, 4, 8))
    app.occurrences.add().CopyFrom(def_occ(KLASS, 7, 0, 5))
    app.occurrences.add().CopyFrom(def_occ(METHOD, 8, 4, 10))
    app.occurrences.add().CopyFrom(def_occ(LOCAL, 4, 24, 29))
    app.occurrences.add().CopyFrom(ref_occ(ADD, 4, 11, 14))
    app.occurrences.add().CopyFrom(ref_occ(HELPER, 10, 4, 10))
    app.occurrences.add().CopyFrom(ref_occ(METHOD, 8, 0, 6))
    app.occurrences.add().CopyFrom(ref_occ(GHOST, 4, 17, 22))
    app.occurrences.add().CopyFrom(ref_occ(LOCAL, 4, 24, 29))
    deprecated = scip.Occurrence(symbol=ADD, range=[4, 8, 4, 30])
    app.occurrences.add().CopyFrom(deprecated)
    app.occurrences.add().CopyFrom(scip.Occurrence(symbol=GHOST))  # no range: unjoined
    app.occurrences.add().CopyFrom(ref_occ(HELPER, 20, 0, 6))  # outside every symbol
    app.occurrences.add().CopyFrom(ref_occ(EXTERNAL, 11, 0, 8))

    extra = index.documents.add()
    extra.language = "python"
    extra.relative_path = "gen/extra.py"
    extra.occurrences.add().CopyFrom(def_occ("scip-python pypi gen 1.0.0/gen/extra.py/only( f).", 0, 0, 4))

    index.external_symbols.add().CopyFrom(
        scip.SymbolInformation(symbol=EXTERNAL, kind=KIND_FUNCTION)
    )
    return index


def main():
    out = os.path.join(os.path.dirname(__file__), "sample.scip")
    with open(out, "wb") as fh:
        fh.write(build_index().SerializeToString())
    print(f"wrote {out} ({os.path.getsize(out)} bytes)")


if __name__ == "__main__":
    main()
