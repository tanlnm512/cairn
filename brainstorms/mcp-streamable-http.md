# mcp-streamable-http — design specification

> Handoff artifact of a spec-brainstorming run. Pre-spec by
> design: it records the chosen direction and the reasoning, not
> requirements. The next step is `/spec-idea mcp-streamable-http`
> with this doc as intent input; requirements shaping happens
> there, with the user.

**Status**: handoff-ready · **Date**: 2026-10-11 ·
**Direction**: Visionary — one shared always-on Streamable HTTP daemon

## Direction

The chosen angle: Cairn's MCP server graduates from a stdio process
every agent spawns into a single always-on Streamable HTTP daemon on
the local machine — one warm graph serving every coding agent
(Kilo, Claude Code, future clients) over one URL, with stdio (and
SSE) removed entirely. The user's words: "replace completely stdio,
Cairn mcp should use only streamable http protocol."

Refinements locked during the run:

- No hand-rolled transport code — the daemon speaks the SDK's
  current Streamable HTTP shape (installed mcp 1.29.0 tops out at
  protocol 2025-11-25); MCP revision 2026-07-28 arrives later via an
  SDK bump, never as custom transport code Cairn maintains.
- One daemon, many clients — agents never own the server process;
  `cairn connect` ensure-running (healthz check → start detached if
  absent → dial) with the existing stray sweeper as enforcement;
  the port bind is the single-instance lock.
- Stateless HTTP mode for the shared daemon (already wired at
  `src/cairn/mcp_server/server.py:248`).
- Client config is URL + optional bearer header, matching the
  recommended pattern in Claude Code's docs and GitHub's reference
  MCP server.

- Selected: Visionary (shared daemon), with the Minimalist's
  deletion discipline (no escape hatch, no dual transport) and the
  Cynic's SDK constraint adopted as a hard rule.
- Rationale: one live graph all agents share — cross-agent memory
  visible instantly, one warm semantic index, one file watcher,
  boot cost paid once instead of per agent session.
- Rejected: Cynic (status quo) — the HTTP path already exists; the
  remaining work is deletion plus daemon ergonomics, and the
  multi-agent payoff is the product's compounding loop. Rejected:
  Minimalist as primary — pure deletion without the ensure-running
  story strands URL-only clients and daemon lifecycle stays manual.
  Rejected: 2026-07-28 revision chasing — spoken by neither the
  pinned SDK nor most clients today; dual-rev serving arrives free
  with an SDK bump (the GitHub MCP server already serves both eras
  by negotiation).

## Problem Statement

Every coding agent that wants Cairn's 26 MCP tools today spawns its
own stdio `cairn serve` process, each paying full boot (catch-up
scan, model warmup, memory decay), each running its own file
watcher against the same store, and each dying with its parent — so
knowledge recorded by one agent is cold and stale to the next.

- Current workaround: stdio transport per agent
  (`mcp.json.template` ships `"command": "cairn", "args":
  ["serve"]`); the codebase already fights the failure classes this
  creates (parent-death watchdog at server.py:284-285, stray
  sweeper at server.py:390-393).
- Cost of the status quo: N× boot latency and memory per concurrent
  agent, N contending watchers and writers on one SQLite store,
  divergent graph freshness across agents, and the cross-agent
  memory payoff structurally unreachable because no instance
  outlives its parent.

## User Personas

### Persona 1 — the multi-agent developer

- Who: a developer running two or more coding agents (Kilo, Claude
  Code) against the same workspace during a working day.
- Needs: one warm, always-fresh code graph; decisions recorded by
  one agent immediately visible to the others; no per-agent startup
  tax; no stray processes.
- Failed by: spawn-per-agent stdio — each session boots cold,
  watches privately, and dies with its agent, so shared memory and
  freshness never compound.

### Persona 2 — the single-agent local user

- Needs: the same tools with zero setup friction — point the agent
  at cairn and it works.
