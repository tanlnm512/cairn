---
type: Tribal-mistake
title: runner context is invalid in job-level workflow env
description: runner context is invalid in job-level workflow env
resource: .github/workflows/weekly-smoke.yml
tags:
- mistake
generated:
  by: cairn/0.21.2
  at: '2026-10-06T16:58:51Z'
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
memory_tier: tribal
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-06T16:58:51Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-06T16:58:51Z'
---

GitHub Actions rejects `${{ runner.* }}` inside a job-level env block with 'Unrecognized named-value: runner' at dispatch time (HTTP 422), and a phantom 0s failed push run on merge. Move runner-derived values to step-level env or use $RUNNER_TEMP in run blocks.