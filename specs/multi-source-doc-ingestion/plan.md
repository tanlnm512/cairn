# Plan: multi-source-doc-ingestion

**Spec**: [spec.md](spec.md) | **Created**: 2026-08-26

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | FRs | Depends on |
|-------|-----------|---------------------|-----|------------|
| 1 | Ingest core: parse → classify → identity → stage (fed markdown, stage-and-stop) | `cairn knowledge ingest --file/--dir <md>` runs the full staged path on fed markdown: frontmatter/status parsing, doc-kind→doc_type classification, draft-skip, stable identities, an OKF outbox + manifest JSON under a staging dir — and the knowledge store is untouched (no approval path exists yet). New ingest package + the `ingest` CLI subcommand skeleton. | FR-002, FR-004, FR-005, FR-006, FR-007, FR-011 | — |
| 2 | Repo doc-tree scan source adapter | Point the same pipeline at a repo: allowlist walk of doc dirs with skip-list (drafts, meeting notes, generated mirrors, changelogs, templates), every skip logged with a reason in the manifest. Demo: scan a fixture repo with ADRs + drafts → manifest lists accepted docs with doc_type and skips with reasons (US1). | FR-001 | Phase 1 |
| 3 | Checkpointed execution: approve → add → embed → verify | The approval path: re-run against a staged manifest with explicit go-ahead (`--ingest`) → the executor, running inside `cairn knowledge ingest` (already cwd = target workspace), writes each manifest row in-process via `add_document` — the same write chokepoint `cairn knowledge add` wraps; no per-row subprocess (D-003) — then `embed_knowledge(batch_size=32)`, then verify (list count vs manifest, `cairn validate`, smoke searches). Re-runs overwrite the same concept ids — counts unchanged (US5). Includes the one core write-path touch: an optional `description` parameter on `add_document` (backward compatible). | FR-008, FR-009 | Phase 1 |
| 4 | PDF/docx converter adapter behind `cairn[ingest]` | Fed binary documents: `.pdf`/`.docx` converted to markdown by a pip-installable converter with prebuilt wheels on all supported platforms, shipped as the `cairn[ingest]` optional extra (no system binaries), tagged `converted` with source provenance; garbage extraction → skipped with a logged reason (US4). **External gate: blocked until tech-spec.md records the converter choice as a C-04 D-### decision.** | FR-003 | Phase 1 + tech-spec C-04 D-### |
| 5 | Per-workspace config overrides + genericity | `cairn.json` grows an ingest section (classification rules, skip-list) layered over built-in defaults; demo on a fixture workspace where an override flips a classification/skip decision. Genericity proof: the pipeline runs unchanged on a second bare fixture workspace with no config and zero polaris-specific hard-coding (survey: satisfied by design if `resolve_store()` is used and no hard-coding; this phase verifies it). | FR-010, FR-012 | Phases 1, 2 |

FR coverage check: 001→P2 · 002,004,005,006,011→P1 · 003→P4 · 008,009→P3 · 010,012→P5 · 007→P1+P3 — each FR in exactly one phase except FR-007, which spans P1 (identity) + P3 (the `add_document` description param, per task.md Phase-3 header/T009); the table above keeps FR-007 in the Phase-1 row only so every FR stays in exactly one milestone row.

## Dependencies

```
M1 core ──┬──> M2 repo-scan ──> M5 config+genericity
          ├──> M3 execute+verify      (parallel with M2, M4)
          └──> M4 pdf/docx            (parallel with M2, M3; gated by tech-spec C-04 D-###)
M5 may overlap M3/M4 if they are still running (its earliest start is after M2)
```

- **M1 → everything**: M1 produces the source-adapter interface, the parse/classify/identity/stage engine, the manifest JSON schema, the new ingest package, and the `ingest` subcommand in `src/cairn/cli/knowledge.py`. Every later phase consumes or extends these.
- **M1 → M3 specifically**: M3 executes the manifest format M1 defines; the approval flag guards a pipeline that must already stage correctly.
- **M1 → M4**: converter output feeds M1's classifier; the converter registers behind M1's source-normalization dispatch.
- **M2 → M5**: FR-010 overrides layer *over built-in defaults* — the skip-list being overridden is M2's product (classification defaults come from M1).
- **External gate on M4**: research.md's converter options (pymupdf4llm AGPL vs markitdown MIT vs heavy ML runtimes) resolve only via the tech-spec C-04 D-### decision — C-04 forbids any new runtime dependency without it. M4 must not start before that decision lands in tech-spec.md.

