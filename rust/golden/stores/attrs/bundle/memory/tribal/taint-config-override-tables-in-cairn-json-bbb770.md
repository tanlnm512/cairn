---
type: Tribal-pattern
title: Taint config override tables in cairn.json
description: Taint config override tables in cairn.json
resource: src/cairn/graph/config.py
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-17T01:53:01Z'
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
- date: '2026-09-17T01:53:01Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-17T01:53:01Z'
---

cairn.json 'taint' section: {'sources': {category: [call names]}, 'sinks': {...}} parses to CairnConfig.taint_sources/taint_sinks as dict[str, set[str]], consumed directly as build_registry(source_overrides, sink_overrides) override tables. _as_name_table in config.py composes _as_string_list per category; _as_string_dict (dict[str,str]) is a near-match, not reused. An empty name list is kept as an empty set so it replaces its default category wholesale. Why: registry replace/extend semantics stay in taint.py; config only parses pass-through tables. How to apply: extend taint config keys via _as_name_table, never a new parsing mechanism.