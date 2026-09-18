# Survey: ide-extensions

**Created**: 2026-09-16 | **Baseline**: main @ d18768c (delta re-survey 2026-09-17 —
19 files changed since 1af3b35, all skillgen/CLI-skill; none cited by any item, so
items are carried byte-identical with every citation re-verified this session)
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item S1: "A local HTTP server stack over the store already exists (dashboard)"
  evidence:   src/cairn/dashboard/app.py:34-35 `DEFAULT_HOST = "127.0.0.1"` / `DEFAULT_PORT = 8765`; src/cairn/cli/dashboard.py:55-56 `# Server stack (starlette/jinja2/uvicorn — transitive deps of mcp) loads only here`; src/cairn/dashboard/routes/ has core.py, graph.py, history.py, knowledge.py, memory.py, settings.py, wiki.py
  status:     DONE
  verify:     ls src/cairn/dashboard/routes/ && grep -n "DEFAULT_PORT" src/cairn/dashboard/app.py
  gap:        routes render HTML for the browser; no editor-facing JSON API contract

item S2: "An auto-managed background server precedent exists (SSE daemon)"
  evidence:   src/cairn/cli/serve.py:94 `"""Install and start the persistent SSE daemon (macOS launchd). ... auto-starts at login and restarts on crash (KeepAlive)."""`; lifecycle.py:25 `DEFAULT_PORT = 9876`
  status:     DONE
  verify:     grep -n "DEFAULT_PORT" src/cairn/mcp_server/lifecycle.py
  gap:        daemon serves MCP/SSE, not editor hover/lens queries

item S3: "Graph data needed by hover/lens is queryable today"
  evidence:   src/cairn/mcp_server/tools_graph.py:99 `def get_callers(...)` / :202 `def get_callees(...)` / :290 `def impact_analysis(...)` (precise default, depth-limited); tools_compass.py:34 `def get_compass(module)`
  status:     DONE
  verify:     grep -n "^def get_callers\|^def get_callees\|^def impact_analysis" src/cairn/mcp_server/tools_graph.py
  gap:        no extension-side consumer; no LSP/IDE code exists in-repo
```

## Supporting evidence
No IDE extension code exists anywhere in the repo (proposal §6.2 places extensions in
separate repositories). The extension's backend options both exist today: dashboard
routes (starlette) and the MCP SSE daemon; neither requires new Python deps.

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
