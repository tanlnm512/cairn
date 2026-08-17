# DS-v2 verification seal (T010, FR-002) — TC-005 / TC-006 / TC-007 proof

**Date**: 2026-08-17 · **Pass rate: 100% — 558/558 expectations tier-1-exact,
zero unresolved, zero aspirational** · Machine-readable mirror:
`VERIFICATION.json` (schema `cairn-ds2-verification/1`).

Task T010 sealed DS-v2 with one final independent verification pass over the
COMPLETE dataset (both levels, both corpora) against fresh graph builds. This
file is the readable verification record TC-006 requires the dataset to carry.

## Result

```
OK  : 558/558 expectations tier-1-exact (pass rate 1.0000, unresolved 0,
      aspirational 0); exactly-one-grade-2-per-query True
```

Every expectation resolves to a real symbol in a freshly built index of its
corpus, at exactly its stated `file#symbol` location — nothing in the dataset
is wishful (TC-006). The pass also re-derived the loader counts (TC-005) and
re-checked DS-v1 immutability (TC-007).

## Counts (re-derived through the loader, not taken from any claim)

| level | kind | queries | expectations |
|-------|------|--------:|-------------:|
| L1 | definition | 46 | 66 |
| L1 | callers    | 42 | 131 |
| L1 | impact     | 34 | 88 |
| L1 | flow       | 32 | 107 |
| L5 | knowledge  | 44 | 166 |
| **total** | | **198** | **558** |

Per corpus (queries / expectations): **attrs-26.1.0 136** (L1 106 + L5 30) /
389; **yarl 62** (L1 48 + L5 14) / 169.

Floors (TC-005): L1 154 ≥ 150 with all four kinds non-zero (each ≥ 32);
L5 44 ≥ 40. Grade invariant: every query carries exactly one grade-2 primary
target (2 = primary, 1 = must-return context).

## Method

Reproduces the T008/T009 authoring method (AUTHORING.md), applied once over
the whole dataset instead of per batch:

1. **Loader gate** — `cairn.eval.load_ground_truth` over
   `benchmarks/datasource/ds2/ground_truth` (fails loudly on any shape
   violation) → 198 queries.
2. **Fresh scratch builds, one per corpus** — the vendored source tree is
   copied to a throwaway workspace and the COPY gets the empty `.git`
   scanner marker (committed trees stay marker-free; same idiom as
   `scripts/verify_ground_truth.py:build_fresh_graph`); `build_graph` runs
   over the workspace. Degraded builds (0 repos / parse errors) abort the
   pass rather than mint verdicts.
3. **Tier-1-exact resolution, every expectation** — exact symbol-name
   equality AND exact repo-relative file-path equality after stripping the
   corpus prefix (`attrs-26.1.0/src/attr/_make.py#attrs` →
   `("attrs", "src/attr/_make.py")` against the attrs inventory). This is
   STRICTER than the committed `match_rank` tier-1 (file *suffix* + name),
   which was also run over each full pool for parity: exact implies tier-1,
   tier-1 implies rank > 0.

## Build facts verified against

The fresh builds reproduce exactly the facts recorded during authoring
(AUTHORING.md); drift would abort the pass:

| corpus | repos | files | symbols | edges | parse_errors |
|--------|------:|------:|--------:|------:|-------------:|
| attrs-26.1.0 | 1 | 50 | 1672 | 4174 | 0 |
| yarl | 1 | 24 | 1066 | 2432 | 0 |

## Pins (manifest.json)

`dataset_version` is **DS-v2** (distinct from DS-v1; declared in the
manifest). All pins use the repo's existing content-pin mechanism —
`cairn.bench.datasource.tree_hash`, the Git-tree-shaped sorted-manifest
digest (`sha256` over sorted `<mode> <relpath>\0<sha256(content)>` entries,
modes normalized git-style, `.git` contents and build-noise caches —
`__pycache__`/`.ruff_cache`/`.mypy_cache`/`.pytest_cache` — excluded) — the
same function DS-v1's `benchmarks/datasource/manifest.json` t1 pins use:

