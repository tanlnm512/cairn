---
type: Tribal-decision
title: Kotlin grammar crate is byte-identical to the vendored C grammar
description: Kotlin grammar crate is byte-identical to the vendored C grammar
resource: rust/crates/cairn-parse/src/parser_jvm.rs
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-19T00:23:39Z'
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
- date: '2026-10-19T00:23:39Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
---

The pinned tree-sitter-kotlin crate (fwcd @ 1852ea17) and vendor/tree-sitter-kotlin build from byte-identical parser.c and node-types.json (md5 892464b4dac74f0c1a50986572b6cfa7): 142 named / 126 unnamed types on both sides, zero mismatches.

Why: the rust-migration plan flags 'Kotlin crate coverage gap vs vendored grammar' as a Phase-2a risk; the node-type revalidation gate passes with this evidence.

How to apply: parser_jvm.rs coverage tests encode the live queried node kinds. The only kotlin.py-queried names absent from BOTH grammars are defensive dead checks (interface_declaration, enum_declaration, function_value_parameter, type_reference); java.py's class_type is likewise absent from tree_sitter_java 0.23.5 wheel and crate alike.