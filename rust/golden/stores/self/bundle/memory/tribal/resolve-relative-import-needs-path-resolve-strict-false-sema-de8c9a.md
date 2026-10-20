---
type: Tribal-pattern
title: resolve_relative_import needs Path.resolve(strict=False) semantics, not canonicalize
description: resolve_relative_import needs Path.resolve(strict=False) semantics, not
  canonicalize
resource: rust/crates/cairn-parse/src/parser_web.rs
tags:
- pattern
generated:
  by: cairn/0.20.2
  at: '2026-10-19T00:14:48Z'
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
memory_type: pattern
session_origin: ''
promotion_history:
- date: '2026-10-19T00:14:48Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
---

Porting Python (importer.parent / spec).resolve() to Rust must not use plain canonicalize() - it fails on any missing component, and must not use lexical dot normalization alone - it skips symlink resolution of existing prefixes (macOS /tmp -> /private/tmp changes the returned string). Why: the parser contract stores the resolved base in Import.imported_path; a wrong resolver either drops the resolution (candidate exists, base does not) or returns a path string that diverges from the Python-built .kg. How to apply: recursive canonicalize-with-fallback - canonicalize when it succeeds, else resolve the parent recursively and append the missing file_name (resolve_existing_prefix in rust/crates/cairn-parse/src/parser_web.rs); verify with a filesystem-backed test, not string expectations built from the unresolved temp path.