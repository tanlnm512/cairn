---
type: Tribal-decision
title: rust dashboard embeds assets and renders via minijinja
description: rust dashboard embeds assets and renders via minijinja
resource: rust/crates/cairn-cli/src/dashboard/mod.rs
tags:
- decision
generated:
  by: cairn/0.22.0
  at: '2026-10-19T08:43:21Z'
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
- date: '2026-10-19T08:43:21Z'
  action: captured
  score: 0.85
  tier: tribal
memory_is_latest: true
memory_supersedes: []
memory_superseded_by: null
valid_from: '2026-10-19T08:43:21Z'
---

Phase 3b serves the dashboard from the rust binary: templates and static assets are copied to rust/crates/cairn-cli/dashboard-assets and embedded at build time (build.rs emit + include_bytes, no include_dir dependency); the router is axum over minijinja 3.0.1 with features serde, json, urlencode, preserve_order. Compat patches to ten rust-side template copies (.append/.update mutation, sum(attribute=), tools attrs, path.strip/split) are allowlisted and sha-pinned by the dashboard_template_drift test; all other files must stay byte-identical to src/cairn/dashboard. minijinja dict.get/items/keys/values/update are provided by an env unknown-method callback; tojson is overridden to match Jinja2's sort_keys and htmlsafe escapes.
Why: D-009 sanctions minijinja; single-binary distribution (plan §1) rules out sidecar assets; the drift test is the transition-era drift guard until the 3d flip.
How to apply: when templates change in src/cairn/dashboard, re-copy into dashboard-assets and re-pin the drift-test hashes deliberately; do not patch the python tree for rust-only needs.