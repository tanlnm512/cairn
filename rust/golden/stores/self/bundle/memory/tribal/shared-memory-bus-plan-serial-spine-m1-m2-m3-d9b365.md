---
type: Tribal-decision
title: 'shared-memory-bus plan: serial spine M1->{M2,M3}'
description: 'shared-memory-bus plan: serial spine M1->{M2,M3}'
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-09-16T17:46:49Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.578
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.7
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-09-16T17:46:49Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-16T17:46:49Z'
---

Planner sequencing for specs/shared-memory-bus/plan.md: M1 (agent identity FR-006 + per-agent namespaces/locking FR-005 + FR-004 baseline) blocks both M2 (share+recall, FR-001/FR-003) and M3 (overlap detection FR-002). M2 parallel M3 after M1; sole shared file src/cairn/cli/memory.py (both add a click subcommand). Evidence: cairn callers store_memory (promotion.py + store_protocol.py), cairn impact recall_memory (11 test symbols), survey S1-S4. Confidence 0.85.