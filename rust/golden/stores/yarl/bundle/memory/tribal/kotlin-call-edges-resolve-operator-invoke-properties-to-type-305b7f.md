---
type: Tribal-pattern
title: Kotlin call edges resolve operator-invoke properties to types
description: Kotlin call edges resolve operator-invoke properties to types
resource: src/cairn/parsers/kotlin.py
tags:
- pattern
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:15:09Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-07T17:15:09Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:15:09Z'
---

Both bare useCase(args) and explicit this.useCase(args)/obj.useCase(args) Kotlin calls retarget their edge from the injected property or variable to its declared UseCase type.

Why: Kotlin operator fun invoke makes the property the callable surface; resolving the property name alone loses the shared class called by every invocation.
How to apply: when changing receiver inference, keep both bare and explicit-receiver forms resolving to the declared type and exercise both in parser tests.