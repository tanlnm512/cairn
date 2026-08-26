# Research: multi-source-doc-ingestion

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-26
<!-- External grounding for tech decisions: every claim below carries a source
     URL/DOI — no unsourced "it is known that". The tech agent consumes this
     file when choosing options in tech-spec.md. -->

Local baseline (verified this session, not external): cairn is MIT-licensed
(`/Users/lnmtan/Projects/others/cairn/LICENSE`; `pyproject.toml` line 10:
`license = "MIT"`). FR-003 requires a pure-python converter behind an optional
extra `cairn[ingest]`, with wheels only for macOS arm64 + manylinux
x86_64/aarch64 + Windows.

## Questions

### RQ1 — Pure-python PDF→markdown converters (pymupdf4llm vs markitdown vs others)

- **source**: https://pypi.org/project/pymupdf4llm/ · **claim**: pymupdf4llm
  1.28.2 (2026-08-06) is a pure-python (`py3-none-any`) wrapper that installs
  PyMuPDF; it emits font-size-derived `#`-`######` headings, GitHub-compatible
  pipe tables, and fenced code blocks, with multi-column reading-order
  reconstruction and header/footer removal. · **relevance**: RQ1 / FR-003
  (quality axes; "pure-python" = the wrapper, engine is a C dep with wheels) ·
  **confidence**: high
- **source**: https://pypi.org/project/PyMuPDF/ · **claim**: PyMuPDF 1.28.2 is
  dual-licensed "GNU AGPL v3 or Artifex Commercial License" and publishes
  wheels for macOS x86_64/arm64, manylinux glibc x86_64/aarch64, musllinux
  x86_64, and Windows x86/x86_64/arm64 (CPython 3.10–3.14) — covering all of
  cairn's target platforms; without a wheel pip compiles C. · **relevance**:
  RQ1 license axis + FR-003 wheel-availability axis · **confidence**: high
- **source**: https://pypi.org/project/markitdown/ and
  https://raw.githubusercontent.com/microsoft/markitdown/main/packages/markitdown/pyproject.toml ·
  **claim**: markitdown 0.1.7 (2026-07-29) is MIT-licensed, Microsoft-backed
  (176k stars, 316 commits), pure-python with format extras — the `[pdf]`
  extra requires `pdfminer.six>=20251230` + `pdfplumber>=0.11.9` (no PyMuPDF,
  no C extension of its own) and ships a CLI (`markitdown file.pdf > out.md`).
  · **relevance**: RQ1 / FR-003 (license-clean alternative; same extras
  pattern cairn plans) · **confidence**: high
- **source**: https://yage.ai/share/markitdown-survey-en-20260412.html and
  https://thunderbit.com/blog/markitdown-review · **claim**: third-party
  benchmarks rank markitdown low for PDF structure — second-to-last of 12
  tools (0.589/1.0) in the yage.ai survey, with heading-hierarchy ≈0.0 and
  table fidelity ≈0.27 in Thunderbit's review — because pdfminer-based
  extraction has no layout model. · **relevance**: RQ1 quality axis (AC US4-1)
  · **confidence**: med (third-party benchmarks, methodology not audited)
- **source**: https://ai.gopubby.com/benchmarking-pdf-to-markdown-document-converters-fc65a2c73bf2
  and https://www.file2markdown.ai/blog/best-pdf-to-markdown-converter ·
  **claim**: the same benchmark family rates pymupdf4llm as the fastest /
  "fastest lightweight option for well-structured PDFs" with good overall
  markdown, though it sometimes emits invalid table structures. ·
  **relevance**: RQ1 quality axis (table risk maps to the FR-003 skip-on-
  garbage gate) · **confidence**: med
- **source**: https://levelup.gitconnected.com/pdf-to-markdown-mastery-the-ultimate-benchmarking-guide-for-2025-11fba7390b77
  · **claim**: higher-quality 2025-2026 alternatives named across benchmarks
  are Docling, MinerU, and Marker — but they trade speed/weight (ML runtimes,
  GPU-oriented) against pymupdf4llm's lightweight C-engine approach. ·
  **relevance**: RQ1 "other credible candidates" (FR-003 says no system
  binaries; these are pip-installable but heavy) · **confidence**: med
- **source**: https://fossa.com/resources/devops-tools/license-compatibility-checker/mit-vs-agpl-3-0/ ·
  **claim**: MIT code can flow INTO an AGPL-3.0 project (one-way compatible),
  but an AGPL dependency inside an MIT package does not make the combination
  MIT — copyleft obligations attach to the combined work when distributed.
  · **relevance**: RQ1 license axis vs cairn's MIT (pymupdf4llm viability as
  an optional extra) · **confidence**: med (vendor explainer; legal gray area,
  not legal advice)
