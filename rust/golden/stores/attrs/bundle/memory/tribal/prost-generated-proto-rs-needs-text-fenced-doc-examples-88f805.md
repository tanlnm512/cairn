---
type: Tribal-mistake
title: prost-generated proto.rs needs text-fenced doc examples
description: prost-generated proto.rs needs text-fenced doc examples
resource: scripts/regen_scip_proto.sh
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-10-19T03:21:26Z'
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
- date: '2026-10-19T03:21:26Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
---

Bare ``` fences in scip.proto doc comments hold EBNF/diagram pseudo-code, but rustdoc compiles bare fences in Rust doc comments as doctests, so a freshly regenerated proto.rs fails 5 doctest compilations. Why: the proto documents its symbol grammar with const/arrow pseudo-code that is not valid Rust. How to apply: scripts/regen_scip_proto.sh annotates bare doc fences as text when installing proto.rs; regenerating by hand without that step turns the workspace doctest gate red.