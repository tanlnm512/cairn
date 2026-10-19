---
type: Tribal-decision
title: 'Rust migration grammar pins: crates.io dart crate is a different grammar than
  Python''s'
description: 'Rust migration grammar pins: crates.io dart crate is a different grammar
  than Python''s'
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-18T15:58:08Z'
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
- date: '2026-10-18T15:58:08Z'
  action: captured
  score: 0.95
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-18T15:58:08Z'
---

For the rust-migration (specs/rust-migration/decisions.md D-005): crates.io `tree-sitter-dart` (0.1.0/0.2.0) is nielsenko/tree-sitter-dart, but the Python side pins tree-sitter-dart==0.1.0 built from efrenbl/tree-sitter-dart — two independent grammars. Pinning the crates.io crate would silently swap dart implementations against every dart golden.
Why: the .kg content and goldens encode node-types from the Python-pinned parser.c releases; swapping grammar implementations breaks C-1 parity silently.
How to apply: vendor efrenbl generated sources at tag v0.1.0 behind a thin workspace crate exposing tree_sitter_language::LanguageFn (its in-repo Rust binding is the old tree-sitter-0.22-embedding style, unusable as a git dep with a 0.26 core). Kotlin is the opposite case: fwcd/tree-sitter-kotlin crates.io crate (0.3.8, 2024-08) is ~2y stale vs the vendored pin 1852ea17b7f60fb3f9d84e0b1555d56b46b39fb1 — use a commit-pinned git dependency (modern tree-sitter-language style). php has no crates.io 0.24.1 (Python pin); nearest same-minor is 0.24.2, gate on 2a node-type revalidation.