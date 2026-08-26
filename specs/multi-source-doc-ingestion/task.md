# Tasks: multi-source-doc-ingestion

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Before-audit**: passed @ 841f480 (2026-08-26 — baseline 2184 passed/1 skipped;
tree clean for plan scope; user WIP in agent_install/conftest adjudicated out)

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1 | 7 | 7 |
| 2 | 1 | 1 |
| 3 | 3 | 3 |
| 4 | 2 | 2 |
| 5 | 2 | 2 |
| **Σ** | 15 | 15 |

## Phase 1: Ingest core — parse, classify, identity, stage (FR-002, FR-004, FR-005, FR-006, FR-007, FR-011)
<!-- Checkpoint (plan.md): `cairn knowledge ingest` appears in help; feeding mixed fixture
     markdown (frontmatter YAML + inline `**Status:**` ADR styles, one draft) produces an
     outbox where every staged file is valid OKF with provenance (`resource`, `Source:`
     line, tags), a manifest with counts and skip reasons, and the knowledge store
     untouched. Verify: `.venv/bin/cairn knowledge --help 2>&1 | grep ingest`
     · `.venv/bin/pytest tests/ -k ingest -q`
     · `.venv/bin/pytest tests/test_import_validation.py tests/test_knowledge_status.py tests/test_knowledge_workflow.py -q`
     (regression baseline 35 passed, survey). Wave 1 (solo) — the engine every later
     phase codes against; T001/T002 are the only parallelizable pair. -->
- [x] T001 [P] Scaffold the ingest package and the fed-markdown source adapter — create `src/cairn/knowledge/ingest/__init__.py` (package docstring only; `run_ingest` lands in T006, layout per D-001) and `src/cairn/knowledge/ingest/adapters.py` defining the `SourceAdapter` protocol plus `FedMarkdownAdapter`, which iterates fed markdown files/dirs and yields one tuple per doc (FR-002)
  done 2026-08-26 — .venv/bin/pytest tests/test_ingest_staging.py -q (adapter contract cases)
  - Contract produced (consumed by T004, T006, T008, T013): every adapter yields `(repo: str, relpath: str, text: str, origin: str)`; T001's adapter sets `origin="fed"`.
  - Tests: adapter yield-contract cases as the first residents of `tests/test_ingest_staging.py` (fed fixture files under `tempfile.TemporaryDirectory()`, Area 6 patterns).
  - Survey FR-002 (TODO — gap): "No mechanism to feed individual files" — `import_directory` walks `*.md` with uniform doc_type and stem-derived titles (`src/cairn/knowledge/store.py:279`, `:295`).
  - Verify: `.venv/bin/pytest tests/ -k ingest -q`; proof-anchor grep: `grep -rn 'outbox\|manifest' src/cairn/knowledge/ --include='*.py'` (empty today, survey FR-006).
- [x] T002 [P] Build the source-doc parser — `src/cairn/knowledge/ingest/parser.py`: YAML frontmatter via core pyyaml (`pyyaml>=6.0`, `pyproject.toml:44`), a minimal-parser fallback for malformed YAML, and inline `**Status:**` / `## Status` markers; strips frontmatter from the body (FR-004)
  done 2026-08-26 — .venv/bin/pytest tests/test_ingest_parse_classify.py -q (parse half)
  - Contract produced (consumed by T003, T004, T005, T006): `parse_source_doc(text: str) -> ParsedDoc` with fields `title: str | None`, `status: str | None`, `tags: list[str]`, `description: str | None`, `body: str` (frontmatter-stripped).
  - New, separate function — the only existing splitter is OKF-internal (`_split_frontmatter`, `src/cairn/okf/concept.py:213-227`, parses OKF frontmatter only — survey FR-004 pitfall).
  - Tests: parse half of `tests/test_ingest_parse_classify.py` — frontmatter YAML and inline `**Status:**` ADR styles side by side both yield title/status/tags (US1-AC2).
  - Survey FR-004 (TODO — gap): no source-doc frontmatter parsing, no inline Status marker parsing, no minimal-parser fallback.
  - Verify: `.venv/bin/pytest tests/test_ingest_parse_classify.py -q`; proof-anchor grep: `grep -rn 'doc_type_map\|classify' src/cairn/knowledge/ --include='*.py'` (empty today, survey FR-004).
