# Test Cases: multi-source-doc-ingestion

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-26
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.

Conventions for every case below:
- The surface under test is the CLI command `cairn knowledge ingest` and its
  observable outputs: exit code, printed summary, the staged outbox (OKF
  files + manifest JSON), the knowledge store listing, and search results.
- Where the spec does not pin an exact flag name, "the scan mode", "the feed
  mode", and "the approval option" mean whatever `cairn knowledge ingest
  --help` documents for that purpose; flags the spec does pin
  (`--include-drafts`, batch size 32) are asserted by name.
- Unless a case says otherwise, it runs against a scratch cairn workspace so
  store observations are isolated.
- Boundary omissions are deliberate, not oversights: no AC implies concurrent
  access, so no concurrency case is derived.

## TC-001 — The ingester exists as a knowledge subcommand
- **Story**: — · **Traces to**: FR-011
- **Given** a built checkout of the CLI
- **When** the ingest command's help is invoked
- **Then** it exits 0 and documents the repo-scan mode, the feed mode, the
  dry-run default, the approval option, and `--include-drafts`
- **Pass condition**: `cairn knowledge ingest --help` exits 0; its text
  covers scanning repos, feeding files, staging/dry run, approval, and
  include-drafts.

## TC-002 — Dry-run repo scan accounts for every doc, accepted or skipped
- **Story**: US1 · **Traces to**: FR-001, AC1 (US1-AC1)
- **Given** a repo whose allowlisted doc directory contains several accepted
  docs (an ADR, a runbook, a convention doc) plus a draft and a
  meeting-notes file
- **When** a dry-run repo scan completes
- **Then** the printed summary and staged manifest list each accepted doc
  with its doc_type, and each skipped file with a non-empty reason
- **Pass condition**: exit 0; every accepted row carries a doc_type; every
  skipped row carries a reason; accepted + skipped together equal the
  documents present in the walked directories (cross-check against a manual
  listing of the doc directory — nothing silently missing).

## TC-003 — Each skip-list category is skipped with a naming reason
- **Story**: US1 · **Traces to**: FR-001
- **Given** a corpus containing one file per skip category — a draft, meeting
  notes, a generated mirror, a changelog, a template — inside an
  otherwise-allowed directory
- **When** the dry-run scan completes
- **Then** all five land in the skipped section with reasons naming their
  categories, and none is staged
- **Pass condition**: manifest's skip section holds five entries, one per
  category, each with a non-empty reason; the outbox contains none of them.

## TC-004 — Empty corpus: clean exit, nothing staged, store untouched
- **Story**: US1 (boundary) · **Traces to**: FR-001
- **Given** a repo whose doc directories contain no candidate documents
- **When** the dry-run scan completes
- **Then** exit 0, zero accepted and zero skipped reported, an empty outbox
  with a zero-count manifest, and no store write
- **Pass condition**: exit 0; summary shows 0 accepted / 0 skipped;
  `cairn knowledge list` output unchanged from before the run.

## TC-005 — Frontmatter YAML and inline Status styles parse equally
- **Story**: US1 · **Traces to**: FR-004, AC2 (US1-AC2)
- **Given** the same logical ADR authored twice — once with YAML frontmatter
  (title, status, tags), once in classic inline style (`# Title` heading plus
  a `**Status:**` marker) — and likewise a second pair of drafts, one per
  style, side by side in the corpus
- **When** the dry-run scan parses them
- **Then** the accepted pair both yield title, status, tags, and the same
  doc_type; the draft pair are both skipped for their draft status, proving
  the inline marker's status was read exactly like frontmatter status
- **Pass condition**: the accepted pair both appear in the outbox under the
  same doc_type with title and tags populated in the staged frontmatter; the
  draft pair both appear in the manifest skip section with reasons citing
  their draft status.

## TC-006 — Doc-kind families map to the promised doc types
- **Story**: US1 · **Traces to**: FR-004
- **Given** one representative doc per family: an ADR/decision record, a
  FINDING note, a feature spec, a use-case doc, a proposal, a design doc, a
  guide, a runbook, a setup doc, a convention doc, a code standard, an
  agent-instruction doc, a vision doc, an architecture doc, a prior-art
  survey
