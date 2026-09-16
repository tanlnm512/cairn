# Tasks: temporal-memory

**Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)
Status reflects code state per [survey.md](survey.md), not intent.
**Before-audit**: pending — the orchestrator writes `passed @ <sha>` here

FR coverage: FR-001 → T001-T003 · FR-002 → T005-T007 · FR-003 → T009-T010 ·
FR-004 → T011 · FR-005 → T001, T004, T008. All statuses trace to survey.md:
S1 (columns) and S5 (successor signals) are TODO; S2/S3/S4 are DONE for
*existing* machinery only — every task below implements a gap those items
name (interval, successor link, build-time trigger, as-of, timeline), so all
start todo. Delivery: one PR per phase, branch `feat/temporal-memory`.

## Burndown
<!-- Recompute on every status change; `check.py` verifies the arithmetic. -->
| Phase | Total | Done |
|-------|-------|------|
| 1     | 4     | 0    |
| 2     | 4     | 0    |
| 3     | 2     | 0    |
| 4     | 1     | 0    |
| **Σ** | 11    | 0    |

## Phase 1: Temporal foundation (FR-001, FR-005)
<!-- Checkpoint: `grep -rn "valid_from\|valid_until" src/cairn/memory/ src/cairn/okf/` returns matches (inverts survey S1's empty result); migration test against a fixture store from the prior schema version passes with rows intact. -->
- [ ] T001 [P] Add `memory_validity` derived table to the schema — src/cairn/graph/schema.py `_apply_schema`: `CREATE TABLE IF NOT EXISTS memory_validity(concept_id PK, valid_from, valid_until, successor_symbol)` with indexes on both date columns, riding the additive-only pattern schema.py's own table comments establish ("Additive-only: plain CREATE TABLE IF NOT EXISTS"; re-verified at schema.py:110, :134, :224, :267, :351, :373); idempotent re-run. Verify before implementing: `grep -rn "valid_from\|valid_until\|as_of" src/cairn/memory/ src/cairn/okf/` → no matches (survey S1). (FR-001, FR-005)
- [ ] T002 Add shared validity write helper + capture stamping — new `write_validity(concept, *, valid_from, valid_until=None, successor_symbol=None)` in src/cairn/memory/store.py (next to `store_memory`; move to src/cairn/okf/bundle.py if the layering fits better — the name+signature are the contract, module is implementer's call), writing concept extensions (source of truth, via `read/write_concept` in src/cairn/okf/bundle.py) and mirroring the `memory_validity` row; wire `store_memory` to stamp `valid_from` = creation time on capture. Extensions are the established per-memory home (`c.extensions["stale"] = True` survey S2). Tests: capture stamps extensions + projection row. (after T001 — consumes the `memory_validity(concept_id PK, valid_from, valid_until, successor_symbol)` table from T001). (FR-001)
- [ ] T003 Add one-time backfill migration for existing rows — src/cairn/graph/schema.py pass modeled on `_maybe_backfill_fts` (schema.py:668, session grep): for every existing memory concept, write `valid_from` into extensions and insert the `memory_validity` row; `valid_until` stays NULL. `valid_from` source for pre-feature concepts is unknown — verify (concept metadata vs file mtime). Test against a fixture store from the prior schema version with rows intact (spec risk mitigation). (after T002 — consumes `write_validity` for the extensions+row write). (FR-001)
- [ ] T004 [P] Capture the FR-005 baseline latency numbers on main @ d18768c — protocol fixed in tech-spec.md § Latency benchmark (D-006): seeded `tmp_path` store, 1000 memory concepts, 50 fixed queries × 3 runs cold cache, p50/p95 for `recall_memory` and CLI `memory search`; record the Before column in tech-spec.md's FR-005 table. Benchmark on `:memory:`/`tmp_path` only — never the real `~/.cairn`. Measurement only, no shared files; must land before T005 or the before/after comparison is void. (FR-005)

## Phase 2: Point-in-time recall (FR-002, FR-005)
<!-- Checkpoint: `grep -n "as_of\|as-of" src/cairn/mcp_server/tools_memory.py src/cairn/cli/memory.py` shows the filter on both surfaces; on a seeded store, default recall omits an expired memory and `--as-of` before its `valid_until` returns it; the before/after benchmark table is present in tech-spec.md. -->
- [ ] T005 Add keyword-only `as_of` validity predicate to `search_memory` — src/cairn/memory/promotion.py:242: new optional keyword-only `as_of` (ISO date; None = now); the per-concept `_visible()` predicate extends to: visible at instant t iff `valid_from <= t` and (`valid_until` is NULL or `valid_until > t`); orthogonal to `include_superseded` (D-002: visibility = valid-at-instant AND (latest OR include_superseded)); lenient default for pre-feature concepts (pins `test_old_memory_without_is_latest_treated_as_latest`, tests/test_memory_lifecycle.py:121). Keep parameters keyword-only — footnote/leak tests stub `promotion.search_memory` with positional-shaped lambdas. Five production consumers inherit the filter (`recall_memory`, `memory_search`, `_search_memory` src/cairn/compass/router.py:265, `_retrieve_l4` src/cairn/eval.py:498, `explore` src/cairn/mcp_server/tools_graph.py:478) — verify eval fixtures don't pin invalidated memories. Lifecycle flag pins (tests/test_memory_lifecycle.py :107/:114) must stay green untouched. (after T003, T004 — consumes the extensions contract written by `write_validity` and backfilled by T003; baseline comparability from T004). (FR-002, FR-005)
- [ ] T006 Add optional `as_of` to MCP `recall_memory` — src/cairn/mcp_server/tools_memory.py:21, signature `def recall_memory(query: str, tier: str = "", include_superseded: bool = False) -> str:` grows keyword-only `as_of`, forwarded to `search_memory`; keep routing through `search_memory` (pinned by tests/test_mcp_connection_leaks.py:170) and footnote pins green (tests/test_mcp_degradation_footnote.py :223/:233). (after T005 — consumes `search_memory(..., as_of=None)` keyword-only parameter). Runs parallel with T007 (disjoint files). (FR-002)
- [ ] T007 Add `--as-of` option to CLI `memory search` — `@memory.command("search")` at src/cairn/cli/memory.py:101 (`memory_search` calls `search_memory` at :114): new `--as-of` option forwarded as `as_of`; default behavior = only currently-valid memories. Verify before implementing: `grep -n "@memory.command" src/cairn/cli/memory.py` (survey S3/S4 — subcommand is `search`, not `recall`). (after T005 — consumes `search_memory(..., as_of=None)` keyword-only parameter). Runs parallel with T006 (disjoint files); shares src/cairn/cli/memory.py with T011, which chains after it. (FR-002)
- [ ] T008 Record after-benchmark numbers + delta in tech-spec.md's FR-005 table — same D-006 protocol on the feature branch; fill After and Delta columns; regression bar: p95 delta beyond 2× run-to-run stddev of the before-run. (after T006, T007 — numbers are only comparable after the query-path changes land). (FR-005)

## Phase 3: Auto-invalidation (FR-003)
<!-- Checkpoint: fixture test — a renamed/removed referenced symbol triggers `valid_until` = build time plus a successor link where uniquely identifiable, and no link where ambiguous; `sed -n '31,66p' src/cairn/cli/validate.py` still delegates to `cairn.compass.critic.validate_paths` (extension, not parallel detector). -->
- [ ] T009 [P] Extend the `--mark` path of `validate_paths` to auto-invalidate — src/cairn/cli/validate.py:35 (`def validate_paths(db, knowledge, mark)`): after the existing `c.extensions["stale"] = True`, set `extensions["valid_until"]` to build time and update the `memory_validity` row via `write_validity`; keep the `stale` boolean, the non-zero exit contract, and the literal hook string `cairn validate-paths --mark` (pinned at tests/test_install_uninstall_fidelity.py:208) unchanged; no new command (D-003); recall-side STALE rendering stays display-only (D-004). Verify before implementing: `sed -n '31,66p' src/cairn/cli/validate.py`. Critic entry shape beyond `entry["concept_id"]`/`entry["verified"]` is unknown — verify whether entries carry the dead ref strings or the CLI must re-extract refs from the concept body. (after T003 — consumes `write_validity(concept, *, valid_from, valid_until=None, successor_symbol=None)` from T002/T003). Parallel with all Phase 2 tasks (disjoint files per plan map). (FR-003)
- [ ] T010 Resolve and record successor-symbol links at mark time — for each dead ref in the `--mark` path, candidate successors are live symbols sharing the dead symbol's identity anchors (file scope / qualified-name prefix + kind, D-005); exactly one candidate → record `extensions["successor_symbol"]` + `memory_validity.successor_symbol`; zero or multiple → no link (timeline renders dead ends honestly). Resolver is new code — survey S5: `grep -n "rename\|successor" src/cairn/graph/incremental.py` → no matches, so it derives from graph identity, not builder events; location unknown — verify (not an incremental-builder change). Fixture tests: unique rename → link; removed symbol → no link; ambiguous → invalidated but unlinked. (after T009 — same `--mark` branch in src/cairn/cli/validate.py; consumes the invalidation write from T009). (FR-003)

## Phase 4: Timeline & closeout (FR-004)
<!-- Checkpoint: `cairn memory timeline <symbol>` on a seeded store lists memories with validity intervals and successor links; full test suite green. -->
- [ ] T011 Add `cairn memory timeline <symbol>` subcommand — new `@memory.command("timeline")` in src/cairn/cli/memory.py (registry already carries `promote`, `decay` survey S3, `search` survey S4): resolve memories citing `<symbol>` via the same backtick-ref extraction the stale path uses, order by `valid_from`, render each memory's validity window, successor link, and current tier/score extensions; cite-scan reads every memory concept — keep it off the recall path (CLI-only cost); the `memory_refs` table's columns are unknown — verify before relying on it for symbol lookup. Includes fixture test (seeded store: intervals + successor links listed) and the full-suite green run for the phase checkpoint. (after T007 — shares src/cairn/cli/memory.py with T007's `--as-of` option; different functions, sequenced to avoid edit collisions per plan map). (FR-004)

## Conventions
- `- [ ]` todo · `(in-progress)` claimed · `- [x]` done + proof note:
      `done <date> — <test/command that proves it>`
- Dropped: `- [ ] ~~T004~~ dropped <date> (D-###)` — never delete the line;
  dropped tasks stay visible with the decision that killed them
- `[P]` = parallelizable (default — no shared files, no upstream task);
  chained tasks note `(after T###)` and name the exact interface they
  consume from their upstream — symbols, signatures, file formats; serial
  runs need a reason, parallel runs need none
- Fix rounds append `(fix <n>/5)` to the entry — the cap survives resume
  only if the count lives here, in the status holder. From round 2 on, an
  implementer's scratch note (what was tried, why it failed) may live at
  `notes/T###.md` — the one file an implementer may write under specs/,
  never read by check.py, never counted as status
- Every task cites its FR-###; tasks with no FR are scope creep — fix the
  spec first
