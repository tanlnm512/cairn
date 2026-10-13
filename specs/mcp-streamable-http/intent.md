# Intent: mcp-streamable-http

**Status**: validated     <!-- validated: `check.py --intent-only`
                                exited 0 on 2026-10-11 after the
                                frontier emptied (6 answered, 4 named
                                assumptions, 0 open markers) -->
**Tier**: large           <!-- effort guess: 8 installer generators,
                                 daemon/lifecycle rework incl. Linux
                                 fallback, transport removal, doctor,
                                 docs and test sweep across ~8 files -->
**Created**: 2026-10-11
**Baseline**: 0.22.0 @ 31a66df
<!-- survey.md's baseline shape — version @ HEAD sha of the repo this
     intent was scouted against; the staleness diagnosis reads it
     unchanged, so it stays at the scouted HEAD -->

The intake artifact — WHAT the user wants and WHY, in their words,
before any docset file exists. spec-shape authors spec.md from here;
nothing in `specs/<name>/` beyond this file is intent's to write.

## Problem
"Review MCP provided by Cairn … replace completely stdio — Cairn mcp
should use only streamable http protocol." Today every agent that
wants cairn's 26 MCP tools spawns its own stdio `cairn serve`
process: each pays full boot (catch-up scan, model warmup, memory
decay), each runs a private file watcher against the same store, and
each dies with its parent — so knowledge recorded by one agent is
cold and stale to the next. The wanted end state (from
`brainstorms/mcp-streamable-http.md`, validated handoff): one
always-on loopback Streamable HTTP daemon serving every coding agent
over one URL, stdio and SSE removed, one warm shared graph.

## Audience
- **The multi-agent developer** (brainstorm persona 1): runs two or
  more coding agents (Kilo, Claude Code) on one workspace; needs one
  warm always-fresh graph with instantly shared memory, no per-agent
  boot tax, no strays. Failed by spawn-per-agent stdio: cold boots,
  N contending watchers/writers, no instance outlives its parent.
- **The single-agent local user** (brainstorm persona 2): needs
  zero-setup — point the agent at cairn and it works. Failed by
  having to remember to start a daemon; needs ensure-running so the
  first dial just works.

## Constraints
Scout-verified facts (baseline 0.22.0 @ 31a66df):
- SDK: `mcp>=0.9.0,<2.0.0` (pyproject.toml:46); installed 1.29.0
  speaks protocol ≤2025-11-25. Hard rule from the brainstorm: no
  hand-rolled transport code; MCP rev 2026-07-28 arrives via SDK
  bump only.
- HTTP path already exists: `_build_http_app` with bearer middleware,
  `/healthz`, bind policy, stateless flag
  (server.py:237-256,150-211; auth.py) and is proven equal to stdio
  by tests/test_http_transport.py (parity TC-001).
- Daemon manager already exists for SSE: `cairn serve
  start|stop|status|restart` via launchd plist
  (cli/serve.py:138-283, lifecycle.py:26-130), macOS-only pid
  tracking (`running_pid` returns None off-macOS); no pidfile, no
  single-flight lock — port-bind is the de-facto singleton gate.
  Port 9876 is the established default everywhere (lifecycle.py:14,
  Dockerfile:18, docs, CI).
- Stray sweeper exists with lsof DB cross-check
  (lifecycle.py:169-233); today installed only in the SSE branch.
- Writability: stdio defaults read-write with live watcher; SSE/HTTP
  default read-only, watcher and boot catch-up skipped
  (serve.py:125-126, server.py:324-387).