| manifest key | covers | value |
|--------------|--------|-------|
| `corpora.attrs-26.1.0.tree_hash` | the vendored attrs-26.1.0 source tree | `ad6eec778ba82da2ac4493676f990c6d155e7b0634900c36a54da0de2d515097` |
| `corpora.yarl.tree_hash` | the vendored yarl snapshot (`t2/yarl`) | `b2ac9f50845b86bdc14388365490e714dad5cb57a0a4896e8879fc9e8745b974` |
| `tree_hash` (top level) | the dataset DATA pair — `queries.jsonl` + `expectations.tsv` staged in a scratch dir and hashed with the same function; `manifest.json`/`VERIFICATION*`/`AUTHORING.md` are excluded so the pin is never self-referential | `d83beefc23ede049d559c4567c173f2df563d6daf5addadb97b04d589c443a05` |

`verify_dataset.py` recomputes the two corpus pins on every run and fails on
mismatch — a corpus edit anywhere breaks the seal loudly.

### Pin revision (2026-08-17, same day as the seal)

The original attrs pin `847e73ef5eabab33...` was minted over the authoring
tree **including untracked build noise** — a `.ruff_cache` dropped inside the
vendored tree by pre-commit ruff runs — so a fresh clone (no caches) hashed
differently and the seal failed with "corpus content drifted" for attrs even
though all 558 expectations verified. The fix went into the pin mechanism,
not the data: `tree_hash` now prunes build-noise caches exactly as it always
pruned `.git` (hash-neutral for clean trees, so every pin minted over a
noise-free tree survives unchanged), and the attrs pin was re-minted over
the pristine `HEAD` tree (`git archive` export — no local noise). The yarl
(`b2ac9f50...`) and data-pair (`d83beefc...`) pins were verified unchanged
under the hardened function. No corpus data or expectation row was touched.
The re-mint is machine-recorded in `VERIFICATION.json` (`pins.revisions`).

## TC-007 — DS-v1 byte-identical

```
git status --porcelain benchmarks/datasource/t2 \
                      benchmarks/quality/ablation.json \
                      benchmarks/quality/ablation.md
# (no output — every DS-v1 artifact identical to HEAD)
```

All DS-v1 artifacts (t2 tree + ablation records) are byte-identical to git
HEAD before and after the seal; DS-v2 content lives only under
`benchmarks/datasource/ds2/` under its own version label.

## Spot-check (TC-006 human leg)

12 queries / 31 expectations, spanning both corpora, all four L1 kinds, L5,
and both grades, were confirmed by reading the corpus source; recorded
grades agree — grade-2 rows are the load-bearing primary in every case.
`DS2-L1-D01 DS2-L1-D40 DS2-L1-C05 DS2-L1-C35 DS2-L1-I03 DS2-L1-I25
DS2-L1-F02 DS2-L1-F27 DS2-L5-K01 DS2-L5-K20 DS2-L5-K31 DS2-L5-K44` —
including exact line citations (`_make.py:2839` is the
`self._default = Factory(meth, takes_self=True)` line for I03; `_make.py:2038`
is the `_attrs_to_init_script(...)` call site for F02; yarl
`_url.py:777/788/799/809` are the four credential property defs for K44).

## Reproduce

```
uv run python benchmarks/datasource/ds2/verify_dataset.py        # human summary
uv run python benchmarks/datasource/ds2/verify_dataset.py --json # machine report
uv run python -c "from collections import Counter; from cairn.eval import load_ground_truth; qs = load_ground_truth('benchmarks/datasource/ds2/ground_truth'); print(len(qs), Counter(q.level for q in qs), Counter(q.kind for q in qs))"
git status --porcelain benchmarks/datasource/t2 benchmarks/quality/ablation.json benchmarks/quality/ablation.md   # TC-007: empty
```
