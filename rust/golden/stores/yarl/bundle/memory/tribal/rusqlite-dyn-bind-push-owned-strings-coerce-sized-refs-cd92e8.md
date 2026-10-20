---
type: Tribal-pattern
title: 'rusqlite dyn bind: push owned Strings, coerce sized refs'
description: 'rusqlite dyn bind: push owned Strings, coerce sized refs'
resource: rust/crates/cairn-query/src/tools_nav.rs
tags:
- pattern
generated:
  by: cairn/0.22.0
  at: '2026-10-18T19:01:36Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-18T19:01:36Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-18T19:01:36Z'
---

Building rusqlite bind lists as Vec<&dyn ToSql> from &str values fails: the &str-to-&dyn-ToSql unsize cast is rejected for the already-unsized str (only &T-to-&dyn-ToSql coerces when T is Sized).
Why: clippy/rustc surface it as 'the size for values of type str cannot be known' with a misleading &&str suggestion; blocking cairn-query tools_nav for a whole compile cycle.
How to apply: stage params in a Vec<String> and map to &dyn ToSql via sized &String refs, use params_from_iter for homogeneous &str slices, and keep literal arrays like &[&name, &limit] whose elements are &&str/&i64.