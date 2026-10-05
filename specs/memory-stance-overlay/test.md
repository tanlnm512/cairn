# Test Cases: memory-stance-overlay

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-04
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details — the only
product surface named is the user-facing command surface the spec itself
promises.

**Fixtures**: `<fixture>` is a scratch cairn workspace with its own small
memory store, seeded fresh per the case's Given; `<primary>` is this
repository's own workspace store. Commands run from the workspace root that
owns the store under test. Every auto pass condition finishes in seconds —
well under the 120 s harness cap.

## TC-001 — Reflect runs and reports (P1 · auto)

- **Story**: US1 · **Traces to**: FR-002
- **Given** a fixture store holding a handful of memories, some sharing a cited code identifier
- **When** `cairn memory reflect` runs once
- **Then** it completes and prints a summary of the stance pass — memories evaluated and stances assigned — with no failures reported
- **Pass condition**: `cd <fixture> && cairn memory reflect`
  Exit code 0; the printed summary reports the memories evaluated and the stances assigned, with no error or failure lines.

## TC-002 — Verified guidance wins, contradicted guidance is flagged (P1 · auto)

- **Story**: US1 AC1 · **Traces to**: FR-002, FR-003
- **Given** a fixture store with two memories citing the same code identifier — one whose citations all verify against the indexed code, and one whose guidance the verified one contradicts
- **When** `cairn memory reflect` runs
- **Then** the fully verified memory is marked preferred; the contradicting memory is marked contested and records the verified memory's identity as its contradicting peer
- **Pass condition**: `cd <fixture> && cairn memory reflect`
  Exit code 0; the summary shows the verified memory as preferred and the contradicting memory as contested with its peer memory's identity named.

## TC-003 — Reflect twice, same answer (P1 · auto)

- **Story**: US1 AC2 · **Traces to**: FR-006
- **Given** a fixture store seeded with memories including a contradicting pair, already reflected once
- **When** `cairn memory reflect` runs twice more with no store changes in between
- **Then** both runs report the same stances, the same peer references, in the same order (idempotent and deterministic)
- **Pass condition**: `cd <fixture> && cairn memory reflect > /tmp/reflect-r1.out && cairn memory reflect > /tmp/reflect-r2.out && diff /tmp/reflect-r1.out /tmp/reflect-r2.out`
  The diff is empty — the two reports are identical.

## TC-004 — A fresh memory carries no stance until reflect (P1 · auto)

- **Story**: US1 · **Traces to**: FR-001
- **Given** a fixture store where a brand-new memory is recorded and reflect has not run since
- **When** the store's memory listing is queried for that memory
- **Then** the memory appears with its tier and score as usual and with no stance label (stance is unset by default)
- **Pass condition**: `cd <fixture> && cairn memory record pattern "qa-stance-default probe" --body "temporary body recorded before any reflect pass" && ! cairn memory search "qa-stance-default" | grep -qiE "preferred|tentative|contested"`
  Record and search both succeed, and the result line for the fresh memory carries no stance label.

## TC-005 — Stance never overrides or rewrites lifecycle (P1 · manual)

- **Story**: US1 · **Traces to**: FR-001, FR-003
- **Given** a promoted memory that contradicts a fully verified peer, so reflect will contest it; its listing and body text captured before reflect
- **When** `cairn memory reflect` runs and the memory is viewed again
- **Then** the memory keeps its promoted tier and score unchanged and its body text is untouched — only the contested stance and peer reference were added (a promoted memory can be contested without being demoted)
- **Pass condition**: human observation — the before/after views of the memory show the same tier and score and identical body text; the only difference is the contested stance with its peer reference.

## TC-006 — Stance is visible where memories already surface (P1 · manual)

- **Story**: US2 AC1 · **Traces to**: FR-004
- **Given** a fixture store with a contested memory whose topic matches a natural query, plus memories with no stance
- **When** an agent runs recall and explore for that topic
- **Then** the contested memory renders with its stance inline and the contradicting peer reference; memories without a stance render exactly as they do today
- **Pass condition**: human observation — recall and explore results for the query show the contested marker with the peer identifier on the contested entry, and unstanced entries are unchanged in format.

