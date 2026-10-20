---
type: Tribal-workaround
title: Rerunning an old CI run cancels the newer run in its concurrency group
description: Rerunning an old CI run cancels the newer run in its concurrency group
resource: .github/workflows/ci.yml
tags:
- workaround
generated:
  by: cairn/0.21.2
  at: '2026-10-06T16:58:52Z'
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
- date: '2026-10-06T16:58:52Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-06T16:58:52Z'
---

'gh run rerun <old> --failed' re-enters the run's concurrency group (e.g. ci-refs/heads/main) and cancels whichever newer run currently holds it. Before rerunning, check no newer run of the same workflow/ref is in progress; prefer rerunning only the newest run.