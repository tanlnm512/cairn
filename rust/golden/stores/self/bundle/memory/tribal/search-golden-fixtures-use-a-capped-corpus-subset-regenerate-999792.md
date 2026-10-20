---
type: Tribal-decision
title: Search golden fixtures use a capped corpus subset, regenerated via --record
description: Search golden fixtures use a capped corpus subset, regenerated via --record
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-19T14:53:29Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-19T14:53:29Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
---

The search golden fixtures (rust/golden/spike/search-store.kg, rust/golden/search/semantic-store.kg) are built from an evenly spaced, path-sorted sample of at most SUBSET_FILE_CAP=260 indexable workspace files (constant in scripts/rust_migration/spike_golden.py, shared by semantic_golden.py). --record re-baselines the slim store + the rust/golden/mcp/{search_symbols,semantic_search}.json spike_queries in one run; default verify mode is the corpus-drift tripwire. Why: 07cb16e grew the checked-in stores to 107.9MB + 26.6MB (879-file corpus); the builder stamps uuid4 ids/indexed_at/mtime per build, so store bytes can never be byte-deterministic and bm25/cosine tie order flips across rebuilds. How to apply: regenerate with scripts/rust_migration/{semantic_golden,spike_golden}.py --record (spike first), then run plain (verify) until it passes, then cargo test --test spike_golden --test golden_search in rust/. The semantic golden query must stay score-separated -- record mode anchors it on the longest symbol name; wide tie pools (e.g. constructor over minified .mjs) flip the LIMIT-10 selection per rebuild and will flake verify.