**Plan assumptions** (marked per guardrail — tech-spec.md, written in parallel, owns module names and the converter choice):
- New code lands in a new ingest module under `src/cairn/knowledge/` (exact layout = tech-spec's call); this plan reasons about *areas* and the confirmed anchor files below.
- M3's write path follows tech-spec D-003 (the in-process alternative is the recorded choice, not a live option): the executor runs inside `cairn knowledge ingest` — already cwd = target workspace — resolves the store with the canonical `resolve_store()` pattern, and calls `add_document` per manifest row in-process, the same write chokepoint `cairn knowledge add` wraps; no per-row subprocess. That still requires passing an extracted description through to `add_document` — today `description=title` is hardcoded in `add_document` (`src/cairn/knowledge/store.py:151`, survey FR-007 evidence) with no parameter. M3 owns that small backward-compatible change (D-004) plus the corresponding CLI flag.
- M1's outbox staging serializes via `OKFConcept`/`to_markdown` reuse (`src/cairn/okf/concept.py`, read-only) — no `store.py` change in M1. Direct *store* writes stay forbidden; the outbox is not the store.

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->
Parallel is the default; serialization is the exception and each exception is justified below.

- **Independent: M2 repo-scan ∥ M3 execute+verify** — disjoint files: M2 adds a scan adapter inside the ingest package + its tests; M3 adds an executor module, touches `src/cairn/knowledge/store.py` (description param) + its tests. Neither reads the other's product (M3 consumes M1's manifest schema, not M2's walker). *One shared merge point:* both extend the `ingest` subcommand options in `src/cairn/cli/knowledge.py` (M2: repo-scan args; M3: approval flag). Keep each CLI delta to additive `@click.option` lines on the same function, land as separate commits; the second lander rebases. The survey confirms this file is a flat subcommand list (`src/cairn/cli/knowledge.py`, group at `knowledge()` + 9 subcommands) — additive edits, small conflict surface.
- **Independent: M4 converter ∥ M2 ∥ M3** — disjoint files: M4 adds a converter module + `pyproject.toml` `cairn[ingest]` extra (no other phase touches pyproject.toml; survey FR-003: extras mechanism at `pyproject.toml:65-137`, no `ingest` extra today) + one branch in the source-normalization dispatch (M1's module). It touches neither the scan adapter, the executor, `store.py`, nor `config.py`. Precondition: tech-spec C-04 D-### landed.
- **Independent: M5 config+genericity ∥ M3 ∥ M4** (earliest start after M2) — disjoint files: `src/cairn/graph/config.py` (`load_config` recognizes only `exclude`/`include`/`repo_namespaces`/`scip` today — survey FR-010; M5-only edit surface), plus override wiring in the classify/skip default modules from M1/M2. No overlap with the executor or converter files.
- **Strictly ordered: M1 → M2/M3/M4/M5** — M1 *is* the shared engine: the adapter interface, classifier, identity rules, manifest schema, and CLI skeleton all later phases code against. Parallel implementation of consumers against an engine that does not exist would invent the interface twice.
- **Strictly ordered: M2 → M5** — M5 layers per-workspace overrides over the built-in skip-list (FR-010); the built-in must exist first, and both touch the same skip-list module M2 creates.
- **Strictly ordered: tech-spec C-04 D-### → M4** — M4's first task adds the converter dependency; C-04 makes that illegal without the recorded decision.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
- **After Phase 1**: `cairn knowledge ingest` appears in help; feeding mixed fixture markdown (frontmatter YAML + inline `**Status:**` ADR styles, one draft) produces an outbox where every staged file is valid OKF with provenance (`resource`, `Source:` line, tags), a manifest with counts and skip reasons, and the knowledge store untouched (list count before == after). Verify:
  - `.venv/bin/cairn knowledge --help 2>&1 | grep ingest`
  - `.venv/bin/pytest tests/ -k ingest -q` (this phase's new tests; C-03 trace to FR-002/004/005/006/007)
  - `.venv/bin/pytest tests/test_import_validation.py tests/test_knowledge_status.py tests/test_knowledge_workflow.py -q` (regression baseline: 35 passed, survey)
- **After Phase 2**: scanning a fixture repo with `docs/` (ADRs + drafts + a generated mirror) lists accepted docs with doc_type and every skip with a reason (US1-AC1); both ADR styles classify identically (US1-AC2). Verify:
  - `.venv/bin/pytest tests/ -k 'ingest and scan' -q`
  - `grep -rn 'allowlist\|skip_list\|SKIP_LIST' src/cairn/knowledge/ --include='*.py'` (survey FR-001 verify — now matches with reason-logging)
- **After Phase 3**: an approved run writes every manifest row; `knowledge list` count == manifest accepted count; `knowledge embed` ran; `cairn validate` passes; a smoke search returns an ingested doc; a second identical run leaves concept counts unchanged (US5-AC1/AC2). No-approval runs still leave the store untouched. Verify:
  - `.venv/bin/pytest tests/ -k 'ingest and (execute or approve or idempotent)' -q`
  - `.venv/bin/pytest tests/test_redaction_chokepoints.py -q` (baseline 42 passed, survey — the write path still routes through `strip_private_data`)
  - end-to-end manual: dry-run → approve → list/validate/search → re-run → diff counts
- **After Phase 4**: a fed text-based PDF stages markdown tagged `converted` with the original path in `resource` (US4-AC1); a garbage-extraction PDF is skipped with a logged reason (US4-AC2); base install (without the extra) degrades gracefully. Verify:
  - `grep 'ingest' pyproject.toml` (survey FR-003 verify — the extra now exists)
  - `.venv/bin/pytest tests/ -k 'ingest and (pdf or docx or convert)' -q` (skips cleanly when the extra is absent — `importorskip` pattern)
- **After Phase 5**: a fixture workspace's `cairn.json` ingest section flips a classification rule and a skip-list entry; a bare second workspace runs on defaults alone; zero polaris-specific strings in the ingest code. Verify:
  - `grep -rn 'polaris' src/cairn/knowledge/ --include='*.py'` → empty
  - `grep -n '_EXCLUDE_KEY\|_SCIP_KEY' src/cairn/graph/config.py` (survey FR-010 verify — still matches, plus the new ingest key)
  - `make ci-local` green (full-gate before merge)

## Risks & mitigations
- Risk: Phase 1 is the largest phase (6 of 12 FRs) — the engine, staging format, manifest schema, and CLI land together. → Mitigation: it is the only strictly serial phase; the checkpoint is the smallest end-to-end demo (fed md only, no scan, no write), and everything after it parallelizes. The manifest schema and adapter interface are its most consequential products — review them hardest at this checkpoint.
- Risk: converter quality varies by library and document structure (spec risk; research.md: pdfminer-based markitdown scores worst-tier on headings/tables, pymupdf4llm is best-quality but AGPL). → Mitigation: converter isolated behind the M4 adapter with the skip-on-garbage gate (US4-AC2), and the C-04 D-### gate forces the license/quality tradeoff to be decided in tech-spec.md before M4 starts.
- Risk: AGPL contamination via PyMuPDF inside an MIT package (research.md RQ1 license analysis). → Mitigation: extra-isolation (`cairn[ingest]` optional, never core) is the established pattern; the D-### decision must record it.
- Risk: the M3 core touch (`add_document` description param) changes a shared write chokepoint. → Mitigation: optional parameter defaulting to current behavior (`description=None` → title), full regression suite (`test_import_validation.py`, `test_knowledge_status.py`, `test_redaction_chokepoints.py`) green at the checkpoint.
- Risk: classification heuristics misclassify unusual corpora (spec risk). → Mitigation: human checkpoint before any write (dry-run default) and M5's per-workspace overrides.
- Risk: embedding cost on large corpora (~170 docs target — corpus assumption from `docs/polaris-doc-ingestion-pipeline.md:7`). → Mitigation: batched embed (`embed_knowledge(batch_size=32)`, FR-008), manifest-scoped rows only.

## Delivery
Branch `feat/multi-source-doc-ingestion` (per spec). One conventional commit per task, code + tests together (C-03); `pre-commit run --all-files` before every commit (C-02). One rolling PR opened when Phase 1 is demoable and updated at each phase checkpoint, merged after Phase 5's `make ci-local`-green checkpoint (C-01). Per task: `cairn update` + `record_memory` after completion (C-05). Stanley is solo — waves below refer to parallel *agent* execution, not parallel humans:

- Wave 1 (solo): Phase 1
- Wave 2 (parallel): Phases 2 ∥ 3 ∥ 4 (4 gated on tech-spec C-04 D-###)
- Wave 3 (parallel with any wave-2 stragglers): Phase 5
