---
type: Tribal-pattern
title: Kotlin comments after the last import are swallowed by import_header
description: Kotlin comments after the last import are swallowed by import_header
resource: rust/crates/cairn-parse/src/parser_jvm.rs
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-10-19T00:23:56Z'
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
- date: '2026-10-19T00:23:56Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
---

In the fwcd tree-sitter-kotlin grammar, comments between the last import and the next declaration attach inside the preceding import_header node. Both KotlinParser and its Rust port return from the import branch without descending, so NOTE/WHY/HACK rationale records are skipped there; comments inside class bodies are recorded normally.

Why: a unit test expected a rationale record from a top-level comment after an import and got zero; the Python parser on the same snippet behaves identically, so it is grammar structure, not a port bug.

How to apply: test kotlin rationale extraction with comments inside class/function bodies; never descend into import_header to 'fix' this, it would diverge from Python.