# DS-v2 ground truth — authoring notes (T008 L1, T009 L5; sealed by T010)

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
pin) is DONE — T010, 2026-08-17: 558/558 expectations tier-1-exact over fresh
scratch builds of both corpora (pass rate 1.0000, zero aspirational). The
verification record lives beside this file (`VERIFICATION.md` + machine
readable `VERIFICATION.json`, schema `cairn-ds2-verification/1`); the
reproducible verifier is `../verify_dataset.py`; `manifest.json` carries the
pinned `tree_hash` values (per-corpus source trees + the data pair) and
`dataset_version: "DS-v2"`.

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

# L5 — knowledge queries (T009)

L5 mirrors DS-v1's semantic level exactly: `kind: "knowledge"`, ids
`DS2-L5-K##` (DS-v1's unprefixed `L5-K*` space never repeats), "How does X
work?" mechanism questions whose answer spans several cooperating symbols,
each query with exactly one grade-2 primary (the load-bearing symbol) plus
grade-1 context rows (2-5 expectations per query, avg 3.8 — DS-v1 averaged
3.1).

## Counts (all through the loader)

- L5 total: **44** (floor 40, T005/D-010/TC-005)
- per corpus: attrs-26.1.0 **30**, yarl **14** (both corpora represented,
  cross-corpus dominant stratum preserved)
- L5 expectations: 166 (113 attrs + 53 yarl); combined with L1: 558 total
- final loader counts: 198 queries = L1 154 + L5 44; kinds
  {definition 46, callers 42, impact 34, flow 32, knowledge 44}

## Method (same as the L1 batches above)

Per batch (15/15/14 queries), after landing:

1. Loader gate: `cairn.eval.load_ground_truth` over the full pair —
   154 L1 rows unchanged every time, L5 15 -> 30 -> 44.
2. Resolution gate against the same fresh scratch inventories (attrs
   repos=1 files=50 symbols=1672 parse_errors=0; yarl 1/24/1066/0): every
   L5 expectation resolved tier-1-exact (exact symbol name + exact
   repo-relative path after stripping the corpus prefix), cumulatively
   57/57, 113/113, 166/166.
3. Rationales cite only code read in this snapshot (file:line), including
   the overloads caveat (yarl's `update_query`/`extend_query`/`with_query`
   def+overload rows share one file#name id) and assignment-not-indexed
   names (`NOTHING`, `NO_OP`, `repr_context`, `mutable`, yarl's
   `from_parts`) — none of those appear as expectations.

## Batches

- L5 batch 1: K01-K15 (attrs knowledge, 15 queries / 57 expectations)
- L5 batch 2: K16-K30 (attrs knowledge, 15 / 56)
- L5 batch 3: K31-K44 (yarl knowledge, 14 / 53)

Authoring fix during verification (recorded in the wave log): K43 initially
claimed `with_path` consumes `path_safe` — untrue in this snapshot
(`with_path` quotes its input via PATH_QUOTER); the rationale now describes
path_safe's real, read-side role.

## Style notes vs DS-v1's L5

- attrs topics: class-building machinery (init codegen, field collection,
  slots/frozen/on_setattr), serialization (asdict/filters), dunder codegen
  (eq/order/hash/repr), converters/validators composition and disabling,
  next-gen define/field defaults, aliasing, pickling, cmp_using,
  VersionInfo, introspection, NOTHING/Factory defaults, exception classes,
  linecache debuggability, deep validators.
- yarl topics are fresh angles distinct from DS-v1's 24 L5 rows and from
  T008's L1 primaries: constructor dispatch, build() validation, query
  merge (update/extend), per-instance cache under __slots__, encode_url
  pipeline, special-scheme empty-host enforcement, with_name/suffix
  surgery, explicit vs effective port, with_fragment self-return,
  rewrite_module/cache rebinding, validate_host dichotomy, joinpath
  mechanics, the three path decoders, credential decoding.
