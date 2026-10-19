---
type: Tribal-pattern
title: Mini workspace fixtures index via empty .git dir + explicit --workspace registration
description: Mini workspace fixtures index via empty .git dir + explicit --workspace
  registration
resource: specs/taint-tracking/fixtures
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T02:19:39Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-17T02:19:39Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T02:19:39Z'
---

A cairn workspace with no .git discovers zero repos and builds an empty graph (scanner._top_level_records requires (root/'.git').exists()). An EMPTY .git directory satisfies discovery while plain git adds the files normally (no gitlink) — a real nested .git would create an embedded-repo gitlink and drop the files from the parent commit. Empty dirs do not survive clones: any suite consuming such fixtures must mkdir each .git and re-register before indexing. Registration must pass the fixture path explicitly (cairn init --workspace <dir>): the no-arg form walks up to the nearest registered ancestor and initializes the parent repo instead.