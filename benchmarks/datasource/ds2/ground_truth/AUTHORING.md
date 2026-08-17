# DS-v2 L1 ground truth — authoring notes (T008)

Shape: byte-for-byte mirror of DS-v1 (`benchmarks/datasource/t2/ground_truth/`):
`queries.jsonl` (one JSON object per line: `query_id`, `level`, `kind`, `text`,
`rationale`) + `expectations.tsv` (`query_id\tsymbol_id\tgrade`; grades {1, 2},
2 = primary target, 1 = must-return context). Loaded through
`cairn.eval.load_ground_truth` (fails loudly on any shape violation).

## ID space (no collision with DS-v1)

Ids are `DS2-L1-<K><nn>` with K in {D=definition, C=callers, I=impact,
F=flow}; numbering is continuous within a kind across both corpora
(D01-D46, C01-C42, I01-I34, F01-F32). DS-v1's unprefixed `L1-*` ids never
repeat. L5 (`DS2-L5-K*`, >=40) is T009's, not T008's.

## Corpus convention

Each expectation's `symbol_id` file component carries its corpus directory as
the leading path segment — `attrs-26.1.0/src/attr/_make.py#attrs` (the new
dominant stratum) or `yarl/yarl/_url.py#URL` (the first corpus, exactly DS-v1's
convention) — so corpus membership (D-011 per-corpus rows) is recoverable from
the dataset itself. Note the yarl snapshot root contains the `yarl/` package
and `tests/` directly, hence the double `yarl/yarl/...` in ids.

## Counts (all through the loader)

- L1 total: **154** (floor 150, T005/D-010)
- per kind: definition 46, callers 42, impact 34, flow 32 (all four > 0, each >= 25)
- per corpus: attrs-26.1.0 **106** (cross-corpus dominant stratum, >= 100), yarl 48
- expectations: 392 total (276 attrs + 116 yarl); every query has >= 1 row
  with exactly one grade-2 primary target

## Method (how this was verified)

Per batch (~22-38 queries), after landing:

1. Loader gate: `uv run python -c "from cairn.eval import load_ground_truth; ..."`
   -> 154 L1, kinds {definition 46, callers 42, impact 34, flow 32}.
2. Fresh scratch builds, one per corpus, mirroring
   `scripts/verify_ground_truth.py:build_fresh_graph` (copy to a throwaway
   workspace, `.git` scanner marker on the COPY only, `build_graph` over the
   workspace):
   - attrs: repos=1 files=50 symbols=1672 edges=4174 parse_errors=0
   - yarl:  repos=1 files=24 symbols=1066 edges=2432 parse_errors=0
3. Resolution gate (STRONGER than the brief's >=10 spot-check): every one of
   the 392 expectations resolved tier-1-exact against its corpus inventory —
   exact symbol-name equality plus exact repo-relative file-path equality
   after stripping the corpus prefix. (The committed verifier's tier-1 is an
   endswith match and its tier-2 is name-substring presence; this dataset
   pins file+name exactly, so it also passes the weaker matcher.)
4. Callers/impact rationales cite only precise (`resolution='exact'`) graph
   edges and closure counts recomputed from the scratch DBs (BFS over precise
   caller edges). Ambiguous-name targets (e.g. bare `evolve`, `update_query`
   calls that resolve to multiple definitions) were avoided as callers/impact
   primaries.

Full sealing (every expectation vs a fresh build, pass-rate artifact, tree_hash
pin) is T010's; `manifest.json` carries `"T010 pins this"` placeholders.

## Batches

- batch 1: D01-D32 (attrs definition, 32 queries / 44 expectations)
- batch 2: C01-C30 (attrs callers, 30 / 97)
- batch 3: I01-I22 + F01-F16 (attrs impact + flow, 38 / 112)
- batch 4: F17-F22 + D33-D46 + C31-C42 (attrs flow tail + yarl def/callers, 32 / 79)
- batch 5: I23-I34 + F23-F32 (yarl impact + flow, 22 / 60)

Authoring fixes during verification (recorded in the wave log): C21 initially
attributed the `optional` used by the deep validators to converters — it is
validators.py's own `optional` (validators.py:214); F10's grade-1 companion
pointed at `_funcs.py#fields` (defined in `_make.py`); F29's cited line for
`__bytes__` corrected to yarl/_url.py:583.

## Style

Mirrors DS-v1's distribution: mostly answerable-by-retrieval with a few harder
prose queries; mix of telegraphic ("Where is X defined...?") and
behavior-only phrasings ("Where is the converter that maps strings like yes,
on and 1 to real booleans defined?"); test-surface queries ("Which tests
exercise X?") for the callers kind, as in DS-v1 C12-C20. No query embeds a
qualified symbol path (bare names appear only where DS-v1's style does).
