---
type: Tribal-mistake
title: spec-code-review saved workflow has args.base compile error
description: spec-code-review saved workflow has args.base compile error
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-09-26T17:11:44Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.443
memory_signals:
  graph_verification: 0.5
  cross_session_refs: 0
  agent_confidence: 0.9
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: drafts
tier: asserted
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-09-26T17:11:44Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
stale: true
memory_refs_baseline: 0.5
valid_from: '2026-09-26T17:11:44Z'
---

The globally saved zcode workflow spec-code-review (~/.zcode/workflows/spec-code-review.dwf.ts) fails to compile when run: line ~223 narrows `args.base` through a separate HAS_BASE boolean, so `args.base.trim()` errors with "'args.base' is of type 'unknown'".
Why: TypeScript does not track narrowing across a const boolean; the saved definition predates the strict-unknown args facade.
How to apply: inline-narrow in one expression (`const BASE_ARG = typeof args.base === "string" && args.base.trim() ? args.base.trim() : ""`) when re-saving the workflow via SaveWorkflow; until then every invocation needs the draft-copy patch (2026-09-26 audit run).