# Survey: remote-mcp-oauth

**Created**: 2026-09-16 | **Baseline**: codex/docs-level-up-specs @ 1af3b35
The survey node's output — the single source of truth for code state. Every citation
in the other four docs must trace to a line here. Evidence is pasted
verbatim from grep/read output in the session that wrote it.

## Items

```
item S1: "Serving surface is stdio + SSE only; no Streamable HTTP, no auth"
  evidence:   src/cairn/cli/serve.py:86 `run(transport="sse" if port else "stdio", port=port)`; src/cairn/mcp_server/server.py:211 `if transport == "stdio":` / :357 `if transport == "sse":` / :376 `mcp.run(transport="sse")` — no other transport branch exists
  status:     DONE
  verify:     grep -n "transport" src/cairn/mcp_server/server.py
  gap:        no principal/token check anywhere in the request path

item S2: "SSE daemon is launchd-managed, loopback, read-only by default"
  evidence:   src/cairn/cli/serve.py:82 `read_only = bool(port)`; serve.py:92 `@click.option("--host", default="127.0.0.1", ...)`; src/cairn/mcp_server/lifecycle.py:25 `DEFAULT_PORT = 9876`; serve start installs a launchd plist (KeepAlive)
  status:     DONE
  verify:     grep -n "DEFAULT_PORT\|render_plist\|KeepAlive" src/cairn/mcp_server/lifecycle.py
  gap:        remote (non-loopback) serving, per-workspace authorization

item S3: "mcp SDK already carries Streamable HTTP + server deps as core"
  evidence:   pyproject.toml:46 `"mcp>=0.9.0,<2.0.0",  # SSE/streamable-http + uvicorn/starlette are core deps in mcp>=0.9; the legacy [sse] extra was removed upstream` (uv.lock resolves mcp 1.29.0)
  status:     DONE
  verify:     grep -n '"mcp' pyproject.toml && grep -B1 -A2 '^name = "mcp"$' uv.lock
  gap:        no auth middleware; no IdP integration; no workspace routing

item S4: "24 MCP tools registered today"
  evidence:   `grep -c "^@mcp.tool"` → tools_graph.py:11, tools_compass.py:5, tools_knowledge.py:5, tools_memory.py:2, tools_wiki.py:1 (sum 24)
  status:     DONE
  verify:     grep -c "^@mcp.tool" src/cairn/mcp_server/tools_*.py
  gap:        parity harness for a second transport
```

## Supporting evidence
Tool registry is a shared FastMCP instance in `src/cairn/mcp_server/_server_core.py`
(`from ._server_core import mcp` decorates tools); transports are chosen only inside
`server.py:run()` — an additive HTTP+auth path does not touch tool bodies.

## Rules
- Every `file:line` pasted from grep/read in this survey — never from memory.
  Can't find it → write `unknown — verify`, don't guess.
- Status derives from evidence, not intent. Run every verify command.
- A number in an old doc is a claim, not evidence — re-count it.
