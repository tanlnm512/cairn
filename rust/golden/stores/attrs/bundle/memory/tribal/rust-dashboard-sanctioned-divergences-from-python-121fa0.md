---
type: Tribal-decision
title: rust dashboard sanctioned divergences from python
description: rust dashboard sanctioned divergences from python
resource: rust/crates/cairn-cli/src/dashboard/data.rs
tags:
- decision
generated:
  by: cairn/0.22.0
  at: '2026-10-19T08:43:22Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-19T08:43:22Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-19T08:43:22Z'
---

Three recorded divergences on the rust dashboard: (1) token estimation is always heuristic chars/4 (the exact tokenizer is part of the Python-only heavy stack, plan §9), so /tokens and /history token-estimate fields differ where python calibrates; (2) reranker_available is false (no native cross-encoder; an ort backend is a possible post-flip D), so /health shows the reranker unavailable where the python venv has one; (3) static URLs are root-relative (/static/...) instead of Starlette's request-absolute form, and minijinja escapes / and ' as numeric entities - browser-equivalent. A differential run against the python server showed 16 of 17 sampled pages byte-identical after normalizing those, plus all sampled JSON routes identical.
Why: plan.md §9 keeps local inference out of rust; D-009 records that minijinja is Jinja2-compatible, not byte-identical.
How to apply: treat further page diffs as bugs first; check for TEXT-id binding (as_i64 on uuid ids) and map-order issues before assuming sanctioned divergence.