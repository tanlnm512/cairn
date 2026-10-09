# Test plan: comment-sweep

Business-level acceptance tests for the comment sweep. Commands run from the
repository root. The Codex sandbox cannot bind loopback addresses; the
full-suite standing verifies named below must run outside that sandbox even
when the product is healthy.

## TC-001 — Remaining grandfathered count is at most half the starting debt

- **Given** the landed shrink-only style gate and the completed sweep
- **When** the maintainer runs the comment-style gate
- **Then** the gate succeeds and reports at most 606 remaining violations.
  Boundary: 606 passes; 607 or more fails.
- **Story**: US1 · **Traces to**: FR-001 (US1/AC1)

**Pass condition**: `env UV_CACHE_DIR=/tmp/cairn-comment-sweep-qa make comment-style ARGS=--json | jq -e '(.ok == true) and (.remaining <= 606)'`

## TC-002 — Trims change no executable behavior

- **Given** uncommitted sweep edits in the working tree
- **When** the comment-only equivalence check runs
- **Then** it exits successfully: every changed source file differs only in
  comments or docstrings, with no renamed, added, removed, or altered
  executable statement.
- **Story**: US1 · **Traces to**: FR-001 (US1/AC2)

**Pass condition**: `bash -lc 'env UV_CACHE_DIR=/tmp/cairn-comment-sweep-qa make verify-no-code-change 2>&1 >/dev/null | sed -n "s/^  //p" | sort | diff -q - <(printf "src/cairn/cli/system/report.py\ntests/test_report.py\n" | sort)'`

Standing per-commit verify: after each intermediate trim commit, run
`env UV_CACHE_DIR=/tmp/cairn-comment-sweep-qa make verify-no-code-change REF=HEAD~1`.

## TC-003 — Re-derived baseline matches the trimmed source exactly

- **Given** the sweep has landed and the grandfather list was regenerated
  through the documented regeneration entry point
- **When** the stored grandfather list and the live comment-style gate are
  compared with the final source tree
- **Then** the stored list holds at most 606 entries, the live gate reports
  zero newly violating blocks and zero stale fingerprints, and both counts
  agree at or below 606.
- **Story**: US1 · **Traces to**: FR-005 (US1/AC1)

**Pass condition**: `jq -e '(.violations | length) <= 606' docs/audits/comment-style-baseline.json && env UV_CACHE_DIR=/tmp/cairn-comment-sweep-qa make comment-style ARGS=--json | jq -e '(.ok == true) and ((.new | length) == 0) and ((.stale | length) == 0) and (.remaining <= 606)'`

## TC-004 — Durable-rationale destination remains available

- **Given** a block carrying non-obvious engineering rationale queued for trim
- **When** the sweep prepares its migration
- **Then** the local learning recorder is installed and accepts the four
  documented learning types, so a migration has somewhere durable to land.
- **Story**: US2 · **Traces to**: FR-002 (US2/AC1)

**Pass condition**: `env UV_CACHE_DIR=/tmp/cairn-comment-sweep-qa .venv/bin/cairn memory record --help`

## TC-005 — Removed durable rationale is recallable after deletion

- **Given** a trimmed block that stated a non-obvious constraint or decision
- **When** an agent or maintainer later searches the learning store using the
  title keywords recorded for that migration, or opens the documented landing
  named for a docs migration
- **Then** the decision, pattern, mistake, or workaround is returned with its
  fact, `Why:`, and `How to apply:` content, or the docs landing contains the
  same rationale — no durable "why" silently disappears with the prose.
- **Story**: US2 · **Traces to**: FR-002 (US2/AC1)

**Pass condition**: `Human observation — for every migration in the delivery record, run env UV_CACHE_DIR=/tmp/cairn-comment-sweep-qa .venv/bin/cairn memory search "<title keyword>" or open the named docs landing and confirm the rationale is present.`

## TC-006 — Delivery record accounts for every migration

- **Given** the completed sweep and its delivery record
- **When** a reviewer reconciles the record against the trimmed blocks
- **Then** every memory or docs migration is listed exactly once with its
  destination, and the list contains no migration whose source prose remains
  in the source tree.
- **Story**: US2 · **Traces to**: FR-002, NFR-005 (US2/AC1)

**Pass condition**: `Human observation — the delivery record's migration list is complete, deduplicated, and reconciles one-to-one with the rationale blocks removed from the source.`

## TC-007 — History-only and obvious narration is deleted, not migrated

- **Given** a trimmed block that only narrates history, restates the adjacent
  code, or states the obvious
- **When** the reviewer inspects that trim and searches the delivery record
  and learning store for its text
- **Then** the block is gone from the source and no migration was created for
  it — the memory layer is not polluted with re-derivable narration.
- **Story**: US2 · **Traces to**: FR-002 (US2/AC2)

**Pass condition**: `Human observation — for a sample of history-only and obvious blocks, the prose is absent from source and absent from both the migration list and learning-store search results.`

## TC-008 — Core behavior stays green after the sweep