## TC-007 — Standing guard: reflect changes nothing but stance (P1 · auto)

- **Story**: US1 · **Traces to**: FR-003, FR-006
- **Given** a fixture store seeded with memories containing a known word, fully reflected once, with its memory listing captured
- **When** reflect runs again on the unchanged store
- **Then** the listing is byte-identical — no title, tier, score, or line order moved; a no-op reflect pass changes nothing at all (guard: if reflect ever starts mutating memory content or lifecycle, this fails)
- **Pass condition**: `cd <fixture> && cairn memory reflect > /dev/null && cairn memory search probe > /tmp/list-before.out && cairn memory reflect > /dev/null && cairn memory search probe > /tmp/list-after.out && diff /tmp/list-before.out /tmp/list-after.out`
  The diff is empty — the full listing is unchanged across a no-op reflect pass.

## TC-008 — Rotted citations downgrade to tentative (P2 · auto)

- **Story**: US3 AC1 · **Traces to**: FR-005
- **Given** a fixture workspace where a memory cites a code identifier that exists and a reflect pass has recorded that baseline; then the cited identifier is removed from the codebase (code changed under the memory)
- **When** the workspace index is refreshed and `cairn memory reflect` runs
- **Then** the stale memory is marked tentative — no peer named, since this is staleness, not contradiction
- **Pass condition**: `cd <fixture> && cairn update && cairn memory reflect`
  Exit code 0; the summary marks the stale memory tentative, with no peer reference attached to it.

## TC-009 — Reflect completes on the primary workspace store within 30s (P2 · auto)

- **Story**: US1 · **Traces to**: NFR-003
- **Given** this repository's own workspace store at its current size
- **When** `cairn memory reflect` runs
- **Then** it completes in under 30 seconds wall time
- **Pass condition**: `cd <primary> && time cairn memory reflect`
  Exit code 0 and the shell-reported `real` wall time is under 30s (comfortably under the 120s harness cap).

## TC-010 — Interrupted reflect leaves memory records uncorrupted (P1 · auto)

- **Story**: US1 · **Traces to**: NFR-004
- **Given** a fixture store seeded with dozens of memories containing a known word — enough that a reflect pass takes more than a moment
- **When** reflect is started and then interrupted (interrupt signal) mid-run
- **Then** the store is intact — every pre-run memory is still listed, none garbled, duplicated, or truncated — and a subsequent reflect completes normally
- **Pass condition**: `cd <fixture> && ( cairn memory reflect & pid=$!; sleep 2; kill -INT $pid 2>/dev/null; wait $pid; true ) && cairn memory search probe && cairn memory reflect > /dev/null && echo INTACT`
  After the interrupted run, search exits 0 and lists every seeded memory with well-formed lines; a follow-up reflect exits 0 and the chain prints INTACT.

## TC-011 — Reflect on an empty store is a clean no-op (P2 · auto)

- **Story**: US1 · **Traces to**: FR-002, FR-006
- **Given** a workspace store with zero memories
- **When** `cairn memory reflect` runs
- **Then** it exits cleanly, reports zero memories evaluated, and assigns no stances — no error, no fabricated output
- **Pass condition**: `cd <fixture> && cairn memory reflect`
  Exit code 0; the summary reports zero memories evaluated and no stances assigned.

## TC-012 — Mere co-citation is not contradiction (P2 · manual)

- **Story**: US1 · **Traces to**: FR-002
- **Given** two memories citing the same code identifier whose guidance is complementary (one records a decision, the other a related pattern) with no superseding relationship between them
- **When** `cairn memory reflect` runs
- **Then** neither memory is contested — contradiction requires disagreeing guidance, not merely sharing a citation
- **Pass condition**: human observation — after reflect, the summary shows neither complementary memory contested (stances are preferred or unset only).

## TC-013 — Reflect alongside a concurrent recorder (P2 · manual)