- [x] T003 (after T002) Build the classifier and draft-status gate — `src/cairn/knowledge/ingest/classifier.py`: built-in doc-kind → doc_type map (ADR/decision/FINDING → decision; FEAT/UC/component spec/proposal/design → spec; guide/runbook/setup → workflow; convention/code-standard/agent-instruction → business-rule; vision/architecture/prior-art → spec + reference tag), layered parse order per D-007 (frontmatter → filename/directory conventions → inline markers), and the status gate: draft/proposed/review/superseded/deprecated → skip with a logged reason; include_drafts=True → ingest with a `draft` tag (FR-004, FR-005)
  done 2026-08-26 — .venv/bin/pytest tests/test_ingest_parse_classify.py -q (classify half, zero skips)
  - Consumes from T002: `ParsedDoc.title/.status/.tags` (status gate + map input) plus the doc's `relpath` for filename/directory conventions; serial after T002 — it consumes the parser's output and appends to the same test file.
  - Contract produced (consumed by T005, T006, T015): `classify_doc(parsed: ParsedDoc, relpath: str, include_drafts: bool) -> Classification` with fields `doc_type: str`, `extra_tags: list[str]`, `skip_reason: str | None`; unmatched fed files default to doc_type spec (US3-AC2).
  - Orthogonal to the store's forward-only lifecycle (`DOC_STATUSES`, `src/cairn/knowledge/store.py:195` — survey FR-005).
  - Tests: classify half of `tests/test_ingest_parse_classify.py` — draft-skip with reason, include-drafts tagging, the doc-kind map table.
  - Survey FR-005 (TODO — gap): "No source-doc status parsing, no draft-skip logic, no --include-drafts flag."
  - Verify: `.venv/bin/pytest tests/test_ingest_parse_classify.py -q`.
- [x] T004 (after T001) (after T002) Derive stable identities — `src/cairn/knowledge/ingest/identity.py`: path-derived stable ID from repo + relpath (D-006), title `"{stable ID} — {frontmatter title}"` capped so the slug survives `slugify`'s 60-char truncation (`src/cairn/okf/utils.py:13-20`), `({repo})` suffix on cross-repo slug collisions, tags = source tags ∪ {stable ID, origin repo}, affects_repos = origin repo, affects_modules = source doc dir, doc_source = imported, and real description extraction (frontmatter description → first meaningful body paragraph → synthesized provenance line; never the title) (FR-007)
  done 2026-08-26 — .venv/bin/pytest tests/test_ingest_identity.py -q (14 passed)
  - Consumes from T001: the `(repo, relpath, text, origin)` tuple fields; from T002: `ParsedDoc.title/.tags/.description`.
  - Contract produced (consumed by T005, T006): `build_identity(repo: str, relpath: str, parsed: ParsedDoc) -> DocIdentity` with fields `stable_id, title, slug, tags, affects_repos, affects_modules, description`.
  - Survey FR-007 (PARTIAL — gap verbatim): "slugify exists but no stable-ID title pattern, no collision suffix, no tag union, no real description extraction (currently defaults to title)" — `add_document` hardcodes `description=title` at `src/cairn/knowledge/store.py:151`; the store-side param lands in T009 (Phase 3).
  - Tests: `tests/test_ingest_identity.py` — stable slugs, `({repo})` collision suffix, tag union, description differs from title.
  - Verify: `.venv/bin/pytest tests/test_ingest_identity.py -q`; proof-anchor grep: `grep -n 'description=title' src/cairn/knowledge/store.py` (matches `:151` today — survey FR-007).
