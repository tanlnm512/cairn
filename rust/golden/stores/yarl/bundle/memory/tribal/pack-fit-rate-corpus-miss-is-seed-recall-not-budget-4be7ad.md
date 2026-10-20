---
type: Tribal-mistake
title: pack-fit-rate corpus miss is seed recall, not budget
description: pack-fit-rate corpus miss is seed recall, not budget
resource: src/cairn/bench/agent_suite.py
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-17T08:05:47Z'
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
- date: '2026-09-17T08:05:47Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T08:05:47Z'
---

The agent-suite pack arm fits 2/4 seeded targets at budget 4000 on the synthetic corpus: zero items dropped, 1821/4000 tokens used; Cls0010_4 and Cls0038_3 never enter the candidate pool. semantic_search on the pack question returns only method-kind rows through the top-60, so no SEED_LIMIT increase fixes it on this corpus. Why: bge-m3 embeddings rank opaque synthetic class names below method bodies; seeding recall, not fitting, bounds the fit rate. How to apply: any fix round for the >=0.85 fit target must change seeding (e.g. class-name lexical anchor alongside semantic seeds), not rank/fit stages.