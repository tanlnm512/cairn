#!/usr/bin/env python
"""Regenerate the heavy/ + heavy-off/ workspaces, the committed SCIP index,
and the heavy ground truth deterministically.

Run from the repo root: uv run --no-sync python tests/fixtures/scip-indexing/generate_heavy.py
The committed index must be regenerated this way, never edited by hand.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

FAMILY = Path(__file__).resolve().parent
HEAVY = FAMILY / "heavy"
HEAVY_OFF = FAMILY / "heavy-off"
HEAVY_GT = FAMILY / "heavy-ground-truth"

N_MODULES = 40
FNS_PER_MODULE = 5
SCIP_CALLS_PER_FN = 10
KIND_FUNCTION = 17
ROLE_DEFINITION = 0x1

RATIO = SCIP_CALLS_PER_FN / 2  # tree-sitter sees 2 call expressions per function


def module_name(i: int) -> str:
    return f"mod_{i:02d}.py"


def util_name(i: int) -> str:
    return f"util_{i:02d}.py"


def fn_name(i: int, j: int) -> str:
    return f"fn_{i:02d}_{j}"


def module_source(i: int):
    """(text, def-line map) for one module; 0-based def lines for the index."""
    lines = [f'"""Module {i:02d} of the heavy call graph."""', "", ""]
    def_lines = {}
    for j in range(FNS_PER_MODULE):
        callee = fn_name((i + 1) % N_MODULES, j)
        def_lines[j] = len(lines)
        lines.append(f"def {fn_name(i, j)}(a, b):")
        for k in range(1, SCIP_CALLS_PER_FN + 1):
            lines.append(f"    t{k} = a * {k} - b")
        lines.append(f"    return shared(t{SCIP_CALLS_PER_FN}, b) + {callee}(a, b)")
        lines.append("")
        lines.append("")
    return "\n".join(lines) + "\n", def_lines


def util_source(i: int) -> str:
    return (
        f'"""Utility {i:02d}; the duplicated shared() makes its callsites ambiguous."""\n'
        "\n"
        "\n"
        "def shared(a, b):\n"
        "    return a - b\n"
    )


def build_index(sources):
    from cairn.parsers import _scip_pb2 as pb

    index = pb.Index()
    for i in range(N_MODULES):
        text, def_lines = sources[i]
        doc = index.documents.add()
        doc.relative_path = module_name(i)
        doc.language = "python"
        for j in range(FNS_PER_MODULE):
            fn = fn_name(i, j)
            symbol = f"python {module_name(i)} {module_name(i)}/{fn}()."
            info = doc.symbols.add()
            info.symbol = symbol
            info.kind = KIND_FUNCTION
            def_occ = doc.occurrences.add()
            def_occ.symbol = symbol
            def_occ.symbol_roles = ROLE_DEFINITION
            def_occ.single_line_range.line = def_lines[j]
            def_occ.single_line_range.start_character = 4
            def_occ.single_line_range.end_character = 4 + len(fn)
            nxt = (i + 1) % N_MODULES
            body_start = def_lines[j] + 1
            for k in range(SCIP_CALLS_PER_FN):
                occ = doc.occurrences.add()
                occ.symbol = f"python {module_name(nxt)} {module_name(nxt)}/{fn_name(nxt, k % FNS_PER_MODULE)}()."
                occ.single_line_range.line = body_start + k
                occ.single_line_range.start_character = 4
                occ.single_line_range.end_character = 24
    return index.SerializeToString(deterministic=True)


def write_ground_truth():
    HEAVY_GT.mkdir(parents=True, exist_ok=True)
    queries = [
        ("hv-definition", "definition", "fn_07_3", "covered definition; identity must survive the overlay"),
        ("hv-chain-target", "call-binding", "fn_08_3", "the cross-module chain callee of fn_07_3"),
        ("hv-mid-chain", "definition", "fn_20_0", "mid-chain definition deep in the corpus"),
        ("hv-wrapped", "call-binding", "fn_33_2", "caller whose chain wraps past the last module"),
        ("hv-wrap-target", "definition", "fn_00_4", "wrap-around chain target in the first module"),
        ("hv-builtin-adjacent", "definition", "fn_11_1", "definition in a body with an unresolved builtin call"),
    ]
    expectations = [
        ("hv-definition", "mod_07.py#fn_07_3", 2),
        ("hv-chain-target", "mod_08.py#fn_08_3", 2),
        ("hv-chain-target", "mod_07.py#fn_07_3", 1),
        ("hv-mid-chain", "mod_20.py#fn_20_0", 2),
        ("hv-wrapped", "mod_33.py#fn_33_2", 2),
        ("hv-wrapped", "mod_34.py#fn_34_2", 1),
        ("hv-wrap-target", "mod_00.py#fn_00_4", 2),
        ("hv-builtin-adjacent", "mod_11.py#fn_11_1", 2),
    ]
    with open(HEAVY_GT / "queries.jsonl", "w", encoding="utf-8") as fh:
        for qid, kind, text, why in queries:
            fh.write(json.dumps({
                "query_id": qid, "level": "L1", "kind": kind, "text": text,
                "rationale": why,
            }) + "\n")
    with open(HEAVY_GT / "expectations.tsv", "w", encoding="utf-8") as fh:
        fh.write("query_id\tsymbol_id\tgrade\n")
        for qid, sym, grade in expectations:
            fh.write(f"{qid}\t{sym}\t{grade}\n")


def main() -> int:
    sources = {}
    for ws in (HEAVY, HEAVY_OFF):
        ws.mkdir(parents=True, exist_ok=True)
        for i in range(N_MODULES):
            text, def_lines = module_source(i)
            sources.setdefault(i, (text, def_lines))
            (ws / module_name(i)).write_text(text, encoding="utf-8")
            (ws / util_name(i)).write_text(util_source(i), encoding="utf-8")
    (HEAVY / "index.scip").write_bytes(build_index(sources))
    (HEAVY / "cairn.json").write_text(
        json.dumps({"scip": {"indexes": {"python": "index.scip"}}}, indent=2) + "\n",
        encoding="utf-8",
    )
    write_ground_truth()
    print(
        f"heavy: {N_MODULES} modules, {N_MODULES * FNS_PER_MODULE} functions, "
        f"target scip/ts calls ratio {RATIO:.1f}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
