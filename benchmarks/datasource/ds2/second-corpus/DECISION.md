# Second-corpus decision (T007, FR-002 / D-011 / TC-008)

Date: 2026-08-17 · Decision: **VENDORED — attrs 26.1.0** (this directory, `attrs-26.1.0/`)

## Candidates evaluated (all measured, none guessed)

All sizes measured on this machine by downloading the pinned PyPI sdist,
verifying its sha256 against the registry-published digest, unpacking, and
summing file bytes (the same byte-sum method `scripts/verify_datasource.py`
uses). "Vendored subset" = package + tests + LICENSE + README + CHANGELOG +
pyproject (the t2/yarl vendoring shape: docs/, CI configs, lockfiles and
build tooling excluded).

| Candidate | Version | License | Source (pinned) | Full sdist tree | Vendored subset | Finding |
|---|---|---|---|---|---|---|
| **attrs** | 26.1.0 | MIT | [sdist](https://files.pythonhosted.org/packages/9a/8e/82a0fe20a541c03148528be8cac2408564a6c9a0cc7e9171802bc1d26985/attrs-26.1.0.tar.gz) sha256 `d03ceb89…eff32` | 1,985.3 KB / 130 files | **674.9 KB / 67 files** | **CHOSEN** — richest API surface for ≥150-query authoring (dual `attr`/`attrs` API generations, validators/converters/setters/filters modules, 16 test files of realistic call shapes), well-known, MIT |
| markdown-it-py | 4.2.0 | MIT | [sdist](https://files.pythonhosted.org/packages/06/ff/7841249c247aa650a76b9ee4bbaeae59370dc8bfd2f6c01f3630c35eb134/markdown_it_py-4.2.0.tar.gz) sha256 `04a21681…8d49` | 290.4 KB / 91 files | 223.1 KB / 68 files | Rejected: the sdist ships **no tests/** (tests live only in the git repo), so a registry-pinned vendoring loses the "realistic call shapes" the t2 corpus carries; also moderate vocabulary overlap with yarl (both are tokenizers/parsers — escape, parse, quote) |
| cachetools | 7.1.7 | MIT | [sdist](https://files.pythonhosted.org/packages/70/d2/47e8bc06fe2a06d3f5bdf20f1126ab66c4e99dc48d940e7ba873f7ac7131/cachetools-7.1.7.tar.gz) sha256 `a3e2a00b…cf50` | 224.5 KB / 37 files | 165.8 KB / 27 files | Rejected: clean and tiny, but the API surface is ~6 modules / 27 files — too thin a symbol substrate for authoring ≥150 L1 queries (all four kinds) plus ≥40 L5 against this corpus alone |

## Why attrs is a genuine cross-corpus contrast to DS-v1's t2 (yarl)

- **Different domain**: declarative class/attribute-construction metaprogramming
  vs yarl's URL parsing/quoting for async HTTP. No query vocabulary carry-over
  (the markdown-it-py overlap trap avoided).
- **Different style**: decorator DSL with many small functions and two parallel
  API generations (`attr.*` legacy, `attrs.*` modern) vs yarl's single large
  stateful `URL` class with a Cython/Python dual implementation.
- **Different upstream org and license**: python-attrs / MIT vs aio-libs /
  Apache-2.0 (both permissive).
- **Comparable scale**: 674.9 KB vs t2's 466.7 KB — per-corpus rows are not
  dominated by a size asymmetry; both parse cleanly as pure-Python source in
  cairn's indexer.

## Constraints check (FR-002, measured with the corpus present)

| Constraint | Limit | Measured | Verdict |
|---|---|---|---|
| Per-corpus (this corpus) | ≤ 3072 KB | 674.9 KB (691,124 bytes, 67 files) | PASS |
| Datasource total | ≤ 5120 KB | 1143.5 KB (byte-sum of the whole `benchmarks/datasource/` tree with this corpus present) | PASS — ~3976 KB headroom for `ground_truth/`, power-analysis artifacts, and the DS2 budget rule |
| Permissive license | MIT/BSD/Apache-2.0/PSF | MIT, LICENSE vendored verbatim, NOTICE attribution present | PASS |
| Full provenance + NOTICE | — | `attrs-26.1.0/provenance.json` (keep/exclude manifest, archive URL + sha256, export notes) + `attrs-26.1.0/NOTICE` (name, version, upstream URL, license, retrieval date 2026-08-17, integrity sha256) | PASS |

Note on the budget checker: the sibling `DS2_BUDGET_KB` rule for
`benchmarks/datasource/ds2` is being added to `scripts/verify_datasource.py`
in parallel (T006). The existing checks already bind this corpus today: the
datasource-total rule (≤ 5120 KB, measured over the whole tree) passes with
the corpus present, and the per-corpus 3072 KB rule is satisfied by the
measured 674.9 KB above.

## D-011 rationale (zero-shot cross-corpus validation)

D-011: tune on DS-v1, validate zero-shot on DS-v2's corpus; per-corpus rows
plus macro-average, never cross-corpus row diffs. A vendored attrs corpus
makes that validation meaningful: it is a real, widely-used library whose
symbol naming, docstring register, and call shapes were never seen during
tuning, so DS-v2 rows measure transfer, not memorization. Deferral would have
left FR-002's second-corpus clause unmet with no size or license obstacle in
the way — every candidate measured fit the budgets — so the only defensible
outcome was to vendor the strongest one.

## Reproduction

```sh
curl -sfL -o /tmp/attrs-26.1.0.tar.gz \
  https://files.pythonhosted.org/packages/9a/8e/82a0fe20a541c03148528be8cac2408564a6c9a0cc7e9171802bc1d26985/attrs-26.1.0.tar.gz
shasum -a 256 /tmp/attrs-26.1.0.tar.gz   # d03ceb89cb322a8fd706d4fb91940737b6642aa36998fe130a9bc96c985eff32
tar xzf /tmp/attrs-26.1.0.tar.gz -C /tmp
# vendored subset = src tests LICENSE README.md CHANGELOG.md pyproject.toml
```