- [x] T005 (after T003) (after T004) Stage the OKF outbox and manifest — `src/cairn/knowledge/ingest/staging.py`: one valid OKF markdown file per accepted doc at `knowledge/{doc_type}/{slug}` via `OKFConcept` + `to_markdown` reuse (`src/cairn/okf/concept.py:166-197` — set fields/extensions and let the serializer emit its fixed key order with `yaml.safe_dump(..., sort_keys=False)`; never hand-roll YAML); body = frontmatter-stripped text plus a `Source:` provenance line; plus `manifest.json` per the D-009 v1 schema (top level: version, generated_at, workspace, counts {accepted, skipped, by_type, by_repo}; accepted rows carry every `add_document` argument plus origin/repo/source_path and staged-file path; skipped rows carry source_path + skip reason) and…
  done 2026-08-26 — .venv/bin/pytest tests/test_ingest_staging.py -q (staging section)
  - Consumes from T001: the yield tuple for skip-row provenance; from T002: `ParsedDoc.body`; from T003: `Classification.doc_type/.extra_tags/.skip_reason`; from T004: all `DocIdentity` fields.
  - Contract produced (consumed by T006, T008, T010): `stage_outbox(entries, outbox_dir: Path) -> dict` returning the parsed manifest; cross-check each staged file's title/type against its row at staging time (D-009).
  - Survey FR-006 (TODO — gap verbatim): "No staging directory, no manifest JSON generation, no OKF outbox concept."
  - Tests: staging cases appended to `tests/test_ingest_staging.py` — staged files valid OKF with `Source:` line and provenance (US2-AC1).
  - Verify: `.venv/bin/pytest tests/test_ingest_staging.py -q`; proof-anchor grep: `grep -rn 'outbox\|manifest' src/cairn/knowledge/ --include='*.py'` (non-empty after).
- [x] T006 (after T005) Wire the stage-only pipeline — publish `run_ingest(...)` in `src/cairn/knowledge/ingest/__init__.py`: compose `FedMarkdownAdapter` → `parse_source_doc` → `classify_doc` (skips recorded with reasons) → `build_identity` → `stage_outbox`; stops after staging and never touches the store (dry-run default, US2-AC2) (FR-002, FR-006)
  done 2026-08-26 — .venv/bin/pytest tests/ -k ingest -q (e2e pipeline cases)
  - Consumes: T001 `FedMarkdownAdapter`, T002 `parse_source_doc`, T003 `classify_doc`, T004 `build_identity`, T005 `stage_outbox` — signatures exactly as pinned in those entries.
  - Contract produced (consumed by T007, T008, T010, T015): `run_ingest(files, dirs, outbox=None, include_drafts=False) -> dict` (the manifest); fed files with no status and no frontmatter default to operational spec with a `fed` origin tag (US3-AC2) and are staged, never dropped.
  - Tests: end-to-end fed-markdown cases appended to `tests/test_ingest_staging.py` — a fed file classifies/stages identically to a same-shape scanned file (US3-AC1); knowledge list count before == after (US2-AC2).
  - Verify: `.venv/bin/pytest tests/ -k ingest -q`.
- [x] T007 (after T006) Register the CLI subcommand — `knowledge_ingest` via `@knowledge.command("ingest")` on the existing Click group in `src/cairn/cli/knowledge.py` (group at `:12-15`): options `--file` (repeatable), `--dir`, `--include-drafts`, `--outbox`; delegates to `run_ingest` and prints counts + skip reasons; arg style follows existing patterns (`--file`/`--body` on add, comma-split `--tags`, `--batch-size` on embed) (FR-011, FR-005, FR-002)
  done 2026-08-26 — .venv/bin/cairn knowledge --help | grep ingest + CliRunner tests
  - Survey FR-011 (TODO — gap): help lists add, embed, export, impact, import, list, remove, search, status, workflow — no ingest; `scripts/ingest_docs.py` does not exist (subsumed by design).
  - Shared merge point: T008 and T010 later add options to this same function — keep this delta to additive `@click.option` lines, separate commits, second lander rebases (plan.md parallelization note).
  - Tests: `click.testing.CliRunner` invocation appended to `tests/test_ingest_staging.py` — help lists ingest; a run leaves the store untouched.
  - Verify: `.venv/bin/cairn knowledge --help 2>&1 | grep ingest` (survey FR-011 verify; plan.md Phase-1 checkpoint).