- **source**: https://github.com/browser-use/browser-use/issues/2610 and
  https://github.com/mindee/doctr/issues/486 · **claim**: real-world
  precedent: MIT-licensed browser-use raised a licensing issue over a
  transitive AGPL PyMuPDF dependency, and doctr moved off PyMuPDF because of
  AGPL commercial-use friction — while the img2table project keeps PyMuPDF
  strictly optional (https://www.reddit.com/r/learnpython/comments/1ggz2pq/)
  and https://hynek.me/articles/python-recursive-optional-dependencies/
  documents extras as the standard mechanism for isolating such deps. ·
  **relevance**: RQ1 license axis (the extra-isolation pattern is established
  mitigation; some orgs ban AGPL deps outright per
  https://www.nijho.lt/post/gpl/) · **confidence**: med-high (documented
  cases; no court-tested answer)

### RQ2 — Pure-python DOCX→markdown converters (mammoth vs markitdown vs others)

- **source**: https://pypi.org/project/mammoth/ · **claim**: mammoth 1.12.1
  (2026-08-09) is BSD-2-Clause, pure-python (`py2.py3-none-any`, no deps,
  universal wheel — trivially available on all platforms), production/stable
  with 80+ releases since 2013, converting docx semantics (headings, lists,
  tables, footnotes, comments) to clean HTML while deliberately discarding
  cosmetic styling; its Markdown output is officially deprecated. ·
  **relevance**: RQ2 / FR-003 (license + wheels ideal; markdown requires
  deprecated path or HTML→md step) · **confidence**: high
- **source**: https://raw.githubusercontent.com/microsoft/markitdown/main/packages/markitdown/pyproject.toml ·
  **claim**: markitdown's `[docx]` extra is exactly `mammoth~=1.11.0` +
  `lxml` — i.e. markitdown's docx→markdown quality IS mammoth's conversion
  plus markitdown's HTML→markdownify step, bundled with base deps
  (beautifulsoup4, requests, markdownify, magika, charset-normalizer,
  defusedxml). · **relevance**: RQ2 (direct mammoth vs markitdown wrapper is
  the real choice; wrapper adds multi-format breadth + CLI) · **confidence**:
  high
- **source**: https://pypi.org/project/mammoth/ · **claim**: mammoth's docs
  warn that docx→HTML is structurally lossy for complex documents and works
  best with semantically-styled sources (styles like "Heading 1" rather than
  visually-formatted text). · **relevance**: RQ2 quality axis / AC US4-2
  (skip-on-garbage gate rationale for docx too) · **confidence**: high

### RQ3 — Stable-id / idempotent ingestion prior art

- **source**: https://developers.llamaindex.ai/python/examples/ingestion/document_management_pipeline/ ·
  **claim**: LlamaIndex's documented scheme: `SimpleDirectoryReader(...,
  filename_as_id=True)` makes the doc_id path-derived (identity), while a
  stored map `doc_id -> document_hash` tracks content freshness — "If the hash
  has not changed, the document will be skipped in the pipeline"; re-ingest of
  edited content re-processes only that doc. · **relevance**: RQ3 / FR-007,
  FR-009 (identity vs content-hash decoupling) · **confidence**: high
- **source**: https://developers.llamaindex.ai/python/framework-api-reference/ingestion/ ·
  **claim**: the IngestionPipeline API checks "if a document is already in the
  doc store based on its id. If it is not, or if the hash of the document is
  updated, it will update the document in the docstore" — with
  `docstore_strategy` values `UPSERTS`, `UPSERTS_AND_DELETE`, `DUPLICATES_ONLY`
  (also recommended in
  https://github.com/run-llama/llama_index/issues/13162 for "no duplicate
  documents"). · **relevance**: RQ3 / FR-009 (upsert-not-duplicate semantics;
  AC US5-2) · **confidence**: high
- **source**: https://docusaurus.io/docs/creating-pages · **claim**: Docusaurus
  derives page routes deterministically from the file's path in the directory
  hierarchy (`/src/pages/foo/test.js` → `[baseUrl]/foo/test`) — a stable
  path-based slug — with frontmatter `slug` override documented on its docs
  plugin (path = default identity, frontmatter = escape hatch). ·
  **relevance**: RQ3 / FR-007 (slug-from-path + override matches cairn's
  slugified-title + `({repo})` collision suffix design) · **confidence**: med
  (pages-plugin page fetched; the docs-plugin slug-override statement is
  pointed to but was not directly retrieved)
- **source**: https://github.com/npryce/adr-tools · **claim**: adr-tools
  gives each ADR a stable file identity as a monotonically numbered markdown
  file (`nnnn-title.md` under `doc/adr/`) and links supersessions by number —
  identity survives edits, content changes in place. · **relevance**: RQ3
  (sequence-number identity as a third documented scheme) · **confidence**:
  high

### RQ4 — Document-type classification conventions (ADR formats, status vocabularies)

- **source**: https://adr.github.io/madr/ · **claim**: MADR ("Markdown
  Architectural Decision Records", dual MIT/CC0) specifies an optional YAML
  frontmatter field `status: "{proposed | rejected | accepted | deprecated |
  … | superseded by ADR-0123`" — i.e. frontmatter-driven status as the
  canonical convention, with an open vocabulary (ellipsis) anchored on
  proposed/rejected/accepted/deprecated/superseded-by-link, plus template
  sections (Context and Problem Statement, Decision Drivers, Considered
  Options, Decision Outcome, Consequences). · **relevance**: RQ4 / FR-004,
  FR-005 (frontmatter status parsing + skip-list vocabulary overlaps MADR's
  status set (both skip proposed/superseded-equivalents) but is not 1:1:
  MADR's `rejected` stays ingestible, and cairn skips draft/review which
  MADR lacks) · **confidence**: high
- **source**: https://github.com/npryce/adr-tools · **claim**: adr-tools'
  `adr new -s 9 …` marks a new ADR as superseding ADR 9 and "changes the
  status of ADR 9" to superceded-by — status transitions expressed as linked
  markers, and file naming `nnnn-title.md` in `doc/adr/` is the
  convention-based classifier (directory + numbering ⇒ decision record). ·
  **relevance**: RQ4 / FR-004 (filename/directory-driven doc-kind
  classification precedent) · **confidence**: high
- **source**: https://github.com/joelparkerhenderson/architecture-decision-record ·
  **claim**: the canonical ADR template catalog (Nygard, Tyree/Akerman, MADR,
  arc42, ITDs, …) shows status vocabularies vary per organization; it
  documents immutability-vs-mutability practice ("In theory, immutability is
  ideal. In practice, mutability has worked better for our teams.") and
  supersession-by-new-record. · **relevance**: RQ4 / FR-005 (skip vs
  `--include-drafts` must tolerate vocabularies beyond one fixed set;
  per-workspace overrides FR-010) · **confidence**: med
- **source**: https://github.com/adr/madr (repo README) · **claim**: MADR's
  own usage guidance — copy a template per decision into `docs/decisions/`
  named `nnnn-title.md` — reinforcing that both the parent directory name and
  the numbered filename carry the doc-kind signal. · **relevance**: RQ4 /
  FR-004 (directory- and filename-driven classification rules to key the
  doc-kind→doc_type map on) · **confidence**: high

## Options summary

### PDF converter behind `cairn[ingest]` (C-04 / FR-003)
- pymupdf4llm — best structure (font-derived headings, GFM tables, fenced
  code; fastest in benchmarks) + wheels on all cairn platforms, but AGPL-3.0
  (or paid Artifex commercial) inside an MIT package — needs extra-isolation
  and still worries AGPL-banning orgs.
- markitdown[pdf] — MIT, Microsoft-maintained, same extras pattern, but
  pdfminer/pdfplumber backends score worst-tier on heading hierarchy (~0.0)
  and tables (~0.27) in third-party benchmarks.
- docling / MinerU / Marker — highest fidelity per benchmarks, but heavy ML
  runtimes (torch/onnx, GPU-leaning) against FR-003's lightweight
  no-system-binary goal.

### DOCX converter behind `cairn[ingest]` (C-04 / FR-003)
- mammoth direct — BSD-2, zero-dep universal wheel, semantic-quality
  conversion; markdown output deprecated (HTML-first, needs an HTML→md step)
  and lossy on visually-styled docs.
- markitdown[docx] — MIT wrapper that IS mammoth+lxml underneath plus
  markdownify HTML→md; one extra buys PDF+DOCX+PPTX/XLSX breadth at the cost
  of extra base deps (bs4, magika, …).

### Stable identity scheme (FR-007 / FR-009)
- path/slug-derived id + content hash (LlamaIndex filename_as_id +
  doc_id→hash skip) — identity stable across edits, hash gates re-processing;
  proven upsert semantics.
- pure content-hash id — natural dedup incl. moves/renames, but identity
  breaks on trivial edits and collides on duplicate content across repos.
- sequence-number id (adr-tools nnnn-title) — human-stable, but requires
  corpus-wide numbering authority cairn won't have.

### Classification basis (FR-004 / FR-005)
- MADR-style frontmatter status (proposed/rejected/accepted/deprecated/
  superseded-by) — precise, spec-backed vocabulary, but only present in
  well-groomed corpora.
- filename/directory conventions (docs/decisions/nnnn-title.md, ADR in
  filename) — works on legacy corpora with no frontmatter, but heuristic and
  per-org variable (hence FR-010 overrides).
- inline `**Status:**`/`## Status` markers (adr-tools-style prose status) —
  rescues ADRs written Nygard-style without frontmatter; needs the FR-004
  fallback parser.
