# Survey: pr-review-loop

**Created**: 2026-09-16 | **Baseline**: codex/docs-level-up-specs @ 1af3b35
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item S1: "Diff-seeded reverse-dependency blast exists as a CLI + engine"
  evidence:   src/cairn/graph/blast.py:1 `"""Diff-seeded reverse dependency radius."""`; blast.py:116 `_changed_files(conn, workspace, base)`; blast.py:162 `_seed_symbols(conn, changed)`; blast.py:471 `def compute_blast(`; cli/blast.py exposes `--base`, `--fuzzy`, `--refresh/--no-refresh` ("Reindex drifted files before diffing")
  status:     DONE
  verify:     grep -n "def compute_blast\|_seed_symbols" src/cairn/graph/blast.py
  gap:        output is radius-only; no memory/compass/wiki enrichment, no pre-submit mode

item S2: "Symbol spans enable comment file+line → enclosing symbol mapping"
  evidence:   src/cairn/graph/schema.py:42-43 `line_start INTEGER,` / `line_end INTEGER,` on the symbols table
  status:     DONE
  verify:     sed -n '36,50p' src/cairn/graph/schema.py
  gap:        no GitHub-comment-to-symbol mapping anywhere

item S3: "Memory substrate: types, tiers, decay, and an auto-capture precedent"
  evidence:   src/cairn/memory/promotion.py:21 `def capture_memory(`; :242 `def search_memory(`; :397 `def promote_memory(`; cli/memory.py subcommands: record, evolve, search, capture, list, stats, digest, promote, decay, embed; src/cairn/graph/schema.py:138 `CREATE TABLE IF NOT EXISTS memory_failure_signatures (` with comment "post_tool_failure auto-capture recurrence gate"
  status:     DONE
  verify:     grep -n "@memory.command" src/cairn/cli/memory.py
  gap:        capture is failure-signature-shaped; nothing captures resolved review comments

item S4: "Compass/wiki readers exist for enrichment"
  evidence:   src/cairn/mcp_server/tools_compass.py:50 `def search_knowledge(query: str, type_filter: str = "", ...)` (type_filter="Wiki" selects wiki pages); :34 `def get_compass(module: str)`
  status:     DONE
  verify:     grep -n "def search_knowledge\|def get_compass" src/cairn/mcp_server/tools_compass.py
  gap:        not composed into any review-facing pack
```

## Supporting evidence
Hooks infrastructure exists for local automation (src/cairn/hooks/: claude_hooks.py,
cursor_hooks.py, git_hooks.py) — a pre-push guard has an install surface to ride;
GitHub-side capture needs a new Action (none exists under .github/workflows for review).

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
