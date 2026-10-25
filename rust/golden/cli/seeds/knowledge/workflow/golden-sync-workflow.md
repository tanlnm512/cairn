---
type: Knowledge-workflow
title: golden sync workflow
description: golden sync workflow
resource: main
tags:
- golden
generated:
  by: cairn/0.23.0
  at: '2026-10-24T21:45:00Z'
okf_version: '0.2'
tier: asserted
doc_status: active
doc_owner: ''
doc_source: manual
epic_link: ''
affects_modules: []
affects_repos: []
steps:
- name: Stale
  description: Points at a gone symbol.
  symbol: GoneSymbol
  file: no/such/file.py
- name: Keep
  description: Anchored at a live symbol.
  symbol: main
  file: scripts/rust_migration/golden_record.py
---

# golden sync workflow

1. **Stale**