- **Given** the landed sweep
- **When** the fast core smoke selection runs
- **Then** every selected test passes, giving a bounded in-sandbox proof that
  the sweep did not break the core build/query/transport path.
- **Story**: US1 · **Traces to**: FR-003, NFR-004 (US1/AC2)

**Pass condition**: `env UV_CACHE_DIR=/tmp/cairn-comment-sweep-qa uv run --extra test pytest -q -m core`

## TC-009 — Full suite is green at every intermediate step

- **Given** each intermediate step recorded during the sweep
- **When** the project's per-change test selection runs against that step
- **Then** it is green with no failures or errors. This full-suite verify
  binds loopback addresses, so run it outside the Codex sandbox; a loopback
  `EPERM` there is an environment result, never a product failure.
- **Story**: US1 · **Traces to**: FR-003, NFR-004 (US1/AC2)

**Pass condition**: `Human observation — each intermediate step's recorded test run is green; the standing command is uv run --no-sync pytest -q -m "not infra" -n auto plus the project's infrastructure-test leg, executed outside the Codex sandbox.`

## TC-010 — Sweep touches only its contracted surfaces

- **Given** the file-level diff of any intermediate sweep step
- **When** a reviewer lists every path changed by that step
- **Then** each path is a source comment/docstring trim, a docs or delivery
  record, a memory record, the module-entry import wiring, or a test text-pin
  update forced by a trim in that same step; no other surface changes.
- **Story**: US1 · **Traces to**: FR-003 (US1/AC2)

**Pass condition**: `Human observation — for every intermediate step, git diff --name-only <previous-step>..<step> contains no path outside the contracted surfaces, and memory-record changes are reconciled through the delivery record.`

## TC-011 — Comment-style gate output is free of the double-import warning

- **Given** the module-entry fix has landed
- **When** the comment-style gate runs as a module
- **Then** its combined output contains no runtime double-import warning; the
  command deliberately fails if the warning reappears.
- **Story**: US3 · **Traces to**: FR-004 (US3/AC1)

**Pass condition**: `bash -lc 'env UV_CACHE_DIR=/tmp/cairn-comment-sweep-qa make comment-style ARGS=--json 2>&1 >/dev/null | grep -q RuntimeWarning && exit 1 || exit 0'`

## TC-012 — Audit-status gate output is free of the double-import warning

- **Given** the module-entry fix has landed
- **When** the audit-status gate runs as a module
- **Then** its combined output contains no runtime double-import warning; the
  command deliberately fails if the warning reappears.
- **Story**: US3 · **Traces to**: FR-004 (US3/AC1)

**Pass condition**: `bash -lc 'env UV_CACHE_DIR=/tmp/cairn-comment-sweep-qa make audit-status 2>&1 >/dev/null | grep -q RuntimeWarning && exit 1; exit ${PIPESTATUS[0]}'`

## TC-013 — Prose-pinned tests are updated, never deleted to pass

- **Given** an existing test that asserted text removed by a trim
- **When** the reviewer compares the test suite before and after that trim
- **Then** the coverage is updated in the same step to assert the surviving
  behavior or contract, and no test or assertion is removed merely to make
  the run green; unrelated coverage is unchanged.
- **Story**: US1 · **Traces to**: NFR-004 (US1/AC2)

**Pass condition**: `Human observation — each prose-pin update lands with its trim, still exercises the original observable contract, and no test disappears without a stated behavioral replacement.`

## TC-014 — Progress remains visible as a single count

- **Given** the completed sweep
- **When** the maintainer runs the plain comment-style gate
- **Then** human-readable output states the remaining grandfathered-violation
  count, so progress can be observed without parsing internals.
- **Story**: US1 · **Traces to**: NFR-005 (US1/AC1)

**Pass condition**: `env UV_CACHE_DIR=/tmp/cairn-comment-sweep-qa make comment-style | grep -E 'comment-style: [0-9]+ grandfathered violations remaining'`

## Testability notes

- FR-003's allowed-path list omits two surfaces the spec's Scope section
  includes: forced test text-pin updates and the release-note entry. TC-010
  treats those Scope-named surfaces as the intended exceptions; the spec
  owner should reconcile the wording.
- Memory records live in the local learning store outside the git worktree,
  so TC-010 reconciles them through the delivery record rather than the diff.

## Coverage matrix

| Requirement | TCs | Type |
|---|---|---|
| FR-001 | TC-001, TC-002 | Auto |
| FR-002 | TC-004, TC-005, TC-006, TC-007 | Mixed |
| FR-003 | TC-008, TC-009, TC-010 | Mixed |
| FR-004 | TC-011, TC-012 | Auto |
| FR-005 | TC-003 | Auto |
| NFR-001 | — | n/a (spec-scoped) |
| NFR-002 | — | n/a (spec-scoped) |
| NFR-003 | — | n/a (spec-scoped) |
| NFR-004 | TC-008, TC-009, TC-013 | Mixed |
| NFR-005 | TC-006, TC-014 | Mixed |
| NFR-006 | — | n/a (spec-scoped) |
