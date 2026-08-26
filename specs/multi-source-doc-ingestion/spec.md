# Spec: multi-source-doc-ingestion

**Status**: approved     <!-- draft while writing → approved at the Stage-4 user gate
                                → active once the first task spawns → done when all
                                tasks are ticked and `check.py` re-run green -->
**Created**: 2026-08-26
**Branch**: `feat/multi-source-doc-ingestion`

## What
A document-ingestion pipeline that turns heterogeneous documentation sources —
repository doc trees, ad-hoc fed markdown files, and fed binary documents
(PDF/docx) — into OKF-format knowledge documents inside a cairn knowledge
store. Every source passes one staged path: normalize to markdown → parse &
classify → stage an OKF outbox with a dry-run manifest → human checkpoint →
`knowledge add` → `knowledge embed` → verify.

## Why
`cairn knowledge import` is unusable for real mixed corpora: it applies one
uniform doc_type to all files, derives titles from filename stems, ignores
frontmatter, has no dedup, and sweeps in drafts/generated files. Meanwhile the
best documentation (ADRs, specs, conventions, runbooks) lives outside the
knowledge layer, so `knowledge_search`/`ask_compass` return nothing for it and
the `affects_repos` graph bridge never fires. The polaris workspace alone has
~170 candidate docs across 15 repos and zero knowledge docs ingested (corpus
assumption per `docs/polaris-doc-ingestion-pipeline.md`; counts are confirmed
or corrected by the first dry run).

