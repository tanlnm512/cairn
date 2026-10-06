# Test Cases: grade-a-guardrails

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-05

Black-box, business-language verification traced to every functional
requirement and applicable quality attribute. Doctor cases run only against a
disposable agent-client profile. No case requires the test itself to bind a
loopback endpoint; where an implementation fixture starts a healthy local
endpoint, run that pass condition outside the Codex sandbox because loopback
binds can fail there even when the product is healthy.

## TC-001 — A dead cairn-owned SSE registration is repaired safely
- **Story**: US1 · **Traces to**: FR-001, NFR-001, NFR-004, NFR-005, AC1
- **Given** a disposable profile containing one cairn-owned SSE registration
  whose endpoint refuses connections
- **When** the user runs the healing doctor in JSON mode
- **Then** that registration is repointed to its equivalent stdio
  registration, the prior configuration is backed up before the rewrite, the
  final configuration is complete rather than partial, and the summary names
  the action plus the stdio-versus-shared-daemon tradeoff
- **Pass condition**: `.venv/bin/cairn doctor --fix --json`

## TC-002 — A healthy environment produces zero actions
- **Story**: US1 · **Traces to**: FR-001, AC2
- **Given** a disposable healthy profile with an unchanged configuration
  snapshot
- **When** the user runs the healing doctor in JSON mode
- **Then** no configuration changes, no backup is created, the action count is
  zero, and the command does not report a remediation
- **Pass condition**: `.venv/bin/cairn doctor --fix --json`

## TC-003 — A recently changed configuration is refused
- **Story**: US1 · **Traces to**: FR-001, NFR-004, AC3
- **Given** a cairn-owned registration is stale but its configuration was
  modified inside the freshness window
- **When** the healing doctor considers a rewrite
- **Then** that configuration is not written or backed up, the summary gives
  guidance explaining the refusal and how to proceed, and no partial stdio
  registration is left behind
- **Pass condition**: `.venv/bin/cairn doctor --fix --json`

## TC-004 — Registrations outside cairn's ownership remain untouched
- **Story**: US1 · **Traces to**: FR-001, NFR-001, AC1
- **Given** one stale registration owned by cairn and one stale registration
  owned by another product
- **When** the healing doctor runs
- **Then** only the cairn-owned registration changes; the other registration
  and its containing configuration remain byte-for-byte unchanged and the
  summary identifies it as out of scope
- **Pass condition**: `.venv/bin/cairn doctor --fix --json`

## TC-005 — Healing does not rewrite unrelated doctor findings
- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** an environment with a remediable registration and at least two
  unrelated findings with different severities
- **When** the healing doctor runs
- **Then** the unrelated finding names, severities, details, and order are the
  same as before; only the new remediation action is added
- **Pass condition**: `.venv/bin/cairn doctor --json && .venv/bin/cairn doctor --fix --json && .venv/bin/cairn doctor --json`

## TC-006 — Secret-bearing configurations are repaired without disclosure
- **Story**: US1 · **Traces to**: NFR-001
- **Given** a cairn-owned stale configuration contains a nonempty credential
  and an unrelated secret-bearing configuration exists
- **When** the healing doctor runs in normal and diagnostic output modes
- **Then** the owned configuration is repaired, its backup retains the
  credential only in the private local backup, and neither command output nor
  diagnostic logs disclose either secret or copy them to a new location
- **Pass condition**: `.venv/bin/cairn doctor --fix --json`

## TC-007 — Healing is idempotent
- **Story**: US1 · **Traces to**: NFR-004
- **Given** the healing doctor has already repaired every actionable stale
  registration
- **When** the user runs it a second time
- **Then** the second summary reports zero actions and creates no additional
  backup or configuration rewrite
- **Pass condition**: `.venv/bin/cairn doctor --fix --json && .venv/bin/cairn doctor --fix --json`

## TC-008 — An interrupted rewrite leaves a whole configuration
- **Story**: US1 · **Traces to**: NFR-004
- **Given** the healing doctor is forcibly interrupted while processing a
  configuration
- **When** the disposable profile is inspected afterward
- **Then** every affected configuration is either the original version or the
  complete stdio version, never a truncated or mixed SSE/stdio registration
- **Pass condition**: `.venv/bin/cairn doctor --fix --json`

## TC-009 — Resolution quality is reported by language
- **Story**: US3 · **Traces to**: FR-002, NFR-005, AC1
- **Given** a built store with known exact, ambiguous, and unresolved call
  edges in more than one language
- **When** the maintainer requests the resolution-quality report in JSON mode
- **Then** every language has exact, ambiguous, and unresolved counts with
  percentages, the counts total the store's call edges, and percentages total
  100% for each language with edges
- **Pass condition**: `.venv/bin/cairn report --json`

## TC-010 — An edge-free store reports zeros
- **Story**: US3 · **Traces to**: FR-002, AC2
- **Given** a successfully built store contains symbols but no call edges
- **When** the resolution-quality report runs
- **Then** the command exits successfully and reports zero exact, ambiguous,
  and unresolved counts with zero percentages per language instead of raising
  an error or omitting the report
- **Pass condition**: `.venv/bin/cairn report --json`

## TC-011 — Resolution evidence is machine-readable
- **Story**: US3 · **Traces to**: FR-002, NFR-005, AC1
- **Given** any built store
- **When** the diagnostic report is emitted in JSON mode
- **Then** a standard JSON parser accepts the complete response without
  human-only decoration on stdout
- **Pass condition**: `.venv/bin/cairn report --json | python3 -m json.tool >/dev/null`

