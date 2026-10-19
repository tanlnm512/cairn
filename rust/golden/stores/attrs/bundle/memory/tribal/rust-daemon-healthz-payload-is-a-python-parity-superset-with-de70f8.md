---
type: Tribal-decision
title: Rust daemon healthz payload is a Python-parity superset with uptime and impl
  fields
description: Rust daemon healthz payload is a Python-parity superset with uptime and
  impl fields
resource: rust/crates/cairn-daemon/src/healthz.rs
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-18T19:52:15Z'
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
- date: '2026-10-18T19:52:15Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-18T19:52:15Z'
---

The Rust daemon (rust/crates/cairn-daemon/src/healthz.rs) serves the Python /healthz fields (status, store_reachable, read_only, degradations count, db) plus uptime_seconds and impl=rust. Python parity consumer is connect's reuse guard, which reads only the db field (src/cairn/cli/connect.py), so the superset keeps cairn connect working unchanged.
Why: workstream 1d (CAIRN_DAEMON_IMPL=rust, doctor reporting the serving impl) needs an impl marker and uptime in the payload; payload parity (plan §5 1c) forbade removing Python fields.
How to apply: when 1d wires the impl switch or doctor reads healthz, consume the existing impl and uptime_seconds fields instead of inventing new ones; keep db byte-identical to str(resolve_store().db) so the store-mismatch guard compares equal.