## Business value
Workspace documentation becomes first-class cairn knowledge: semantically
searchable, tag-filterable, and graph-bridged. Success is measured by: docs
ingested per workspace (target for polaris: ~170 candidates → ~140 classified
in — assumption-derived from `docs/polaris-doc-ingestion-pipeline.md`, to be
recorded as a measurement at first run, not a hard pass margin), zero
duplicates on re-run, and smoke searches ("llm gateway alias
routing", "contribution flow gate") returning the expected docs after embed.

## User stories
<!-- Ordered by priority; each independently demoable. -->
### US1 — Ingest a workspace's doc corpus by repo scan (P1)
As a workspace owner, I want to point the ingester at my repos and have it
walk allowlisted doc dirs with a skip-list, so that the ground-truth docs are
collected without drafts, meeting notes, or generated mirrors.

**Acceptance criteria** (each traces to an FR below):
- AC1: Given a repo with `docs/` containing ADRs and drafts, When a dry-run
  scan completes, Then the manifest lists accepted docs with doc_type and
  lists each skipped file with a reason.
- AC2: Given frontmatter YAML and inline `**Status:**` ADR styles side by
  side, When parsed, Then both yield title, status, tags, and a doc_type.

### US2 — Stage and review before any write (P1)
As a cautious operator, I want the pipeline to stage OKF markdown files plus a
manifest and stop, so that nothing reaches the store before I review and
explicitly approve.

**Acceptance criteria**:
- AC1: Given a completed dry-run, When I inspect the outbox, Then every staged
  file is valid OKF (frontmatter + body, `knowledge/{doc_type}/{slug}` path
  shape) with provenance (`resource`, `Source:` line, tags).
- AC2: Given no explicit go-ahead, When the pipeline finishes, Then the
  knowledge store is untouched.

### US3 — Feed markdown files directly (P1)
As a contributor, I want to feed individual `.md` files or directories to the
ingester, so that one-off docs land in the same staged pipeline without a repo
scan.

**Acceptance criteria**:
- AC1: Given a fed `.md` file with frontmatter, When ingested, Then it is
  classified and staged identically to a scanned file of the same shape.
- AC2: Given a fed file with no status and no frontmatter, When ingested,
  Then it defaults to operational `spec` with a `fed` origin tag and is
  staged (not silently dropped).

### US4 — Feed PDF/docx documents (P2)
As a contributor, I want to feed PDF or docx files, so that legacy binary
documents are converted to markdown and enter the same pipeline.

**Acceptance criteria**:
- AC1: Given a text-based PDF, When fed, Then a markdown conversion is staged
  with a `converted` provenance tag and the original path in `resource`.
- AC2: Given a PDF whose text extraction yields no usable text, When fed,
  Then the file is skipped with a logged reason (no empty doc ingested).

### US5 — Idempotent execution into the store (P1)
As an operator, I want approved ingestion to write via `knowledge add`, embed,
and verify, so that re-runs are safe and the result is provably searchable.

**Acceptance criteria**:
- AC1: Given an approved manifest, When ingestion completes, Then
  `knowledge list` count matches the manifest and `knowledge embed` has run.
- AC2: Given a second identical run, When it completes, Then concept counts
  are unchanged (stable slugs overwrite, never duplicate).

## Requirements
<!-- EARS-shaped SHALL statements. Standing requirements use the
     ubiquitous pattern; the rest use WHEN / IF … THEN / WHERE patterns. -->
- **FR-001**: The system shall ingest documentation from repository doc trees
  via an allowlist walk with a skip-list (drafts, meeting notes, generated
  mirrors, changelogs, templates) where every skip is logged with a reason.
- **FR-002**: The system shall ingest directly-fed markdown files and
  directories through the same parse/classify/stage path as scanned files.
- **FR-003**: The system shall convert fed PDF and docx documents to markdown
  before classification, tagging them `converted` with source provenance,
  using a pip-installable converter with prebuilt wheels on all supported
  platforms, shipped behind an optional install extra (`cairn[ingest]`) — no
  system binaries required (converter library choice recorded as a C-04
  D-### decision in tech-spec.md).
- **FR-004**: The system shall parse YAML frontmatter (with a minimal-parser
  fallback) and inline `**Status:**`/`## Status` markers, classifying each doc
  via a doc-kind → doc_type map: ADR/decision/FINDING → `decision`; FEAT/UC/
  component spec/proposal/design → `spec`; guide/runbook/setup → `workflow`;
  convention/code-standard/agent-instruction files → `business-rule`;
  vision/architecture/prior-art → `spec` + `reference` tag.
- **FR-005**: The system shall skip any document whose status is
  `draft/proposed/review/superseded/deprecated`, logging a reason per skip;
  WHERE `--include-drafts` is passed, the system shall ingest them with a
  `draft` tag.
- **FR-006**: The system shall stage an OKF outbox — one valid OKF markdown
  file per accepted doc (frontmatter per cairn's OKF serializer contract:
  `type: Knowledge-{doc_type}`, title, description, resource, tags,
  generated{by,at}, okf_version, tier/doc_status/doc_source/affects_* — and
  body with frontmatter stripped and a `Source:` provenance line) — plus a
  manifest JSON with counts by type/repo and skips with reasons.
- **FR-007**: The system shall derive stable identities: title
  `"{stable ID} — {frontmatter title}"` slugified deterministically, `({repo})`
  suffix on cross-repo slug collisions, tags = source tags ∪ {stable ID,
  origin repo}, `affects_repos` = origin repo, `affects_modules` = source doc
  dir, `doc_source` = `imported`, and a real one-line description extracted
  from the doc (never defaulting to the title).
- **FR-008**: WHEN no explicit ingest approval is given, THEN the system shall
  stop after staging (dry-run is the default); WHEN approval is given, THEN
  the system shall write each manifest row through the `knowledge add` write
  path — in-process via `add_document`, the same chokepoint (D-003; cwd =
  target workspace so the right store resolves) — then embed in-process
  (`embed_knowledge`, batch size 32), then verify (list count vs manifest,
  `cairn validate`, smoke searches).
- **FR-009**: The system shall be idempotent: re-runs overwrite the same
  concept ids (slug-stable) and never create duplicate documents.
- **FR-010**: The system shall accept per-workspace configuration overrides
  (classification rules, skip-list entries), layered over the built-in
  defaults, WHERE a rule is workspace-specific. Layering semantics (D-005):
  classification entries add to and refine the built-in doc-kind map;
  skip-list entries add patterns AND can disable built-in skip categories
  (e.g. re-admit changelogs — TC-028), so overrides are not additive-union
  only.
- **FR-011**: The system shall ship as a cairn CLI subcommand
  (`cairn knowledge ingest …`) in this repo, subsuming the polaris-only
  `scripts/ingest_docs.py` plan.
- **FR-012**: The system shall be generic for any cairn workspace out of the
  box: built-in doc-kind→doc_type defaults with per-workspace config
  overrides (FR-010); polaris is the first real corpus, not a compile-time
  target.

## Scope
**In**: source adapters (repo scan, fed md, fed pdf/docx); normalization &
classification; OKF outbox staging + manifest; checkpointed execution via
`knowledge add`/`embed`/verify; per-workspace config overrides; dry-run
default.
**Out (deferred)**: changes to cairn core (`knowledge import` frontmatter
awareness, `--status` flag on add, embed enqueueing — the existing backlog);
URL/Confluence/Jira source adapters; OCR for scanned PDFs; task-queue
(compass/wiki) synthesis; dedup of near-identical content across repos.

## Assumptions & risks
- Assumption (clarify pass 2026-08-26, orchestrator-chosen — user did not
  answer; revisit at the approval gate): tool lives as a `cairn knowledge
  ingest` subcommand (FR-011); v1 is generic, not polaris-first (FR-012);
  PDF conversion is a pip-installable optional extra (prebuilt wheels, no
  system binaries), no pandoc (FR-003).
- Assumption: the target store is a cairn workspace bundle
  (`~/.cairn/<store_key>/.knowledge`); the tool runs with the workspace as
  cwd so CLI store resolution applies.
- Assumption: `knowledge add` remains the single write chokepoint (privacy
  redaction + canonical serialization for free) — direct bundle writes stay
  forbidden.
- Assumption: PDFs are text-based; OCR is out of scope (FR-003 skip path).
- Risk: PDF→md conversion quality varies by library and document structure —
  mitigation: converter isolated behind an adapter with a skip-on-garbage
  gate (AC US4-2) and the converter choice recorded as a D-### (C-04).
- Risk: classification heuristics misclassify unusual corpora — mitigation:
  per-workflow config overrides (FR-010) and the human checkpoint before any
  write.
- Risk: embedding cost on large corpora — mitigation: batched embed
  (--batch-size 32) and manifest-scoped rows.
