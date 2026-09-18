# Survey: shared-memory-bus

**Created**: 2026-09-16 | **Baseline**: codex/docs-level-up-specs @ d18768c
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item S1: "Store runs in WAL mode with busy timeout"
  evidence:   src/cairn/graph/schema.py:844 `conn.execute("PRAGMA journal_mode = WAL")`; :851 `conn.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")` (inside the shared connect(); WAL set on writable opens, busy_timeout on all)
  status:     DONE
  verify:     sed -n '837,855p' src/cairn/graph/schema.py
  gap:        no cross-agent locking protocol; no memory row ownership

item S2: "Shared-daemon precedent: contention was the design driver"
  evidence:   src/cairn/cli/serve.py:97-98 `one `cairn serve --port N` process shared by all MCP clients, replacing the one-stdio-server-per-client model that caused "database is locked"`
  status:     DONE
  verify:     sed -n '90,105p' src/cairn/cli/serve.py
  gap:        sharing is process-level, not agent-level; no agent identity

item S3: "Session-scoped activity tables already record who touched what"
  evidence:   src/cairn/graph/schema.py:121-131 `CREATE TABLE IF NOT EXISTS memory_refs (... session_id TEXT NOT NULL ...)`; tool_metrics table (:330); events table (:385, `session_id TEXT, attrs TEXT`); src/cairn/telemetry/events.py:152 `def emit(name: str, **attrs: Any) -> None:`
  status:     DONE
  verify:     grep -n "session_id" src/cairn/graph/schema.py | head -5
  gap:        session ids are per MCP session, not stable agent identities; no overlap query exists

item S4: "Memory store API is the write chokepoint for sharing"
  evidence:   src/cairn/memory/store.py:42 `def create_memory(`; :99 `def store_memory(concept, bundle, tier=None, old_id=None)`; src/cairn/mcp_server/tools_memory.py:21 `def recall_memory(...)` / :134 `def record_memory(`
  status:     DONE
  verify:     grep -n "def store_memory\|def create_memory" src/cairn/memory/store.py
  gap:        no visibility/namespace parameter; no share command
```

## Supporting evidence
No `cairn memory share` or agent-registration command exists (cli/memory.py subcommands:
record, evolve, search, capture, list, stats, digest, promote, decay, embed,
batch-critic, forget, demote, purge, consolidate; memory group registered at
src/cairn/cli/__init__.py:36; no `share` command or `--agent` option anywhere
in src/cairn/cli/).
One MCP resource exists: `@mcp.resource("cairn://status")`
(src/cairn/mcp_server/_server_core.py:471) — a read-only index/build status
block. No memory-content resource; no subscription/notification machinery
(no `subscribe`/`notify` code in src/cairn/mcp_server/ or src/cairn/memory/).

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
