---
type: Tribal-pattern
title: Patch lazy PEP-562 proxies at the source module, never on the proxy
description: Patch lazy PEP-562 proxies at the source module, never on the proxy
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-10-15T16:37:45Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.578
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.7
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-15T16:37:45Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-15T16:37:45Z'
---

monkeypatch.setattr on a module whose attribute comes from __getattr__ (e.g. cairn.graph.queries.semantic_search) records the __getattr__-resolved original at teardown and RESTORES it by setting the attribute — permanently shadowing __getattr__ for the rest of the process, so later tests that patch the source module are silently ignored.
Why: monkeypatch restores by value, not by deleting the synthetic attribute; a set module attribute beats __getattr__ forever.
How to apply: in tests, always monkeypatch the defining module (cairn.graph.semantic) and let the lazy proxy resolve through it; the eval suite's _raise_semantic documents the pattern.