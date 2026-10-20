---
type: Tribal-mistake
title: find_symbol_paths fork tie-break flips across rebuilds
description: find_symbol_paths fork tie-break flips across rebuilds
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-10-05T05:40:52Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.492
memory_signals:
  graph_verification: 0.667
  cross_session_refs: 0
  agent_confidence: 0.85
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: drafts
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-05T05:40:52Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_refs_baseline: 0.667
valid_from: '2026-10-05T05:40:52Z'
---

In src/cairn/graph/taint.py `_paths_from_seed`/`_exact_neighbors`, when one BFS batch expands multiple frontier nodes whose edges converge on the same terminator (fork fixture: mid_left and mid_right both call hub_sink), the two candidate rows tie on every ORDER BY column (s.name, f.path, s.id are identical because they join the same target symbol). SQLite breaks the tie by index scan order over `source_id`, and symbol ids are fresh uuids per build, so which mid the shortest path picks flips between rebuilds of the same graph. Determinism per store (FR-007, double-run byte-identical) still holds — the flip is only across rebuilds. Why: tests that pin WHICH tied hop was chosen (e.g. mid_left) are flaky; pin set-membership instead. How to apply: a real fix belongs in the walk's ordering (break row ties by a build-stable key such as the source symbol name, not uuid source_id) in graph/taint.py; until then, no test or consumer may assert a specific winner among structurally tied equal-length routes.