- **When** the dry-run scan classifies them
- **Then** staged doc types follow the spec's map: ADR/decision/FINDING →
  `decision`; FEAT/UC/component spec/proposal/design → `spec`;
  guide/runbook/setup → `workflow`; convention/code-standard/
  agent-instruction → `business-rule`; vision/architecture/prior-art →
  `spec` plus a `reference` tag
- **Pass condition**: each staged doc's doc_type (manifest row and outbox
  location) matches the mapping above; the vision/architecture/prior-art
  docs additionally carry a `reference` tag in the staged frontmatter.

## TC-007 — Malformed frontmatter falls back instead of crashing
- **Story**: US1 (boundary) · **Traces to**: FR-004
- **Given** a doc whose frontmatter block is not valid YAML (unbalanced
  quotes, bad indentation) but whose body is a normal accepted-status doc
  with its `# Title` heading intact
- **When** the dry-run scan parses it
- **Then** the malformed frontmatter is treated as absent (D-007 layering):
  the fallback layers recover what they can and the doc is staged — never
  skipped merely because the frontmatter was unreadable — and the run
  continues
- **Pass condition**: exit 0; the doc is staged in the accepted section
  with a doc_type and its title recovered from the body's `#` heading; no
  traceback printed.

## TC-008 — Draft-family statuses are skipped with a logged reason
- **Story**: US1 · **Traces to**: FR-005
- **Given** five otherwise-good docs whose statuses are, respectively,
  draft, proposed, review, superseded, deprecated
- **When** the dry-run scan completes with no special flags
- **Then** all five are skipped, each with a reason citing its status, and
  none is staged
- **Pass condition**: manifest skip section contains all five with
  status-citing reasons; the outbox contains none of them.

## TC-009 — `--include-drafts` ingests draft docs tagged `draft`
- **Story**: US1 · **Traces to**: FR-005
- **Given** the same five draft-family docs as TC-008
- **When** the scan is re-run with `--include-drafts`
- **Then** they are accepted and staged, each carrying a `draft` tag in
  addition to its normal tags
- **Pass condition**: all five appear as accepted in the manifest; each
  staged file's tags include `draft`.

## TC-010 — Staged files are valid OKF with provenance
- **Story**: US2 · **Traces to**: FR-006, AC1 (US2-AC1)
- **Given** a completed dry run over a mixed corpus
- **When** the outbox is inspected
- **Then** every staged file has an OKF frontmatter block (type
  `Knowledge-{doc_type}`, title, description, resource, tags, generated
  by/at, okf_version, tier/doc_status/doc_source/affects_*) and a body with
  the source frontmatter stripped and a `Source:` provenance line, and lives
  at a `knowledge/{doc_type}/{slug}`-shaped path
- **Pass condition**: for each staged file: frontmatter parses as a YAML
  block between `---` fences; `type:` begins with `Knowledge-`; `resource`
  is non-empty; the body contains a line beginning `Source:`; the source
  doc's own frontmatter does not reappear in the body; the file's location
  matches its doc_type area.

## TC-011 — Manifest counts and skips agree with the staged outbox
- **Story**: US2 · **Traces to**: FR-006
- **Given** a completed dry run over a corpus spanning two repos and at
  least three doc types, with some skips
- **When** the manifest is compared against the outbox
- **Then** its counts by doc_type and by repo equal the actual staged
  files, and its skip list carries a reason per skip
- **Pass condition**: recount the staged files by type and by repo and
  compare with the manifest numbers (must be equal); every skip entry has a
  non-empty reason; accepted + skipped totals equal docs encountered.

## TC-012 — STANDING GUARD — without approval nothing reaches the store
- **Story**: US2 · **Traces to**: FR-008, AC2 (US2-AC2)
- **Given** a scratch workspace with the store listing recorded, and both a
  repo to scan and files to feed
- **When** the pipeline runs to completion with NO explicit approval — dry
  run only
- **Then** the knowledge store is untouched: identical listing count and
  contents as before, validation still clean; only the outbox was created
- **Pass condition**: capture `cairn knowledge list` before and after —
  byte-identical output; `cairn validate` exits 0; the outbox exists on
  disk. This case must fail if any store write ever occurs without explicit
  approval.

## TC-013 — A fed markdown file stages identically to the same file scanned
- **Story**: US3 · **Traces to**: FR-002, AC1 (US3-AC1)
- **Given** the same frontmatter-carrying markdown file processed in two
  separate dry runs: once placed inside a scanned repo's doc directory,
  once fed directly as a one-off file