- Agent integration surface: 9 client installers with hardcoded
  stdio/SSE config generators (agent_install/_common.py:16,72-112;
  clients/*); Claude Desktop installer is stdio-only by design with
  no URL alternative (claude_desktop.py:28-32); default install
  transport today is SSE (agent_install/__init__.py:336-337);
  spawn-probe verification for stdio registrations
  (agent_install/__init__.py:247-322); doctor audits stdio
  registrations and repoints dead ones to stdio
  (system/doctor.py:711-856,1039).
- Dockerfile and CI are already HTTP-only (Dockerfile:18-25;
  ci.yml:410,423). Dashboard (port 8765) and VS Code extension are
  separate surfaces, unaffected.
- Conflict: specs/remote-mcp-oauth (draft, dormant, task.md unfilled)
  FR-005 mandates `cairn serve` stays byte-identical for stdio/SSE —
  this intent removes them; that spec must be superseded or amended.
- Constitution: no new runtime deps without a D-### decision (C-03);
  test-first (C-02); test isolation rules (C-04).

## Questions
- Q1 — Daemon writability, read-write default or read-only default?
  — answered: read-write by default (live watcher, memory writes,
  boot catch-up on); `--read-only` remains as opt-in for replicas.
  The daemon is the single writable server, so SQLite single-writer
  discipline is preserved.
- Q2 — Lifecycle surface, new `cairn connect` vs folding into
  `serve start`? — answered: both, split by role. New `cairn
  connect` (healthz-check → start detached if absent → emit/return
  the URL) is the agent/human entrypoint; `serve
  start|stop|status|restart` remains the daemon manager, switched
  from SSE to HTTP transport, with a Linux daemonize fallback
  (launchd is macOS-only today) and a lock or bind-race guard for
  concurrent first starts.
- Q3 — Loopback auth? — answered: `cairn init` auto-generates a
  `CAIRN_MCP_API_KEY`; every generated client config embeds it;
  unkeyed loopback dials remain permitted for manual testing
  (DNS-rebinding protection already enforces Origin checks).
- Q4 — Claude Desktop (stdio-only, no URL support)? — answered:
  dropped from the installer list with a documented "unsupported
  until remote-capable" note; the 8 remaining clients all support
  URL configs.
- Q5 — Breaking-change mechanics? — answered: clean break in
  0.23.0 — stdio/SSE transports and their flags removed, no
  deprecation window; `cairn doctor` detects surviving stdio
  registrations and repoints them to URL+key.
- Q6 — specs/remote-mcp-oauth FR-005 conflict? — answered: marked
  superseded by this spec for the transport base (INDEX.md
  amendment ships with this spec's delivery); the OAuth/remote
  work re-bases later on top of the HTTP-only daemon.
- Assumed (folded, not user-ruled): bare `cairn serve` runs the
  foreground HTTP daemon on the default port; the `--transport`
  flag is removed entirely; `sse_url`/`sse_responds` probes and the
  install spawn-probe become healthz probes; `cairn config
  --mcp-config` prints the URL+key shape.
<!-- each closed question becomes a bullet of its own: Qn, the
     question, then — answered: … | deferred (Scope out) | assumed:
     the named assumption -->

## FR seeds
- **FR-001**: The system shall expose cairn's MCP tools over a
  Streamable HTTP endpoint only, with no stdio or SSE transport.
- **FR-002**: The system shall run the daemon read-write by default
  (live watcher, memory writes, boot catch-up); WHEN `--read-only`
  is passed, the system shall skip watcher and writes.
- **FR-003**: WHEN no daemon is listening on the configured port,
  `cairn connect` shall start one detached single instance (guarded
  against concurrent first starts) and emit the URL.
- **FR-004**: The system shall write agent client configurations in
  URL + key form for all supported clients, embedding the
  auto-generated `CAIRN_MCP_API_KEY` (Claude Desktop excluded).
- **FR-005**: The system shall bind loopback by default on the
  stable port 9876 and enforce the existing key/bind policy
  off-loopback.
- **FR-006**: WHEN the stray sweeper observes orphaned serve
  processes holding the store DB open, the system shall evict them
  (existing lifecycle machinery, extended to the HTTP daemon).
- **FR-007**: WHEN `cairn doctor` finds surviving stdio
  registrations after the 0.23.0 break, it shall repoint them to
  the URL+key configuration.

## Handoff
Next: `/spec-gen mcp-streamable-http` — the docset (spec.md onward)
is authored there, never here. The Baseline above is what shape's
survey re-baselines against.
