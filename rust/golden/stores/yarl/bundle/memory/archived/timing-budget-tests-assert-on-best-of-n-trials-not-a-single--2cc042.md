---
type: Tribal-mistake
title: 'Timing-budget tests: assert on best-of-N trials, not a single wall-clock sample'
description: 'Timing-budget tests: assert on best-of-N trials, not a single wall-clock
  sample'
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-25T02:40:37Z'
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
memory_tier: archived
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-25T02:40:37Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
memory_triage: session-bookkeeping
valid_from: '2026-09-25T02:40:37Z'
---

tests/test_dashboard_app.py::test_first_health_render_on_fresh_app_is_under_budget (SC-1, 200ms wall-clock) failed 3 of 3 spec-code-review gate runs under the full 4031-test suite while passing in isolation — scheduler noise around the budget, not a regression.
Why: wall-clock asserts on a loaded pytest process are load-sensitive; PR #137 (docs/packaging) was falsely red-gated.
How to apply: judge wall-clock budgets on the best of several trials when the regression mode pays the slow path on every trial (min-of-N keeps full teeth); fixed in commit b2ef3b5 on fix/health-render-budget-flake.