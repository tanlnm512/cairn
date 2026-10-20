---
type: Tribal-mistake
title: search_symbols goldens flake on bm25 ties across rebuilds
description: search_symbols goldens flake on bm25 ties across rebuilds
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-10-18T17:26:42Z'
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
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-18T17:26:42Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-18T17:26:42Z'
---

`ORDER BY bm25(symbols_fts) rank` tie order is build-nondeterministic: equal-scoring rows (e.g. the three `activate` helpers in tests/test_graft_parity_*.py) flip relative order between two builds of the identical tree, because the parse pool makes symbols.rowids nondeterministic (documented in golden_record.refine_targets) and FTS doclist scan order follows rowid. One rebuild reproduced the committed search_symbols.json golden; the next flipped two lines and failed the P0-3 diff oracle.
Why: the P0-3 recorder has no tie normalization for search_symbols (sort_lines=false), so re-recorded goldens can differ from committed ones without any real behavior change.
How to apply: never compare search_symbols goldens across DIFFERENT store builds; compare against frozen store bytes (the P0-5 slim fixture pattern: rust/golden/spike/search-store.kg + spike_queries in the fixture). Bundled rusqlite SQLite and Python system SQLite order ties identically on identical bytes (verified empirically, 5/5 stable). If Phase 1 wants build-stable goldens, add a rowid tiebreak or sort-tie normalization as a recorded ruling.