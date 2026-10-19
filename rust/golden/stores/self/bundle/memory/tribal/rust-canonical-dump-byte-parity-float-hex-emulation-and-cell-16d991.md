---
type: Tribal-decision
title: 'Rust canonical-dump byte parity: float.hex emulation and cell-tuple sorting'
description: 'Rust canonical-dump byte parity: float.hex emulation and cell-tuple
  sorting'
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-18T18:02:56Z'
okf_version: '0.2'
memory_status: tribal
memory_score: 0.632
memory_signals:
  graph_verification: 1.0
  cross_session_refs: 0
  agent_confidence: 0.95
  critic_score: 0.5
  freshness: 1.0
  reinforcement: 0.0
  authority: 0.5
memory_tier: tribal
tier: asserted
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-18T18:02:56Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-18T18:02:56Z'
---

The Rust canonical dump (rust/crates/cairn-store/src/dump.rs, exposed via `cairn canonical-dump`) mirrors scripts/rust_migration/kg_dump.py byte-for-byte; kg_diff.py over the cairn self-store reports "dumps identical: 30 tables, 552015 rows" and the raw files are cmp-identical. Non-obvious details pinned for future ports: (1) Python float.hex() must be emulated exactly — normal doubles are 0x1.<13 hex digits, trailing zeros kept>p<signed decimal exp>, subnormals are 0x0.<raw 52-bit mantissa as 13 hex digits>p-1022, zero is ±0x0.0p+0, inf/±, and nan WITHOUT sign; SQLite normalizes -0.0 to 0.0 on storage. (2) Rows sort by the cell-TUPLE (Vec<Vec<String>>), never by the tab-joined line — tab sorts below printable chars, so the two orders diverge. (3) The "[table x]" header-lookalike escape only fires when the FULL joined row line both starts with "[table " AND ends with "]". Why: the canonical dump is the C-1 parity arbiter; a silent format drift would false-pass or false-fail every Phase 2 differential run. How to apply: when porting more dump/diff semantics or debugging golden diffs, reproduce these three rules exactly and generate expected floats from real CPython, not from memory.