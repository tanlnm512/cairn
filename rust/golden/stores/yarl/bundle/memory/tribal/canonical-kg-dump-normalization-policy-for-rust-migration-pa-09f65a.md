---
type: Tribal-decision
title: Canonical .kg dump normalization policy for Rust-migration parity
description: Canonical .kg dump normalization policy for Rust-migration parity
resource: scripts/rust_migration/kg_dump.py
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-18T16:20:08Z'
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
- date: '2026-10-18T16:20:08Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-18T16:20:08Z'
---

kg_dump.py (scripts/rust_migration) defines the P0-3 canonical dump format: rows sorted by escaped-cell tuple, hidden rowids never selected, floats rendered via float.hex() (C99 hex-float, bit-exact and Rust-portable — Python repr is not, its exponent formatting differs from Rust's), blobs as x'<hex>', NULL as \N, tab/newline/backslash escaped. FTS5 virtual tables and their shadow tables are skipped (index is derivably rebuildable from the exported symbols table; binary doclists are insertion-order dependent so they can never compare equal across builds). vec0 virtual tables and shadows are skipped from sqlite_master.sql classification alone — PRAGMA table_info on a vec0 vtable raises "no such module: vec0" when the extension is not loaded. Table inventory comes from sqlite_master, not SCHEMA_SQL: the live self-store holds repo_deps, which no current Python code creates (schema drift), and the dump must tolerate that.
Why: the .kg schema is the frozen cross-language contract (C-1) and this normalization is defined once here for the whole migration; the Rust port of the dumper must reproduce the format byte-for-byte.
How to apply: when porting the canonical dump to Rust (G2), render floats as C99 hex-float, classify tables from sqlite_master.sql before any introspection, and never SELECT from virtual tables. Note: symbols/edges ids are uuid4().hex (builder.py _new_id), so cross-build row equality additionally requires id relabeling — deferred to plan workstream 2c, out of P0-3 scope.