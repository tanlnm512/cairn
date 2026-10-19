---
type: Tribal-workaround
title: 'Shared-memory-bus TC chains: bare wait returns 0 so search runs before the
  share verb exists'
description: 'Shared-memory-bus TC chains: bare wait returns 0 so search runs before
  the share verb exists'
resource: specs/shared-memory-bus/test.md
tags:
- workaround
generated:
  by: cairn/0.20.2
  at: '2026-09-17T00:02:24Z'
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
memory_type: workaround
session_origin: ''
promotion_history:
- date: '2026-09-17T00:02:24Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T00:02:24Z'
---

In the shared-memory-bus acceptance chains, the TC-012/TC-013 shape (cmd1 & cmd2 & wait && search) always runs the trailing search: bare wait with no operands returns 0 even when both backgrounded share commands fail. Only the && chain (TC-014 shape) short-circuits on the missing share verb. Why: a closing audit re-running these TCs before the CLI task lands will see real search/stats output against the real store and could misread it as the share path working. How to apply: judge TC-012/TC-013 pass/fail on the share command's own output, not on whether the search step ran; all pre-verb runs are read-only against the store (share fails at click parse before any DB open).