## Phase 2: Repo doc-tree scan source adapter (FR-001)
<!-- Checkpoint (plan.md): scanning a fixture repo with `docs/` (ADRs + drafts + a
     generated mirror) lists accepted docs with doc_type and every skip with a reason
     (US1-AC1); both ADR styles classify identically (US1-AC2). Verify:
     `.venv/bin/pytest tests/ -k 'ingest and scan' -q`
     · `grep -rn 'allowlist\|skip_list\|SKIP_LIST' src/cairn/knowledge/ --include='*.py'`.
     Wave 2 — parallel with Phases 3 and 4 (disjoint files; the only shared surface is
     additive options on `knowledge_ingest`, plan.md). -->
- [x] T008 (after T001) Build the repo-scan adapter — `RepoScanAdapter` in `src/cairn/knowledge/ingest/adapters.py`: allowlist walk of doc dirs with a built-in skip-list (drafts, meeting notes, generated mirrors, changelogs, templates), every skip logged with a reason into the manifest's skipped rows (T005's schema); plus repo-scan args on `knowledge_ingest` in `src/cairn/cli/knowledge.py` as additive `@click.option` lines (FR-001)
  done 2026-08-26 — .venv/bin/pytest tests/test_ingest_scan.py -q (6 passed)
  - Consumes from T001: the `SourceAdapter` protocol + `(repo, relpath, text, origin)` yield contract (`origin` = scanned repo name); from T005: the manifest skip-row shape (`source_path` + reason).
  - New skip-list contract for knowledge ingestion, distinct from the graph scanner's `DEFAULT_SKIP_DIRS` (`src/cairn/graph/scanner.py:102` — code indexing only, no reason logging; survey FR-001).
  - Survey FR-001 (TODO — gap verbatim): "No repo-scanning doc walker, no skip-list, no reason-logging for ingestion skips."
  - Tests: `tests/test_ingest_scan.py` — fixture repo with ADRs + drafts + a generated mirror: accepted rows carry doc_type, every skip carries a reason (US1-AC1/AC2).
  - Verify: `.venv/bin/pytest tests/ -k 'ingest and scan' -q`; proof-anchor grep: `grep -rn 'allowlist\|skip_list\|SKIP_LIST' src/cairn/knowledge/ --include='*.py'` (matches with reason-logging after).

## Phase 3: Checkpointed execution — approve, add, embed, verify (FR-007, FR-008, FR-009)
<!-- Checkpoint (plan.md): an approved run writes every manifest row; `knowledge list`
     count == manifest accepted count; `knowledge embed` ran; `cairn validate` passes; a
     smoke search returns an ingested doc; a second identical run leaves concept counts
     unchanged (US5-AC1/AC2); no-approval runs still leave the store untouched. Verify:
     `.venv/bin/pytest tests/ -k 'ingest and (execute or approve or idempotent)' -q`
     · `.venv/bin/pytest tests/test_redaction_chokepoints.py -q` (baseline 42 passed —
     the write path still routes through `strip_private_data`) · manual end-to-end:
     dry-run → approve → list/validate/search → re-run → diff counts.
     Wave 2 — parallel with Phases 2 and 4. -->