- **When** both runs complete
- **Then** the file gets identical classification and staging treatment:
  same doc_type, both manifest rows accepted with the same classification,
  and staged bodies carrying the same frontmatter-stripped text. The
  stable-ID-derived fields (title `"{stable ID} — {frontmatter title}"`,
  slug, tag union, origin/affects) differ by construction — the two runs
  present different (repo, relpath) paths and FR-007/D-006 derive the
  stable ID from exactly that — so they are excluded from the comparison
  (each must still follow its own run's FR-007 pattern)
- **Pass condition**: compare the two staged results — doc_type matches;
  both manifest rows classify identically; staged file bodies are
  byte-identical apart from the `Source:` line's differing location;
  staged frontmatters differ only in the stable-ID-derived fields
  (title/slug/tag union/origin), each matching its own run's stable ID.

## TC-014 — Fed file with no frontmatter defaults to spec with a fed tag
- **Story**: US3 · **Traces to**: FR-002, AC2 (US3-AC2)
- **Given** a plain markdown file with neither frontmatter nor any status
  marker
- **When** it is fed to the pipeline
- **Then** it is staged — not silently dropped — classified as a `spec`
  with a `fed` origin tag
- **Pass condition**: manifest lists it as accepted with doc_type spec; the
  staged file's tags include `fed`; the file exists in the outbox.

## TC-015 — Fed directory walks its markdown through the same path
- **Story**: US3 · **Traces to**: FR-002
- **Given** a directory of mixed markdown docs (varied kinds, one draft),
  including nested subdirectories
- **When** the directory is fed
- **Then** every markdown file inside is processed exactly as a scan would:
  accepted docs staged with doc_type, the draft skipped with a status
  reason
- **Pass condition**: manifest accounts for each file in the directory
  (accepted with doc_type, or skipped with reason); nested subdirectory
  files are included in the accounting.

## TC-016 — Fed nonexistent path fails cleanly
- **Story**: US3 (boundary) · **Traces to**: FR-002
- **Given** a file path that does not exist
- **When** it is fed
- **Then** the command exits non-zero with a clear message naming the bad
  path, stages nothing, and touches no store
- **Pass condition**: exit code non-zero; an actionable message naming the
  path, no traceback; `cairn knowledge list` output unchanged.

## TC-017 — Text-based PDF converts and stages with converted provenance
- **Story**: US4 · **Traces to**: FR-003, AC1 (US4-AC1)
- **Given** a text-based PDF document, in an environment with the ingestion
  optional extra installed
- **When** it is fed
- **Then** a markdown conversion is staged, carrying a `converted`
  provenance tag, with the original PDF's path recorded as its `resource`
- **Pass condition**: manifest accepts it; the staged file's tags include
  `converted`; its frontmatter `resource` is the original PDF path; its
  body contains the PDF's text.

## TC-018 — Text-free PDF is skipped, never ingested empty
- **Story**: US4 · **Traces to**: FR-003, AC2 (US4-AC2)
- **Given** a PDF whose text extraction yields nothing usable (e.g. a
  scanned, image-only PDF)
- **When** it is fed
- **Then** it is skipped with a logged reason and no empty document is
  staged or ingested
- **Pass condition**: manifest skip section contains it with a reason
  mentioning unusable or empty extraction; the outbox has no file for it;
  store listing unchanged.

## TC-019 — docx converts through the same path as PDF
- **Story**: US4 · **Traces to**: FR-003
- **Given** a text docx document
- **When** it is fed
- **Then** it stages like the PDF path: markdown staged, `converted` tag,
  original path in resource, classified by its content
- **Pass condition**: accepted in the manifest; staged file's tags include
  `converted`; resource is the docx path; doc_type reflects the content's
  doc kind.

## TC-020 — Feeding a PDF without the optional extra degrades gracefully
- **Story**: US4 (boundary) · **Traces to**: FR-003
- **Given** an environment where the optional ingestion extra is NOT
  installed
- **When** a PDF is fed
- **Then** the command fails gracefully with an actionable message pointing
  at the optional extra; no traceback, nothing staged, store untouched
- **Pass condition** (manual): in a venv without the extra, run the feed;
  observe a clean error naming the optional install extra; exit non-zero;
  outbox and store unchanged.

## TC-021 — Conversion needs no system binaries
- **Story**: US4 · **Traces to**: FR-003
- **Given** a clean machine with none of the usual external document tools
  installed (no pandoc, libreoffice, or similar on PATH), with only the
  optional ingestion extra added on top of a normal install
- **When** a text PDF is fed
- **Then** conversion succeeds purely via the packaged extra
- **Pass condition** (manual): in a fresh environment, confirm the external
  tools are absent from PATH, install only the extra, then re-run TC-017's
  steps — they pass.

## TC-022 — Stable identity fields land in every staged doc
- **Story**: US1, US5 · **Traces to**: FR-007
- **Given** accepted docs with frontmatter titles and their own tags, from
  a known repo and doc directory
- **When** they are staged
- **Then** each staged doc's title follows the "{stable ID} — {frontmatter
  title}" pattern; its tags include the source doc's tags plus the stable
  ID and the origin repo; `affects_repos` is the origin repo;
  `affects_modules` reflects the source doc directory; `doc_source` is
  `imported`; and `description` is a real one-line description extracted
  from the doc, never a copy of the title. Re-running the dry run on the
  unchanged corpus produces the same staged file set
