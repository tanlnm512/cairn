---
type: Tribal-decision
title: Parser bodies cap embedding context before docstring duplication
description: Parser bodies cap embedding context before docstring duplication
resource: src/cairn/parsers/base.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:15:08Z'
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
- date: '2026-10-07T17:15:08Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:15:08Z'
---

Tree-sitter parser bodies are capped at 1500 characters, exclude the definition signature, and omit a leading docstring that is already stored separately.

Why: bounded implementation text preserves distinctive embedding signal without duplicating docstrings or overwhelming chunks with very long bodies.
How to apply: keep Symbol.docstring as the docstring owner and change the shared body cap only with embedding-chunk behavior covered by tests.