- Failed by: stdio's invisibility (no health endpoint, no shared
  state to inspect) and by a daemon model that requires remembering
  to start a server; needs `cairn connect` ensure-running so the
  first agent dial just works.

## Core MVP Features

1. HTTP-only server — serves personas 1 and 2. `cairn serve` runs
   Streamable HTTP only; stdio and SSE branches, the stdio
   parent-death watchdog, and their tests are deleted; stateless
   mode is the default.
2. `cairn connect` ensure-running — serves persona 2. Healthz-check
   the default port; start the daemon detached if absent (one-time
   lock, stray sweeper as enforcer); print/connect the URL; safe
   for concurrent agents racing the first start.
3. Client-config template — serves personas 1 and 2.
   `mcp.json.template` and AGENTS.md become URL + bearer-header
   shape (`{"type": "http", "url": "http://127.0.0.1:<port>/mcp"}`)
   with per-client one-liners (Claude Code `claude mcp add
   --transport http ...`).
4. Default port and key material — serves persona 2. A stable
   default loopback port and an auto-generated `CAIRN_MCP_API_KEY`
   when unset, so loopback zero-setup works and off-loopback stays
   gated (existing auth/bind policy, server.py:150-256, auth.py).

**Out of scope (day-one cut list)**: a `--stdio` escape hatch
(breaks the one-daemon invariant; clients that cannot dial URLs are
not served day one); OS service registration via launchd/systemd —
documented fast follow, not MVP; MCP revision 2026-07-28 compliance
(Mcp-Method header validation, MRTR, subscriptions/listen) —
arrives with an SDK bump only; remote/team exposure (TLS, non-
loopback hardening) beyond the existing bearer + bind policy;
dual-transport support of any kind.

## Potential Risk Mitigations

| Risk (from the panel) | Early warning | Mitigation or acceptance |
|---|---|---|
| Clients can't or won't dial an HTTP URL (Visionary's watch) | A daily-use agent of the user fails to connect against the daemon URL in the first week | Claude Code, Cursor, VS Code and Kilo support `type: http` configs (code.claude.com/docs/en/mcp, 2026-10-11); day-one validation = repoint one real client; agents without URL support are accepted as unsupported. |
| SDK (mcp 1.29.0, ≤2025-11-25) never gains 2026-07-28; team drifts into custom transport code | Any hand-rolled JSON-RPC-over-HTTP or header-validation code appearing in review | Hard rule in this spec: no custom transport code; the revision lands via `mcp>=X` SDK bump or not at all; pyproject pin moves only upstream. |
| Existing stdio configs break on upgrade (every consumer of `mcp.json.template`) | Issue reports of "cairn serve ignores stdio" post-release | Accepted: intentional breaking change; release notes carry the new URL config and the one-line migration; `cairn connect` exists so the new path is one command. |
| Concurrent agents race the first daemon start (port fight, double boot) | Two daemons observed, or connect flapping under simultaneous first dials | Single-flight lock file + port-bind failure as the natural singleton gate; existing stray sweeper (server.py:390-393) evicts orphans. |
| Stray daemons accumulate after crashes (no parent-death kill under HTTP) | `ps` shows orphaned `cairn serve` processes days later | Stray sweeper already built for the SSE/HTTP path; healthz + `cairn connect` make detection trivial; accepted residual risk of a stale pidfile. |
| Multiple daemons on drifting ports split the graph | Agents reporting different tool freshness from the same workspace | One documented default port; `cairn connect` refuses to start a second instance; sweeper enforces. |

**Kill criteria**: stop the project (or reinstate a compatibility
path) if, after pointing the user's real daily agents at the daemon,
(a) a majority of daily-use clients cannot hold a working
connection for a full day, or (b) orphaned-daemon or split-graph
incidents recur after the sweeper and single-flight lock ship —
both observable within the first two weeks of dogfooding.

---

*Next step: `/spec-idea mcp-streamable-http` — treat this doc as
intent input. The commit is the user's.*