- [x] T009 [P] Add the optional description parameter to the write chokepoint — `add_document` in `src/cairn/knowledge/store.py` (signature `:93-106`): new `description=None` parameter with `description = description or title` (D-004), plus a matching `--description` flag on `cairn knowledge add` in `src/cairn/cli/knowledge.py`; default behavior bit-for-bit unchanged for all 25 existing callers (FR-007, FR-008)
  done 2026-08-26 — .venv/bin/pytest tests/test_add_document_description.py tests/test_redaction_chokepoints.py -q (44 passed)
  - Survey FR-007 (PARTIAL — gap): `src/cairn/knowledge/store.py:151` hardcodes `description=title`, no parameter; FR-008 (PARTIAL — gap): "Individual subcommands (add, embed, list) exist. No orchestration that chains them."
  - Redaction runs before slugify (`strip_private_data` at `src/cairn/knowledge/store.py:127-128`, defined in `src/cairn/memory/privacy.py:57-70`) — this task must not bypass or reorder it.
  - Tests: regression baselines stay green — `.venv/bin/pytest tests/test_import_validation.py tests/test_knowledge_status.py tests/test_knowledge_workflow.py -q` (35 passed) and `.venv/bin/pytest tests/test_redaction_chokepoints.py -q` (42 passed); plus a unit test that a passed description lands in the concept and None keeps today's behavior.
  - Verify: proof-anchor grep `grep -n 'description=title' src/cairn/knowledge/store.py` (matches `:151` today; after this task the assignment flows through the new parameter).
- [x] T010 (after T005) (after T009) Build the executor write path — `src/cairn/knowledge/ingest/executor.py`: `--ingest` approval gate; resolve the store with the canonical pattern `resolve_store()` / `store.ensure()` / `OKFBundle(str(store.knowledge))` (`src/cairn/cli/knowledge.py:44-46`), write each manifest row in-process via `add_document` (D-003 — the same function `cairn knowledge add` calls at `src/cairn/cli/knowledge.py:51`; no per-row subprocess), rows processed in sorted (repo, relpath) order, then `embed_knowledge(conn, bundle, batch_size=32)` (`src/cairn/graph/embeddings.py:1263`, connection wired as the `knowledge embed` handler does at `src/cairn/cli/knowledge.py:138-185`); add the `--ingest` flag to `knowledge_ingest` as additive `@click.option` lines (FR-008, FR-009)
  done 2026-08-26 — .venv/bin/pytest tests/test_ingest_execute.py -q (write+embed+--ingest)
  - Consumes from T005: the D-009 manifest row fields (concept_id, title, doc_type, tags, description, resource, affects_repos, affects_modules, origin/repo/source_path, body with `Source:` line); from T009: the `add_document(..., description=None)` signature.
  - Survey FR-008 (PARTIAL — gap): "No dry-run default / approve gate. No verify step" — the chained orchestration is exactly this task plus T011.
  - Tests: `tests/test_ingest_execute.py` — approval gate (no `--ingest` leaves the store untouched, US2-AC2); approved run writes every row and embeds (US5-AC1).
  - Verify: `.venv/bin/pytest tests/ -k 'ingest and execute' -q`.
- [x] T011 (after T010) Add the verify step and idempotency proof — extend `src/cairn/knowledge/ingest/executor.py`: after write+embed, verify via `list_documents` count vs manifest accepted count (`src/cairn/knowledge/store.py:162-183`), `cairn validate` (`src/cairn/cli/validate.py:11-25`), and smoke `search_knowledge` calls; report counts, skips, verify result to the operator; a second identical run leaves concept counts unchanged (FR-008, FR-009)
  done 2026-08-26 — .venv/bin/pytest tests/test_ingest_execute.py -q (verify+idempotency, 9 passed)
  - Consumes from T010: the executor's write-path entry point and its manifest handle.
  - Survey FR-009 (PARTIAL — gap): "no explicit dedup check at the ingest pipeline level … idempotency is only proven for the underlying primitives, not for the pipeline as a whole" — slug determinism (`slugify` → `concept_id` at `src/cairn/knowledge/store.py:131-133`) and atomic overwrite (`os.replace`, `src/cairn/okf/concept.py:159`) already exist; embeddings are keyed by (doc_id, model) so re-embed after overwrite skips by design — do not assert re-embedding happened.
  - Tests: appended to `tests/test_ingest_execute.py` — list count == manifest count, validate passes, smoke search returns an ingested doc, re-run count-stable (US5-AC1/AC2).
  - Verify: `.venv/bin/pytest tests/ -k 'ingest and (execute or approve or idempotent)' -q`.

