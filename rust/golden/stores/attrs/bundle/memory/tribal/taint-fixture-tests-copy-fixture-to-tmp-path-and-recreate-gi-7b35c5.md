---
type: Tribal-pattern
title: Taint fixture tests copy fixture to tmp_path and recreate .git
description: Taint fixture tests copy fixture to tmp_path and recreate .git
resource: tests/test_taint_fixtures.py
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T02:38:32Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-17T02:38:32Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T02:38:32Z'
---

Fixture dirs under specs/taint-tracking/fixtures carry local-only empty .git markers that do not survive clones. Tests must copy the fixture into tmp_path, mkdir .git there, then build the graph with build_graph(workspace=<copy>, db_path=<tmp>) and drive cairn taint via CliRunner with --db and CAIRN_WORKSPACE pointed at the copy (CONSTITUTION C-04 hermeticity; no store outside the sandbox is touched).