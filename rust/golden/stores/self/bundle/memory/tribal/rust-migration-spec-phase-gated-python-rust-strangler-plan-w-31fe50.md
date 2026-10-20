---
type: Tribal-decision
title: 'rust-migration spec: phase-gated Python→Rust strangler plan with frozen .kg
  + MCP contracts'
description: 'rust-migration spec: phase-gated Python→Rust strangler plan with frozen
  .kg + MCP contracts'
resource: specs/rust-migration/plan.md
tags:
- decision
generated:
  by: cairn/0.20.2
  at: '2026-10-18T15:10:12Z'
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
- date: '2026-10-18T15:10:12Z'
  action: captured
  score: 0.9
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-18T15:10:12Z'
---

The rust-migration spec (specs/rust-migration/, created 2026-10-18) executes the Python→Rust feasibility assessment of the same date. Shape: strangler migration, not big-bang — Phase 0 foundations (parity harnesses: MCP goldens + .kg canonical dump), Phase 1 Rust read-path daemon behind CAIRN_DAEMON_IMPL=rust (gate G1: 26/26 tools golden-equal, RSS ≤60%), Phase 2 build path (gate G2: canonical-dump-equal corpora, closure ≥5×), Phase 3 CLI/distribution/flip to v1.0. Frozen contracts: the .kg schema and the 26-tool MCP surface — both language-neutral, both arbitrated by goldens.

Why: the 115k-LOC pytest suite is the behavioral spec a big-bang rewrite would lose; the phase gates keep Python authoritative and shippable until the flip, so a solo maintainer can stop after any phase without a stranded half-rewrite.

How to apply: any Rust-migration work starts from specs/rust-migration/plan.md, not from a fresh survey; new runtime crates land as C-03 D-### decisions; divergences from Python behavior are golden-captured and ruled, never silent. Local sentence-transformers and PDF/docx ingest are deliberately NOT ported (server backend / Python sidecar).