## TC-012 — The perf suite measures every budgeted tool
- **Story**: US2 · **Traces to**: FR-003, NFR-005, AC1
- **Given** a bounded synthetic corpus and a completed warm-up pass
- **When** the perf suite runs in JSON mode
- **Then** it emits a finite p95 for definition lookup, callers, recursive
  impact, symbol search, semantic search, and the aggregated explorer, using
  measurements taken after warm-up
- **Pass condition**: `.venv/bin/cairn bench --suite perf --n-files 2 --complexity low --repeats 1 --embed-backend hash --json | python3 -c 'import json,sys,math; d=json.load(sys.stdin); ops={x["name"]:x for x in d["ops"]}; required={"find_definition","get_callers","impact_analysis","search_symbols","semantic_search","explore"}; assert required <= ops.keys(); assert all(math.isfinite(ops[n]["p95_ms"]) for n in required)'`

## TC-013 — A budget breach fails with the decision data
- **Story**: US2 · **Traces to**: FR-003, AC1
- **Given** a bounded bench fixture has one tool above its checked-in budget
  and every other tool below budget
- **When** the hard budget gate evaluates that run
- **Then** the gate exits nonzero and its failure names the tool, measured
  p95, and budget, while also identifying the other tools as within budget
- **Pass condition**: `.venv/bin/cairn bench --suite perf --n-files 2 --complexity low --repeats 1 --embed-backend hash --json`

## TC-014 — Main gates catastrophes while PRs receive advice
- **Story**: US2 · **Traces to**: FR-003, AC1, AC2
- **Given** the same over-budget result is produced by a main or merge-group
  bench job and by a PR-leg bench job
- **When** both jobs finish
- **Then** the main/merge-group job is failed by the gate, while the PR job
  completes without a hard timing failure and emits an advisory trend
  containing the tool, measurement, and budget
- **Pass condition**: `gh run list --limit 10`

## TC-015 — The weekly CLI replay succeeds
- **Story**: US4 · **Traces to**: FR-004, NFR-005, AC1
- **Given** the weekly schedule has run the checked-in command list against
  the deterministic fixture repository
- **When** the maintainer reviews the job
- **Then** every replayed command exited zero, the log identifies each
  command, and the workflow job is green
- **Pass condition**: `gh run list --limit 10`

## TC-016 — The first failing command stops the weekly replay
- **Story**: US4 · **Traces to**: FR-004, AC1
- **Given** a disposable replay uses the checked-in fixture with one early
  command forced to fail and a later command known to succeed
- **When** the replay executes
- **Then** the job is red, names the exact failing command, and does not
  execute or report the later command as part of that failing pass
- **Pass condition**: `gh run list --limit 10`

## TC-017 — The weekly replay needs no outbound telemetry
- **Story**: US4 · **Traces to**: NFR-002
- **Given** the checked-in command list, deterministic fixture, and an
  execution environment with outbound network access disabled
- **When** the weekly replay executes
- **Then** every command that is part of the normal replay still succeeds and
  no telemetry or usage record is exported outside the repository or fixture
  environment
- **Pass condition**: `gh run list --limit 10`

## TC-018 — Protected main passes the maintainer verifier
- **Story**: US4 · **Traces to**: FR-005, NFR-005, AC2
- **Given** a maintainer with admin-scoped GitHub authentication and a main
  branch configured according to the release checklist
- **When** the maintainer runs the protection verifier
- **Then** it exits zero and reports successful verification of required
  reviews, force-push denial, and required status checks through both the
  legacy protection and rulesets sources, plus every mandated checklist
  setting
- **Pass condition**: `make verify-protection`

## TC-019 — Protection drift fails and names the setting
- **Story**: US4 · **Traces to**: FR-005, AC2
- **Given** one required protection setting is missing or weakened in a
  disposable API fixture representing main
- **When** the maintainer runs the protection verifier against that fixture
- **Then** it exits one, names the drifted setting and which protection source
  revealed it, and does not report overall protection as verified
- **Pass condition**: `make verify-protection`

## TC-020 — The protection verifier is not a CI gate
- **Story**: US4 · **Traces to**: FR-005
- **Given** the repository's continuous integration workflow definitions
- **When** they are searched for an invocation of the maintainer-only
  protection verifier
- **Then** no CI job invokes it, preserving its admin-scoped, maintainer-run
  contract
- **Pass condition**: `if git grep -n "verify-protection" -- .github/workflows; then exit 1; else exit 0; fi`

## TC-021 — Guardrail instruments are CI-consumable
- **Story**: US2 · **Traces to**: NFR-005
- **Given** JSON-mode output from the healing doctor, resolution report,
  budgeted bench, weekly replay, and protection verifier
- **When** CI or the bundled system report consumes each output
- **Then** each parses as JSON without human preprocessing and preserves the
  corresponding tool, action, count, measurement, command, or protection
  result needed to make a machine decision
- **Pass condition**: `gh run list --limit 10`

## Coverage matrix

| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001 | TC-001, TC-002, TC-003, TC-004, TC-005 | manual |
| FR-002 | TC-009, TC-010, TC-011 | mixed |
| FR-003 | TC-012, TC-013, TC-014 | mixed |
| FR-004 | TC-015, TC-016 | manual |
| FR-005 | TC-018, TC-019, TC-020 | mixed |
| NFR-001 | TC-001, TC-004, TC-006 | manual |
| NFR-002 | TC-017 | manual |
| NFR-003 | not applicable per spec | n/a |
| NFR-004 | TC-001, TC-003, TC-007, TC-008 | manual |
| NFR-005 | TC-001, TC-009, TC-011, TC-012, TC-015, TC-018, TC-021 | mixed |
| NFR-006 | not applicable per spec | n/a |
