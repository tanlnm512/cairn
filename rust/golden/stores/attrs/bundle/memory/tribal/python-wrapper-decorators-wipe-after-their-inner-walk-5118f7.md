---
type: Tribal-pattern
title: python wrapper decorators wipe after their inner walk
description: python wrapper decorators wipe after their inner walk
resource: rust/crates/cairn-parse/src/parser_script.rs
tags:
- pattern
generated:
  by: cairn/0.22.0
  at: '2026-10-19T02:01:00Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.61
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.85
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-19T02:01:00Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-19T02:01:00Z'
---

Class-body members inherit the class decorator only until a nested decorated_definition walks; python's finally clears the slot for everything after it (verified: A.plain_first ['@dec0'], A.decorated ['@prop'], A.plain_after [], B.b_plain ['@dec1']). The rust port threads a mutable Vec decorator slot and clears it after each wrapper walk.

Why: an immutable parameter-threaded port keeps outer decorators alive across nested wrappers and over-attaches modifiers plus decorates edges; the G2 self-corpus diff showed exactly 24 such rows.

How to apply: when porting python tree-sitter walkers that mutate per-wrapper state in try/finally, replicate the mutation-and-clear semantics, not clean immutable scoping.