## Phase 4: PDF/docx converter behind cairn[ingest] (FR-003)
<!-- External gate (plan.md): M4 blocked until tech-spec.md records the converter
     choice as a C-04 D-### decision — DISCHARGED: tech-spec.md D-002 ("Converter =
     pymupdf4llm (PDF) + mammoth + markdownify (docx) behind `cairn[ingest]` — C-04
     discharge") is on disk; this phase may start. Wave 2 — parallel with Phases 2/3.
     Checkpoint (plan.md): a fed text-based PDF stages markdown tagged `converted` with
     the original path in `resource` (US4-AC1); a garbage-extraction PDF is skipped with
     a logged reason (US4-AC2); base install without the extra degrades gracefully.
     Verify: `grep 'ingest' pyproject.toml` · `.venv/bin/pytest tests/ -k 'ingest and
     (pdf or docx or convert)' -q` (skips cleanly when the extra is absent). -->
- [x] T012 [P] Declare the cairn[ingest] optional extra — extras block in `pyproject.toml` (`:65-137`; watch, test, dev, semantic, ann, scip, otlp exist, no ingest today): `pymupdf4llm>=1.28` + `mammoth>=1.11` + `markdownify` per D-002; converter deps never land in core, so the base distribution is unchanged (FR-003)
  done 2026-08-26 — grep ingest pyproject.toml + tomllib parse + extras install
  - Survey FR-003 (TODO — gap): `grep -rn 'pdf\|docx' src/cairn/ --include='*.py'` empty and `grep 'ingest' pyproject.toml` empty (exit 1) today.
  - Verify: `grep 'ingest' pyproject.toml` (non-empty after; plan.md Phase-4 checkpoint anchor).
