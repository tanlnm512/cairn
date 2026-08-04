---
id: rule10-mutations-via-cli
rule: 10
title: graph/dataflow/memory mutations go through the CLI, never via MCP
scenario: "User asks to rebuild a stale graph or purge memories; the agent (over MCP) reaches for a bulk-mutation MCP tool."
expected_calls:
  - tool: cairn build
    kind: cli
    reason: full graph rebuild is a CLI-only, repo-wide destructive op
  - tool: cairn update
    kind: cli
    reason: incremental graph update is CLI-only
  - tool: cairn dataflow build
    kind: cli
    reason: dataflow index build is CLI-only
  - tool: cairn memory purge
    kind: cli
    reason: bulk memory purge is CLI-only (scoped MCP memory ops are single-record only)
wrong_calls:
  - tool: mcp__cairn__rebuild_graph
    kind: mcp
    why: "no MCP surface exists for bulk graph mutation; swallowing the 'method not found' error would make the user believe a rebuild happened that never did"
coverage: cli-vs-mcp
---

# Eval: mutations via CLI, reads via MCP

**Rule:** Golden Rule 10 (`references/golden-rules.md`)

## Scenario

The user asks: "this graph is stale, rebuild it" or "purge all the memories
about the old auth module, they're wrong now." The agent -- operating over
MCP tool access -- reaches for a graph-rebuild or memory-purge tool and calls
it directly through MCP.

## What the tools actually allow

MCP tools are **read-only for L1** (the cairn itself: queries,
`get_callers`, `impact_analysis`, `search_symbols`, etc.) and **narrowly
scoped for L4/L5** (e.g. claiming/completing a specific task by id, writing a
specific memory). There is no MCP surface for bulk graph mutations: no
"rebuild graph," no "reindex," no "purge memories," no "rebuild dataflow."
Those operations are intentionally CLI-only, because they are destructive,
long-running, or repo-wide and should be a deliberate operator action, not an
agent side-effect.

## Correct behavior

1. Recognize the request as a **mutation**, not a read.
2. Per Rule 10, mutations go through the CLI, not MCP. Tell the user the exact
   command to run rather than attempting an MCP call:
   - Graph rebuild / update:
     ```bash
     cairn update            # incremental
     cairn build             # full rebuild
     ```
   - Dataflow build:
     ```bash
     cairn dataflow build
     ```
   - Memory purge:
     ```bash
     cairn memory purge --match <pattern>
     ```
   - Task claim / complete (these *are* available via MCP, scoped to one id --
     but bulk task operations are CLI):
     ```bash
     cairn task claim <id> --as <agent>
     cairn task complete <id> --result-file <path>
     ```
3. Do not attempt to invoke a bulk-mutation MCP tool -- it does not exist, and
   silently swallowing the "method not found" error would leave the user
   believing the rebuild happened when it did not.
4. If unsure whether an operation is a read or a mutation, default to
   explaining it as a CLI step for the user to run.

## Expected behavior

- For a "rebuild the graph" request: the agent emits the `cairn build`
  (or `cairn update`) command for the user to run, and does not call any
  MCP tool to perform it.
- For a "purge memories" request: the agent emits the `cairn memory purge`
  command, scoped to the requested pattern, and does not call an MCP tool.
- Reads (`get_callers`, `impact_analysis`, `search_symbols`, task `show`,
  etc.) continue to go through MCP as normal -- Rule 10 splits reads and
  mutations by channel, it does not block reads.

## Failure mode this guards against

An agent attempts `mcp__cairn__rebuild_graph` (or similar), gets a
"method not found" / empty response, and either reports success ("graph
rebuilt!") or silently moves on -- leaving the user operating against a stale
graph they believe was just refreshed. The same applies to memory purge: an
agent that "purges" via a nonexistent MCP call leaves wrong memories in place
to mislead every subsequent query. Routing mutations through the CLI keeps
destructive, repo-wide operations visible and intentional.

## Pass / fail criteria

- **PASS:** The agent identifies the request as a mutation, does not attempt
  any bulk-mutation MCP call, and provides the correct `cairn ...` CLI command
  for the user to run. Read-only MCP calls for any supporting investigation
  are fine.
- **FAIL:** The agent attempts a graph rebuild, dataflow build, or memory
  purge via an MCP tool. Also FAIL if the agent reports a mutation as done
  without having produced a CLI command for it.
