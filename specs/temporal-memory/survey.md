# Survey: temporal-memory

**Created**: 2026-09-16 | **Baseline**: codex/docs-level-up-specs @ 1af3b35
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item S1: "No temporal validity columns exist anywhere in memory"
  evidence:   `grep -rn "valid_from\|valid_until\|as_of\|as-of" src/cairn/memory/ src/cairn/okf/` → no matches
  status:     TODO
  verify:     grep -rn "valid_from\|valid_until\|as_of" src/cairn/memory/ src/cairn/okf/
  gap:        the schema addition itself (memory records are OKF concepts + extensions)

item S2: "Stale-reference detection exists and can mark concepts"
  evidence:   src/cairn/cli/validate.py:31 `@main.command(name="validate-paths")`; :35 `def validate_paths(db, knowledge, mark)`; --mark path sets `c.extensions["stale"] = True`; delegates to `cairn.compass.critic.validate_paths`
  status:     DONE
  verify:     sed -n '31,66p' src/cairn/cli/validate.py
  gap:        boolean flag only — no validity interval, no successor link, no build-time trigger

item S3: "Lifecycle machinery is tier movement + decay"
  evidence:   src/cairn/memory/promotion.py:397 `def promote_memory(`; src/cairn/memory/store.py:234 `def demote_memory(`, :273 `def purge_archived(`; cli/memory.py has `promote` and `decay` subcommands
  status:     DONE
  verify:     grep -n "@memory.command" src/cairn/cli/memory.py
  gap:        no time-travel query surface

item S4: "Recall surfaces: MCP tool + CLI search (no `cairn memory recall`)"
  evidence:   src/cairn/mcp_server/tools_memory.py:21 `def recall_memory(query: str, tier: str = "", include_superseded: bool = False) -> str:`; cli/memory.py subcommand list contains `search`, not `recall`
  status:     DONE
  verify:     grep -n "@memory.command" src/cairn/cli/memory.py
  gap:        neither surface takes an as-of date

item S5: "Incremental builder carries no rename/successor signals"
  evidence:   `grep -n "rename\|successor\|moved" src/cairn/graph/incremental.py` → no matches (only duplicate-deletion and removed-name repair-pass comments)
  status:     TODO
  verify:     grep -n "rename\|successor" src/cairn/graph/incremental.py
  gap:        successor linking must be derived from graph identity, not builder events
```

## Supporting evidence
Memory persistence is OKF-concept-based (src/cairn/okf/bundle.py read/write_concept;
src/cairn/memory/store.py store_memory) — validity columns land in concept extensions
or a joined index table; the additive-migration pattern is established by
schema.py's "Additive-only: plain CREATE TABLE IF NOT EXISTS" table comments.

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