- [x] T013 (after T001) (after T012) Build the binary-document converter — `src/cairn/knowledge/ingest/convert.py`: pdf/docx → markdown behind lazy imports of the `cairn[ingest]` extra (never at module import time — base installs and CI without the extra must import the ingest package cleanly); register the binary branch in the `SourceAdapter` dispatch in `src/cairn/knowledge/ingest/adapters.py`; missing extra → skip with reason "cairn[ingest] not installed"; garbage/empty extraction → skip with a logged reason; accepted conversions tagged `converted` with `resource` = original path (FR-003)
  done 2026-08-26 — .venv/bin/pytest tests/test_ingest_convert.py -q (real PDF conversion)
  - Consumes from T001: the `SourceAdapter` yield contract — converted docs enter the same parse/classify/stage engine as `(repo, relpath, text=converted markdown, origin="converted")`; from T012: the extra's importable modules.
  - Converter composition per D-002: pymupdf4llm for PDF; mammoth HTML → markdownify for docx (mammoth's own Markdown output is deprecated — go HTML→markdown, not its markdown converter).
  - Tests: `tests/test_ingest_convert.py` with `pytest.importorskip` — text-based PDF stages markdown tagged `converted` with original path in `resource` (US4-AC1); garbage extraction skips with a reason (US4-AC2); extra-absent skips cleanly.
  - Verify: `.venv/bin/pytest tests/ -k 'ingest and convert' -q`; proof-anchor grep: `grep -rn 'pdf\|docx' src/cairn/ --include='*.py'` (empty today, survey FR-003).

## Phase 5: Per-workspace config overrides + genericity (FR-010, FR-012)
<!-- Earliest start after Phase 2 (M2 → M5: overrides layer over M2's built-in
     skip-list); otherwise parallel with Phase 3/4 stragglers. Wave 3.
     Checkpoint (plan.md): a fixture workspace's cairn.json ingest section flips a
     classification rule and a skip-list entry; a bare second workspace runs on
     defaults alone; zero polaris-specific strings in ingest code. Verify:
     `grep -rn 'polaris' src/cairn/knowledge/ --include='*.py'` (empty)
     · `grep -n '_EXCLUDE_KEY\|_SCIP_KEY' src/cairn/graph/config.py` (still matches,
     plus the new ingest key) · `make ci-local` green. -->
- [x] T014 (after T008) Recognize the ingest config key — `load_config` in `src/cairn/graph/config.py` (function at `:69`; reads `root / "cairn.json"` at `:77`): add `_INGEST_KEY = "ingest"` in the existing key style (`:63-66`) parsed into a new raw-dict `CairnConfig` field (D-005); the 6 existing callers (`_load_namespaces`, `_build_config_spec`, `_build_graph_impl`, the `config` CLI command, 2 tests) stay unaffected; tolerate malformed ingest sections the way `test_config_repo_namespaces_malformed_is_ignored` tolerates malformed namespaces (FR-010)
  done 2026-08-26 — .venv/bin/pytest tests/test_ingest_config.py tests/test_cross_repo_namespaces.py -q
  - Survey FR-010 (TODO — gap verbatim): "cairn.json loading mechanism exists but only recognizes graph-scoped keys. No knowledge/ingest config keys (skip-list overrides, classification overrides)."
  - Tests: key-parsing + malformed-tolerance cases opening `tests/test_ingest_config.py`.
  - Verify: `grep -n '_EXCLUDE_KEY\|_SCIP_KEY' src/cairn/graph/config.py` (matches today; plus the new ingest key after — survey FR-010 verify).
- [x] T015 (after T014) Layer workspace overrides over built-in defaults and prove genericity — wire the typed ingest config into `classifier.py` (classification-rule overrides) and the skip-list in `RepoScanAdapter` (`adapters.py`): workspace config layers over built-in defaults for both; built-ins must run with no config file at all; prove the pipeline runs unchanged on a second bare fixture workspace with zero corpus-specific hard-coding (FR-010, FR-012)
  done 2026-08-26 — .venv/bin/pytest tests/test_ingest_config.py -q (overrides) + grep -rn polaris src/cairn/knowledge/ (empty)
  - Consumes from T014: the `CairnConfig.ingest` raw dict (shape: classification-rule overrides + skip-list entries, typed inside the ingest package); from T003: `classify_doc`'s built-in map; from T008: the built-in skip-list being overridden.
  - Survey FR-012 (PARTIAL — gap verbatim): "no ingest-specific code exists to be generic or polaris-specific — this FR will be satisfied by design if the implementation uses the existing resolve_store() and avoids polaris hard-coding" — store resolution is already generic via `resolve_workspace()` / `resolve_store()` (`src/cairn/paths.py:160`, `:180`).
  - Tests: appended to `tests/test_ingest_config.py` — an override flips one classification and one skip decision; a bare workspace with no config runs on defaults alone.
  - Verify: `grep -rn 'polaris' src/cairn/knowledge/ --include='*.py'` (empty — plan.md Phase-5 checkpoint).

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      done `<date>` — `<test/command>` that proves it
- Dropped: `- [ ] ~~T004~~ dropped <date> (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)` and name the exact interface they
  consume from their upstream — symbols, signatures, file formats; serial
  runs need a reason, parallel runs need none
- Fix rounds append `(fix <n>/5)` to the entry — the cap survives resume
  only if the count lives here, in the status holder
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