- **Story**: US1 · **Traces to**: NFR-004
- **Given** a fixture store where, while `cairn memory reflect` is running, another agent records a new memory (the concurrent-write scenario the spec names as a risk)
- **When** both operations finish
- **Then** the newly recorded memory exists intact and all pre-existing memories remain intact — no write is lost or garbled
- **Pass condition**: human observation — after a record issued during a reflect run, searching finds the new memory by its title with correct content, and a follow-up reflect completes cleanly.

## TC-014 — A declared stance renders and survives reflect without a verdict (P1 · auto)

- **Story**: US1 · **Traces to**: FR-002
- **Given** a fixture store where a memory is recorded with an explicitly declared stance (the record-time prior of the spec's dual stance authority) and nothing in the store can contradict it
- **When** the memory is listed, then `cairn memory reflect` runs with no contradicting evidence, and the memory is listed again
- **Then** the listing line renders the declared stance from the moment of recording, and the declaration survives reflect unchanged — reflect produced no evidence verdict, so the prior stands
- **Pass condition**: `cd <fixture> && cairn memory record pattern "qa-stance-declared probe" --body "prose-only body recorded with an explicit stance declaration before any reflect pass" --stance preferred && cairn memory search "qa-stance-declared" | grep -qi preferred && cairn memory reflect > /dev/null && cairn memory search "qa-stance-declared" | grep -qi preferred`
  The record succeeds, the search line renders the declared preferred stance, and it still renders after the reflect pass — the declaration survives when reflect produces no verdict. (The case where an evidence verdict overrides a declaration is TC-002's territory.)

## Coverage matrix

| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001 (optional stance, unset default, orthogonal to lifecycle) | TC-004, TC-005 | auto, manual |
| FR-002 (dual stance authority: record-time declaration, reflect recomputes from evidence, verdict overrides on conflict) | TC-001, TC-002, TC-011, TC-012, TC-014 | auto ×4, manual |
| FR-003 (stance-only mutation; contested records peer) | TC-002, TC-005, TC-007 | auto ×2, manual |
| FR-004 (inline stance rendering in recall/explore) | TC-006 | manual |
| FR-005 (rotted citations downgrade to tentative) | TC-008 | auto |
| FR-006 (idempotent and deterministic) | TC-003, TC-011 | auto |
| NFR-003 (30s wall on primary workspace store) | TC-009 | auto |
| NFR-004 (interrupt-safe, uncorrupted files) | TC-010, TC-013 | auto, manual |
| NFR-001 (security) | — | not applicable — spec records: local file metadata only, no new surface |
| NFR-002 (privacy) | — | not applicable — spec records: no new data leaves the store |
| NFR-005 (observability) | — | not applicable — spec records: the command's own summary suffices; that summary is the observation surface of TC-001/002/008 |
| NFR-006 (accessibility) | — | not applicable — spec records: no UI surface touched |

Acceptance-criterion coverage: US1 AC1 → TC-002 · US1 AC2 → TC-003 ·
US2 AC1 → TC-006 · US3 AC1 → TC-008. Boundary cases: empty store (TC-011),
concurrent access (TC-013), benign co-citation (TC-012), primary-store scale
(TC-009).

Priorities: P1 × 9 (TC-001–TC-007, TC-010, TC-014), P2 × 5 (TC-008,
TC-009, TC-011–TC-013).

## Notes for the suite owner

- **FR-002's stance authority is resolved as DUAL**: a stance may be
  declared at record time (a prior), reflect recomputes from evidence, the
  evidence verdict overrides a declaration on conflict, and the declaration
  survives when reflect produces no verdict. TC-014 covers the declared
  stance rendering and its survival; the verdict-overrides-declaration
  conflict case stays TC-002's territory.
- Auto stance assertions observe the command's own summary output (the
  report surface the spec promises under NFR-005); the rendered recall/explore
  surfaces are covered manually in TC-006. If the summary format is pinned
  later, the TC-002/TC-008 pass conditions can be tightened to exact greps.
- The idempotency contract (FR-006) is a property; with no property library
  assumed, it is pinned by fixed double-run diffs (TC-003, TC-007) — no new
  dependency added for testability.
- TC-007 doubles as the standing regression guard for FR-003: any reflect
  change that starts mutating titles, tiers, scores, or line order fails it.