- **Pass condition**: inspect each staged file's frontmatter for every
  claim above; run the dry run twice and compare staged file names (and
  contents apart from generated timestamps) — identical.
- Survey baseline (FR-007): existing behavior defaults the description to
  the title (survey evidence: `description=title`); this case verifies the
  promised improvement and then guards it.

## TC-023 — Cross-repo title collisions get a repo-suffixed identity
- **Story**: US1, US5 · **Traces to**: FR-007
- **Given** two different repos, each containing a doc whose frontmatter
  title is identical
- **When** both are staged in one dry run
- **Then** both survive as distinct docs — the slug collision is resolved
  with a `({repo})` suffix — neither overwrites the other
- **Pass condition**: two staged files with distinct identities, each
  carrying its own repo's marker; both rows present in the manifest.

## TC-024 — Approved run writes, embeds, and verifies
- **Story**: US5 · **Traces to**: FR-008, AC1 (US5-AC1)
- **Given** a reviewed dry-run manifest that the operator now approves,
  running with the target workspace as the working directory
- **When** the approved ingestion completes
- **Then** the store listing count equals the manifest's accepted count,
  the embed step ran at batch size 32, and verification passed
- **Pass condition**: exit 0; `cairn knowledge list` count equals the
  manifest's accepted count; the run summary reports the embed step (batch
  size 32) and verify results; `cairn validate` exits 0; a smoke search
  (`cairn knowledge search "<distinctive phrase from an ingested doc>"`)
  returns that doc.
