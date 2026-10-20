---
type: Tribal-decision
title: SWE-bench workspaces are commit-keyed warm caches
description: SWE-bench workspaces are commit-keyed warm caches
resource: src/cairn/cli/bench.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T16:14:12Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T16:14:12Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T16:14:12Z'
---

SWE-bench workspaces live under <CAIRN_HOME>/cache/swe-bench/<repo>-<base_commit>: directory presence means that exact content is checked out, clones stage into a pid-suffixed temp dir renamed into place, and a run that loses a concurrent rename uses the winner's checkout.
Why: warm reruns must never touch the network, and the temp-then-rename protocol keeps a concurrent run from ever seeing a half-cloned tree.
How to apply: key new bench caches by the exact pinned revision and keep the atomic-rename publication step.