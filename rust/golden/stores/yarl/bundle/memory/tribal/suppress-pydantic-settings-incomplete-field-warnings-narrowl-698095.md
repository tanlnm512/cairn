---
type: Tribal-workaround
title: Suppress pydantic-settings incomplete-field warnings narrowly at FastMCP construction
description: Suppress pydantic-settings incomplete-field warnings narrowly at FastMCP
  construction
resource: src/cairn/mcp_server/_server_core.py
tags:
- workaround
generated:
  by: cairn/0.21.2
  at: '2026-10-07T15:35:22Z'
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
memory_type: workaround
session_origin: ''
promotion_history:
- date: '2026-10-07T15:35:22Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T15:35:22Z'
---

FastMCP construction is wrapped in warnings.catch_warnings() with a filter for IncompleteFieldDefinitionWarning only when that class exists.
Why: the mcp SDK Settings.lifespan field has an unresolved forward reference to FastMCP (model_rebuild is never called after the class is defined), so pydantic-settings warns on every construction; older pydantic-settings does not define the warning class at all.
How to apply: keep the ImportError guard for old versions and never widen the filter — the singleton is constructed on every CLI invocation.