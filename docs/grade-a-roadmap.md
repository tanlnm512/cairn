# Grade-A Roadmap

<- [Docs index](README.md)

The completion plan for the grade-A program: what shipped, what remains,
and the acceptance criterion for each item. Scoreboards: `make
audit-status` (debt), `make comment-style` (contract drift),
`make verify-protection` (governance).

## Shipped

| Capability | Delivery |
|---|---|
| Audit-debt counter + status sidecar | PR #149 |
| Shrink-only comment-style ratchet (pre-commit + CI) | PR #149 |
| Root-artifact relocation | PR #149 |
| `cairn doctor --fix` (cairn-owned, atomic, freshness-guarded) | PR #150 |
| Resolution-quality report (`cairn report`) | PR #150 |
| p95 catastrophe budgets, enforcing on main/merge-group | PR #150 |
| Weekly CLI smoke workflow | PR #150, #151 |
| `make verify-protection` verifier + checklist | PR #150 |
| Standards flake fix (stdio catalog retry) | PR #153 |

## Workstreams

### A — Governance (closes D-014; the load-bearing gap)
- **A1** Configure `main` protection: force-push denial + required checks
  per [release-checklist.md](release-checklist.md). Owner: maintainer
  (GitHub settings). Accept: `make verify-protection` exits 0.
- **A2** Reconcile the verifier's `MANDATORY_CHECKS` wishlist with the
  ruleset's configured checks (union-compare against reality). Accept: no
  false FAIL; verifier reports the configured set.
- **A3** No more admin merges; standing non-author review; 30 consecutive
  reviewed PRs. Owner: maintainers.
- **A4** Retro-review the admin-merged PRs (#149/#150/#151/#153) in one
  batch; findings → audit sidecar.

### B — Budget recalibration + flake closure (D-017 follow-through)
- **B1** Mint factor-10 budgets from a green CI-class perf run
  (`scripts/mint_perf_budgets.py`); review the provenance stamp. Accept:
  budgets carry a CI `machine_profile`; main bench stays green.
- **B2** Close flake issue #152 after 5 consecutive green full-matrix runs
  + 2 weekly smokes.

### C — `comment-sweep` spec (contract completion)
- Reduce the comment/docstring baseline by ≥50% (1213 → ≤606) with
  mechanical-only transforms; migrate durable rationale to `cairn memory`
  or docs before deletion; land the parked runpy import-warning fix
  (ratchet D-012). Accept: `make comment-style` reports ≤606; full suite
  green throughout.

### D — Audit burndown execution
- Work the 51 P1 findings in the audit's fix order; every fix ships a
  failing-first regression test and flips its sidecar ID. Fold in this
  program's own escapes (mypy local gap, omitted test file, `runner.temp`,
  pre-commit pipe masking, stdio flake). Accept: `make audit-status`
  reports P1 ≤ 10.

### E — Precision follow-up (evidence-first)
- Rank languages by ambiguous-edge mass from `cairn report`
  (`resolution_by_language`); spec the exact-edge upgrade for the top two
  (LSP or SCIP; `scip-indexing-v2` groundwork exists). Accept: measured
  exact-ratio delta published in [benchmarks.md](benchmarks.md); parity
  tests in CI.

### F — Parser conformance matrix
- Per-language golden fixtures + generated table in
  [indexing.md](indexing.md); 15 rows green or explicitly tier-marked.
  Feature freeze on new surfaces until C/D land. Accept: 15/15 truthful
  rows.

## Sequencing

1. Now: A1, A2, B1 (small).
2. Next: C and D in parallel (disjoint files).
3. Standing: A3/A4.
4. After C/D: E, then F.

## Gate mapping

A → process · D → debt · C → contract · E → precision · F → hardness ·
B → guardrails. All six "definition of A" gates from the original review
close when their workstream closes.
