---
type: Tribal-decision
title: 'cairn-parse 2a scaffold: family agents own parser_*.rs only'
description: 'cairn-parse 2a scaffold: family agents own parser_*.rs only'
resource: rust/crates/cairn-parse/src/lib.rs
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-18T23:36:39Z'
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
memory_type: decision
session_origin: ''
promotion_history:
- date: '2026-10-18T23:36:39Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
---

Each of the five 2a family agents owns exactly one file: rust/crates/cairn-parse/src/parser_jvm.rs (kotlin, java), parser_web.rs (typescript, javascript), parser_apple.rs (swift, objc), parser_sys.rs (go, rust, c, cpp, csharp), parser_script.rs (python, php, ruby, dart). Shared infra is pre-wired: src/registry.rs routes all 16 language names (incl. tsx) to pinned grammar crates per decisions.md D-004/D-005; src/generic.rs ports GenericTreeSitterParser as the GenericTier trait (config() + symbol_kind/implementation_name hooks) with parse_file/parse_source; src/model.rs mirrors base.py dataclasses (Symbol/Edge/Import/RationaleRecord/ParsedFile, Edge.generic_tier marks generic-tier call edges); src/rationale.rs ports _rationale.py. Dart grammar is vendored at rust/vendor/tree-sitter-dart (efrenbl v0.1.0, commit 12ba73f) as crate cairn-tree-sitter-dart exposing LANGUAGE; kotlin is the cargo git dep pinned to 1852ea17.
Why: five agents landing concurrently would conflict on lib.rs/Cargo.toml; grammar deps and registration are already compiled and smoke-tested, so per-language work needs zero shared-file edits.
How to apply: implement only inside your parser_*.rs file, via a type implementing GenericTier (generic tier, like Python parsers/rust.py) or custom traversal over registry::parser() (like Python parsers/kotlin.py); never edit lib.rs, registry.rs, model.rs, generic.rs, rationale.rs, or Cargo.toml.