# Grade-A Roadmap — remaining work

<- [Docs index](README.md)

Live state after the ratchet (#149), guardrails (#150), comment-sweep
(#160), protection/ruleset configuration (#157/#158), budget
recalibration (#159/#161/#162), audit batches 1–4 (#163/#164/#166,
#168-#171), the systemic-cluster flips + A4 retro (#172), the conformance
matrix (#173), and the precision baseline + spec (#174). Scoreboards:
`make audit-status`, `make comment-style`, `make verify-protection`.

## Done

| Gate | Delivery |
|---|---|
| Contract — comment ratchet + sweep (1213 → 224) | #149, #160 |
| Guardrails — budgets, weekly smoke, doctor --fix, verifier | #150, #151 |
| Protection — 14 required checks + force-push denial, verifier green | #157, #158 |
| Budgets — factor 10 from CI-class run, self-consistent source p95s | #161, #162 |
| Flakes — stdio catalog root-caused (sequential exchange) | #153, #165 |
| Audit batches 1–4 — all P0/P1/P3 closed (146-finding index; 59 fixed) | #163, #164, #166, #168-#171 |
| A4 retro — post-audit admin-merged PRs reviewed; C78/C79 fixed failing-first | #172 |
| SY1/SY2/SY3 systemic clusters — verified remediated, flipped | #172 |
| F — parser conformance matrix: 15 golden languages, tier-marked, staleness-gated | #173 |
| E (evidence half) — ambiguous-edge baseline measured + published; spec drafted | #174 |

## Remaining

| Item | State |
|---|---|
| E (implementation) | `specs/exact-edge-precision/` awaiting the FR-005 ruling (auto-pyright budget cap); then implement + publish the exact-ratio delta |
| P2 polish — 87 findings | Below the grade-A bar; opportunistically by module |
| A3 — merges through the review gate | Standing: ruleset enforces everything except the admin override itself |

## Sequencing

E (post-ruling) → P2 polish by module.
