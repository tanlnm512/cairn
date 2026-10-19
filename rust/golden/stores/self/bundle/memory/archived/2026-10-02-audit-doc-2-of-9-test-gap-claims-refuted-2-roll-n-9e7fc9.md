---
type: Tribal-mistake
title: '2026-10-02 audit doc: 2 of 9 test-gap claims refuted, 2 roll-note errors,
  Q32/C32 roll mislabel'
description: '2026-10-02 audit doc: 2 of 9 test-gap claims refuted, 2 roll-note errors,
  Q32/C32 roll mislabel'
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-10-02T15:35:45Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.632
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.95
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: archived
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-02T15:35:45Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_triage: session-bookkeeping
valid_from: '2026-10-02T15:35:45Z'
---

Meta-audit of audit-findings-2026-10-02.md (13 parallel verifier batches, all 141 findings + 26 systemic samples + 9 test-gap claims re-checked at HEAD cf4fd65, code identical to 98fc2d8): all 141 core defects CONFIRMED real; 5 doc defects found — (1) appendix P2 roll records Q32's verdict under C32's ID (C32's own verdict absent, roll has 87 P2 rows); (2) C73 roll note says _refuse_out_of_namespace "exists unused" but it is used 3x (knowledge/store.py:243,267,292); (3) C42 roll note implies _escape_like unused repo-wide but it is used at 5 sites (refs.py:94,183; traversal.py:54; cross_repo.py:146; compass/generator.py:51) — only unused at gaps.py:67; (4) test-gap "trace_flow diamonds untested" REFUTED (tests/test_traversal_parity.py diamond corpus entry_north→mid_a/mid_b→leaf_util, TRACE_QUERIES:64, goldens/traversal_parity.json 'trace'); (5) test-gap "_find_tracked_file_row/reindex_paths agreement untested" REFUTED (tests/test_incremental_derived.py:379 parity + tests/test_reindex_resolution_invariant.py). Gate counts are env-sensitive: doc says 3818 passed/2 skipped; same tree gives 3827/1 (10+ importorskip modules; doc ran in sandboxed project mode).
Why: the doc's "All nine gaps confirmed" and "141/141 checked, 0 corrections needed beyond the 5 PARTIALs" claims are overstated; a fixer trusting the test-gap list would skip writing a diamond trace test that already exists, or delete a "dead" helper that is live.
How to apply: when using the doc as a fix backlog, ignore its test-gap items 1 and 8, treat C73/C42 "_escape_like/_refuse unused" sub-claims as wrong, and re-check roll verdicts against the index when IDs look shifted.