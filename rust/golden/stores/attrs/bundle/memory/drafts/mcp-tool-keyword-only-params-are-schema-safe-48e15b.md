---
type: Tribal-pattern
title: MCP tool keyword-only params are schema-safe
description: MCP tool keyword-only params are schema-safe
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-09-16T20:52:53Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.4
memory_signals:
  graph_verification: 0.5
  cross_session_refs: 0
  agent_confidence: 0.7
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: drafts
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-09-16T20:52:53Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-09-16T20:52:53Z'
valid_until: '2026-09-18T16:34:19Z'
stale: true
memory_refs_baseline: 0.5
---

FastMCP's func_metadata includes KEYWORD_ONLY parameters in the tool's JSON schema and calls tools with **kwargs, so an MCP tool may declare trailing keyword-only options (e.g. `def tool(x, *, as_of: str | None = None)`). The `instrument` wrapper preserves the real signature via functools.wraps/`__wrapped__`, so schema generation sees the parameter. Keyword-only keeps new options compatible with test stubs shaped `lambda conn, bundle, q, **kw`.

Why: new optional tool parameters must not break positional-shaped stubs in exact-traffic pins.

How to apply: add trailing keyword-only parameters to MCP tools instead of positional ones; forward them as keywords to the underlying store function.