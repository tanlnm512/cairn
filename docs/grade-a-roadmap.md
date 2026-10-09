# Grade-A Roadmap — remaining work

<- [Docs index](README.md)

Live state after the ratchet (#149), guardrails (#150), comment-sweep
(#160), protection/ruleset configuration (#157/#158), budget
recalibration (#159/#161/#162), and audit batches 1–3 (#163/#164/#166).
Scoreboards: `make audit-status`, `make comment-style`,
`make verify-protection`.

## Done

| Gate | Delivery |
|---|---|
| Contract — comment ratchet + sweep (1213 → 226) | #149, #160 |
| Guardrails — budgets, weekly smoke, doctor --fix, verifier | #150, #151 |
| Protection — 14 required checks + force-push denial, verifier green | #157, #158 |
| Budgets — factor 10 from CI-class run, self-consistent source p95s | #161, #162 |
| Flakes — stdio catalog root-caused (sequential exchange) | #153, #165 |
| Audit batches 1–3 — 18 findings closed (P0 0/2, P1 36/51) | #163, #164, #166 |

## D — finish the burndown (P1 36 → target ≤10; realistically 0)

Verify-then-fix protocol per batch: findings predate recent fixes, so each
is verified against current code first; real fixes ship failing-first
regression tests; already-fixed findings get their sidecar ID flipped (and
a pin added if none exists). Full suite green before every merge.

| Wave | Scope | Findings |
|---|---|---|
| 4a | graph | C3, C8, C9, C11–C14, Q27–Q29 (10) |
| 4b | dashboard + cli | C4, C53–C55, C65, C30 + the missed C70 sidecar flip (7) |
| 4c | scripts, agent_install, viz | S8–S10, S17, S18, S3, S4, C17, C75 (9) |
| 4d | long tail | bench Q1–Q2 · knowledge C68, Q40 · retrieval C15 · llm C29 · compass C40 · telemetry C69 · mcp_server C73 · memory C74 (10) |

After the P1 floor: SY2 stale-docs corrections (~13 enumerated sites),
SY3 dedup helpers (3), then P2s opportunistically by module (75 — polish,
below the grade-A bar).

## E — precision follow-up (evidence-first)

1. Rank languages by ambiguous-edge mass from `cairn report`
   (`resolution_by_language`) on self + the t2 corpus; pick the top two.
2. Spec and implement the exact-edge upgrade for those two — SCIP
   indexers first (`scip-indexing-v2` groundwork), LSP only where SCIP
   cannot reach.
3. Publish the measured exact-ratio delta in [benchmarks.md](benchmarks.md).

## F — parser conformance matrix

Per-language golden fixtures feeding a generated 15-row table in
[indexing.md](indexing.md); every row green or explicitly tier-marked.

## A3/A4 — standing human-process items

- **A4** Retro-review the admin-merged PRs in one batch; findings → audit
  sidecar.
- **A3** From the next PR onward, merges through the review gate; the
  ruleset now enforces everything except the admin override itself.

## Sequencing

4a + 4b → 4c + 4d → A4 → E → F.