- Survey baseline (FR-008): the underlying add/embed/list subcommands
  already exist (survey evidence: `.venv/bin/cairn knowledge --help` →
  "Commands: add, embed, export, impact, import, list, remove, search,
  status, workflow"); this case guards the new orchestration on top.

## TC-025 — STANDING GUARD — identical re-run never duplicates
- **Story**: US5 · **Traces to**: FR-009, AC2 (US5-AC2)
- **Given** one completed approved ingestion, with the store listing
  recorded
- **When** the identical approved run executes a second time
- **Then** concept counts are unchanged: same listing count, same
  identities, no duplicated titles — stable identities overwrote in place
- **Pass condition**: `cairn knowledge list` before vs after: same count,
  same identities, no title appearing twice. This case must fail if a
  re-run ever creates a duplicate document.

## TC-026 — Regression guard — underlying store idempotency stays green
- **Story**: US5 · **Traces to**: FR-009
- **Given** the shipped codebase
- **When** the pre-existing store-level idempotency check runs
- **Then** it passes, proving the overwrite primitive the ingest pipeline
  relies on
- **Pass condition**: `.venv/bin/pytest
  tests/test_import_validation.py::TestImportDirectoryValidation::test_import_directory_normal_files_succeed -q`
  exits 0 (survey FR-009's verify command, recorded passing there; cited
  verbatim as the existing regression guard).

## TC-027 — Workspace classification override beats the built-in default
- **Story**: US1 · **Traces to**: FR-010
- **Given** a workspace whose configuration reclassifies a doc kind the
  built-in default maps to one doc_type (e.g. force the runbook family to
  `spec`)
- **When** a dry run scans a doc of that kind
- **Then** the override wins: the doc is staged as `spec`, not `workflow`
- **Pass condition**: with the override present, the manifest and staged
  doc_type are the overridden value; with the override removed, the same
  doc reverts to the built-in mapping (both runs observed).

## TC-028 — Workspace skip-list override beats the built-in default
- **Story**: US1 · **Traces to**: FR-010
- **Given** a workspace whose configuration both adds a skip pattern (e.g.
  skip everything under an `internal-notes/` directory) and un-skips a
  built-in category (e.g. accept changelogs)
- **When** a dry run scans docs hitting both rules
- **Then** the layering applies: the added pattern's files are skipped with
  reasons, and the formerly skipped category is now accepted
- **Pass condition**: with the config, the manifest skips the added
  pattern's files (with reasons) and accepts the changelog; a baseline run
  without the config shows the changelog skipped — the difference proves
  the override layer.

## TC-029 — A brand-new workspace works out of the box
- **Story**: US1–US5 · **Traces to**: FR-012
- **Given** a second, freshly initialized cairn workspace — not the
  development corpus's workspace, with no workspace-specific configuration —
  holding a small synthetic corpus of a few markdown docs
- **When** the full flow runs there: dry-run scan, review, approve
- **Then** the whole promise holds in that workspace: staging, manifest,
  store counts matching the manifest, searchable results
- **Pass condition**: in the new workspace the dry run exits 0 with a
  correct manifest; after approval `cairn knowledge list` count matches the
  manifest's accepted count; a smoke search returns an ingested doc.
- Survey baseline (FR-012): store/workspace resolution is already generic
  (survey FR-012); this case guards the ingest feature's out-of-the-box
  genericity on top of it.

## TC-030 — Scale: the reference corpus end to end (business target)
- **Story**: US1, US5 (business value) · **Traces to**: FR-008, FR-012
- **Given** the reference workspace the spec names — "~170 candidate docs
  across 15 repos" with zero knowledge docs ingested (spec, Business
  value; corpus assumptions per docs/polaris-doc-ingestion-pipeline.md:7:
  "Corpus assumptions: all 15 repos (~170 candidate md docs); drafts
  skipped; reference docs ingested as tagged `spec`")
- **When** the full pipeline runs there: scan dry run, review, approve,
  embed, verify
- **Then** every candidate is accounted for exactly (accepted + skipped
  equals the walked candidate count), the store count matches the
  manifest, and the spec's example smoke searches return the expected
  docs
- **Pass condition** (manual): dry-run manifest's accepted + skipped
  counts equal the walked candidate count exactly; the accepted count is
  recorded against the spec's "~140 classified in" assumption as a
  measurement (not a pass/fail margin); after approval `cairn knowledge
  list` count equals the manifest accepted count; `cairn knowledge search
  "llm gateway alias routing"` and `cairn knowledge search "contribution
  flow gate"` each return the expected documents.

## Coverage matrix
<!-- Every FR appears; `check.py` fails an FR with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001 | TC-002, TC-003, TC-004 | auto |
| FR-002 | TC-013, TC-014, TC-015, TC-016 | auto |
| FR-003 | TC-017, TC-018, TC-019, TC-020, TC-021 | auto + manual (020, 021) |
| FR-004 | TC-005, TC-006, TC-007 | auto |
| FR-005 | TC-008, TC-009 | auto |
| FR-006 | TC-010, TC-011 | auto |
| FR-007 | TC-022, TC-023 | auto |
| FR-008 | TC-012, TC-024, TC-030 | auto + manual (030) |
| FR-009 | TC-025, TC-026 | auto |
| FR-010 | TC-027, TC-028 | auto |
| FR-011 | TC-001 | auto |
| FR-012 | TC-029, TC-030 | auto + manual (030) |

No FR is untestable — every requirement has at least one observably
verifiable case on the CLI surface.

## Acceptance-criteria trace
| Story AC | Test cases |
|----------|------------|
| US1-AC1 | TC-002 |
| US1-AC2 | TC-005 |
| US2-AC1 | TC-010 |
| US2-AC2 | TC-012 |
| US3-AC1 | TC-013 |
| US3-AC2 | TC-014 |
| US4-AC1 | TC-017 |
| US4-AC2 | TC-018 |
| US5-AC1 | TC-024 |
| US5-AC2 | TC-025 |
