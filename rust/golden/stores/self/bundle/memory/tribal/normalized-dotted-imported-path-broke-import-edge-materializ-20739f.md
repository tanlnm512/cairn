---
type: Tribal-mistake
title: Normalized dotted imported_path broke import-edge materialization
description: Normalized dotted imported_path broke import-edge materialization
tags:
- mistake
generated:
  by: cairn/0.20.2
  at: '2026-10-15T16:38:00Z'
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
memory_type: mistake
session_origin: ''
promotion_history:
- date: '2026-10-15T16:38:00Z'
  action: captured
  score: 0.7
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-15T16:38:00Z'
---

Storing clean dotted module paths in the imports table (python_parser plain/from imports) made builder._import_module_bases receive pre-normalized text with no from/import prefix, so from-imports lost their module base and materialize_import_edges produced zero edges.
Why: _import_module_bases parsed raw statement text; normalized paths fell to the else branch returning only the full member-qualified path, never the module that matches a file.
How to apply: when changing a storage shape, grep every consumer of the old shape (materialize, resolver, cross_repo prefix matching) and extend them in the same change; the full-suite run caught what per-module tests missed.