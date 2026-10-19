---
type: Tribal-decision
title: Doc-link proposals stay scoped, mirrored, and deduplicated
description: Doc-link proposals stay scoped, mirrored, and deduplicated
resource: src/cairn/knowledge/doc_link.py
tags:
- decision
generated:
  by: cairn/0.21.2
  at: '2026-10-07T17:31:07Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.625
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.92
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-07T17:31:07Z'
  action: captured
  score: 0.92
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-07T17:31:07Z'
---

Doc-link parsing tolerates markdown wrappers around one edge per line; validation confines endpoints to known island members and rejects self-links; accepted proposals mirror into both documents without duplicating an existing pair.
Why: model formatting varies, while inferred links must stay within the isolated corpus and satisfy the index's directional frontmatter.
How to apply: keep parse, critic, and apply separate; mirror through MIRROR_RELATION and let any existing pair+relation win.