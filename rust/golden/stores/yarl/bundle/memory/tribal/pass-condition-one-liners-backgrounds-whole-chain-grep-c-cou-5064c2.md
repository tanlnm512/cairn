---
type: Tribal-mistake
title: 'Pass-condition one-liners: & backgrounds whole chain; grep -c counts lines'
description: 'Pass-condition one-liners: & backgrounds whole chain; grep -c counts
  lines'
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-10-05T03:23:53Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.621
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.9
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-05T03:23:53Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-05T03:23:53Z'
---

Two pitfalls when authoring self-contained compound pass-condition one-liners in spec test.md files.
1) `setup && cmd > out & p=$!; ...` backgrounds the ENTIRE preceding && chain, so variables assigned in it ($d from mktemp) are unset in the following foreground job (redirect collapses to /out). Fix: wrap the parallel section in an explicit subshell `( ... & p1=$!; ... & p2=$!; wait $p1 && wait $p2 )` after the setup chain.
2) `grep -c PATTERN file` counts matching LINES, not matches — a 50-entry JSON array on one line counts as 1. Fix: `grep -o PATTERN file | wc -l | tr -d " "` when many matches share a line.
Why: both silently invert a passing TC into a false red (or hide truncation), discovered validating specs/pr-tooling/test.md TC-003/TC-020 against a shim.
How to apply: run each authored pass condition through bash -n plus a shim-executed dry run before finishing a qa spawn; count matches, never lines, for multi-match assertions.