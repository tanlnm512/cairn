# Test Cases: mcp-streamable-http

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-11
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details — cases observe
only the `cairn` CLI (`serve`, `connect`, `init`, `config`,
`install-agents`, `doctor`), HTTP requests against the daemon, process and
port state, and configuration files under a scratch home.

## Conventions

- Auto pass-condition commands run from the repo root and are
  self-contained: each verifies the venv CLI up front, builds a throwaway
  one-file git-inited workspace store under `mktemp -d`, and isolates the
  run with a scratch `HOME` (client configs), scratch `CAIRN_HOME` (store
  registry and persisted key), `CAIRN_DB` (scratch store), and
  `CAIRN_WORKSPACE`; `CAIRN_MCP_API_KEY` is unset unless the case sets it.
- Cases that exercise the default port (TC-001, TC-007, TC-008, TC-009,
  TC-014, TC-016, TC-017) fail fast with a busy marker if something already
  listens on 9876; non-default cases use ports 9871–9878. Every case tears
  down what it started before asserting.
- MCP probes speak the client protocol over HTTP: initialize (carrying
  `Accept: application/json, text/event-stream`), pick up the session id
  from the response headers, send the initialized notification, then list
  or call tools. Responses may ride plain JSON or SSE `data:` framing;
  assertions tolerate either.
- Expected values come from independent truth: the 26-tool catalog count
  (spec's stated tool surface, re-counted in survey.md), the documented
  health-payload contract (`status`, `store_reachable`, `read_only`,
  `degradations`), and the key material the system itself emits in its MCP
  config output.
- The repo ships no property-based test library, so contract edges are
  pinned with fixed boundary examples; no test-only dependency is added.
- Most requirements are TODO at baseline: their cases are red today (no
  `connect` command, removed transports still accepted, keyless configs)
  and go green only when the promised behavior lands. TC-010, TC-019, and
  TC-020 are standing regression guards over already-green behavior and
  must stay green throughout.
- Auto commands finish well under the 120 s harness cap (typical run: a few
  seconds; TC-006 allows up to ~80 s for watcher pickup). Assertions exit
  nonzero on violation and end with an `..._OK` marker.

## TC-001 — Bare serve speaks HTTP only, at the single MCP path, with the full catalog

- **Story**: US1 · **Traces to**: FR-001, FR-005, NFR-005, AC1
- **Given** a cairn store built from one tiny source file and no daemon
  running on the default port.
- **When** `cairn serve` is launched with no transport flags at all, and a
  client establishes an MCP session on the local machine.
- **Then** the daemon answers on the default port over loopback only, the
  single MCP path serves the complete 26-tool catalog, the legacy SSE path
  is gone, the health probe answers without credentials, and the startup
  output carries a readiness line naming the served URL.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; ! lsof -nP -iTCP:9876 -sTCP:LISTEN | grep -q LISTEN || { echo PORT9876_BUSY; exit 1; }; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir -p "$d/ws/src" "$d/home"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/ws/src/m.py"; git -C "$d/ws" init -q; export HOME="$d/home" CAIRN_HOME="$d/cairn-home"; unset CAIRN_MCP_API_KEY; ( cd "$d/ws" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); A='Accept: application/json, text/event-stream'; I='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}'; CAIRN_DB="$d/store.db" "$c" serve >"$d/srv.log" 2>&1 & pid=$!; h=0; for i in $(seq 1 60); do curl -s -m 1 -o /dev/null "http://127.0.0.1:9876/healthz" && { h=1; break; }; sleep 0.5; done; hz=$(curl -s -m 2 -o /dev/null -w '%{http_code}' "http://127.0.0.1:9876/healthz"); sid=$(curl -s -m 5 -D - -o /dev/null -X POST "http://127.0.0.1:9876/mcp" -H 'Content-Type: application/json' -H "$A" -d "$I" | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); curl -s -m 5 -o /dev/null -X POST "http://127.0.0.1:9876/mcp" -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'; n=$(curl -s -m 10 -X POST "http://127.0.0.1:9876/mcp" -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | .venv/bin/python -c 'import json,sys; s=sys.stdin.read(); s="".join(l[5:] for l in s.splitlines() if l.startswith("data:")) or s; j=json.loads(s); print(len(j["result"]["tools"]))'); sse=$(curl -s -m 2 -o /dev/null -w '%{http_code}' "http://127.0.0.1:9876/sse"); bad=$(lsof -nP -iTCP:9876 -sTCP:LISTEN | awk 'NR>1{print $9}' | grep -vcE '127\.0\.0\.1|localhost|\[::1\]'); kill "$pid" 2>/dev/null; test "$h" = 1 && test "$hz" = 200 && test -n "$sid" && test "$n" = 26 && test "$sse" != 200 && test "$bad" = 0 && grep -q '127.0.0.1:9876' "$d/srv.log" && echo SURFACE_OK`

## TC-002 — No stdio MCP on the serve path (standing guard)

- **Story**: US1 · **Traces to**: FR-001
- **Given** the daemon launched with its standard input held open and fed
  one line of MCP-over-stdio protocol text.
- **When** the daemon's standard output and its HTTP listener are
  inspected.
- **Then** it never answers on standard input (no protocol reply appears
  on stdout) while the HTTP endpoint is alive and serving: stdio speaking
  is not compiled into the serve path. Fails if a stdio branch ever creeps
  back into `cairn serve`.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir -p "$d/ws/src" "$d/home"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/ws/src/m.py"; git -C "$d/ws" init -q; export HOME="$d/home" CAIRN_HOME="$d/cairn-home"; unset CAIRN_MCP_API_KEY; ( cd "$d/ws" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); I='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}'; mkfifo "$d/in"; exec 3<>"$d/in"; CAIRN_DB="$d/store.db" "$c" serve --port 9875 <"$d/in" >"$d/srv.log" 2>&1 & pid=$!; h=0; for i in $(seq 1 60); do curl -s -m 1 -o /dev/null "http://127.0.0.1:9875/healthz" && { h=1; break; }; sleep 0.5; done; printf '%s\n' "$I" >&3; sleep 3; alive=0; kill -0 "$pid" 2>/dev/null && alive=1; kill "$pid" 2>/dev/null; exec 3>&-; test "$alive" = 1 && test "$h" = 1 && ! grep -q '"jsonrpc"\|"result"' "$d/srv.log" && grep -q '127.0.0.1:9875' "$d/srv.log" && echo NO_STDIO_OK`

## TC-003 — Removed transport options are refused, naming the HTTP replacement

- **Story**: US1 · **Traces to**: FR-003, AC3
- **Given** the transport-selection flag with each of its removed values.
- **When** `cairn serve` is invoked with any of them (with standard input
  closed so a wrong success cannot hang).
- **Then** every invocation exits non-zero and prints a message naming the
  HTTP replacement, instead of starting any server.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir -p "$d/ws/src" "$d/home"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/ws/src/m.py"; git -C "$d/ws" init -q; export HOME="$d/home" CAIRN_HOME="$d/cairn-home"; unset CAIRN_MCP_API_KEY; ( cd "$d/ws" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); ok=1; for t in stdio sse http; do CAIRN_DB="$d/store.db" "$c" serve --transport "$t" </dev/null >"$d/ref_$t.log" 2>&1; rc=$?; { test "$rc" -ne 0 && grep -qiE 'http|url' "$d/ref_$t.log"; } || ok=0; done; test "$ok" = 1 && echo TRANSPORT_REFUSED_OK`

## TC-004 — Daemon is read-write by default; one agent's memory is instantly visible to another

- **Story**: US1 · **Traces to**: FR-002, NFR-003, AC2
- **Given** the daemon running with no read-mode flags, and two separate
  MCP client sessions connected to it.
- **When** agent A records a memory through a write tool, and agent B — on
  its own session — immediately searches for it.
- **Then** the write succeeds, agent B's recall returns that memory, the
  health payload reports read-write mode, and both agents were served by
  the same single process throughout.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir -p "$d/ws/src" "$d/home"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/ws/src/m.py"; git -C "$d/ws" init -q; export HOME="$d/home" CAIRN_HOME="$d/cairn-home"; unset CAIRN_MCP_API_KEY; ( cd "$d/ws" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); A='Accept: application/json, text/event-stream'; I='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}'; CAIRN_DB="$d/store.db" "$c" serve --port 9871 >"$d/srv.log" 2>&1 & pid=$!; h=0; for i in $(seq 1 60); do curl -s -m 1 -o /dev/null "http://127.0.0.1:9871/healthz" && { h=1; break; }; sleep 0.5; done; curl -s -m 5 "http://127.0.0.1:9871/healthz" > "$d/h.json"; sidA=$(curl -s -m 5 -D - -o /dev/null -X POST "http://127.0.0.1:9871/mcp" -H 'Content-Type: application/json' -H "$A" -d "$I" | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); curl -s -m 5 -o /dev/null -X POST "http://127.0.0.1:9871/mcp" -H "mcp-session-id: $sidA" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'; w=$(curl -s -m 15 -X POST "http://127.0.0.1:9871/mcp" -H "mcp-session-id: $sidA" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"record_memory","arguments":{"type":"decision","title":"qa shared decision probe","body":"How: share one daemon."}}}'); sidB=$(curl -s -m 5 -D - -o /dev/null -X POST "http://127.0.0.1:9871/mcp" -H 'Content-Type: application/json' -H "$A" -d "$I" | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); curl -s -m 5 -o /dev/null -X POST "http://127.0.0.1:9871/mcp" -H "mcp-session-id: $sidB" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'; r=$(curl -s -m 15 -X POST "http://127.0.0.1:9871/mcp" -H "mcp-session-id: $sidB" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"recall_memory","arguments":{"query":"shared decision probe"}}}'); np=$(lsof -nP -t -iTCP:9871 -sTCP:LISTEN | sort -u | wc -l | tr -d ' '); kill "$pid" 2>/dev/null; test "$h" = 1 && test -n "$sidA" && test -n "$sidB" && test "$sidA" != "$sidB" && printf '%s' "$w" | grep -q 'Recorded decision' && printf '%s' "$r" | grep -q 'qa shared decision probe' && .venv/bin/python -c 'import json,sys; j=json.load(open(sys.argv[1])); assert j["read_only"] is False, j' "$d/h.json" && test "$np" = 1 && echo SHARED_RW_OK`

## TC-005 — `--read-only` disables write tools

- **Story**: US1 · **Traces to**: FR-002
- **Given** the daemon started with the read-only opt-out.
- **When** the health payload is read and a write tool is invoked.
- **Then** the payload reports read-only mode and the write tool answers
  with a tool error instead of recording anything.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir -p "$d/ws/src" "$d/home"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/ws/src/m.py"; git -C "$d/ws" init -q; export HOME="$d/home" CAIRN_HOME="$d/cairn-home"; unset CAIRN_MCP_API_KEY; ( cd "$d/ws" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); A='Accept: application/json, text/event-stream'; I='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}'; CAIRN_DB="$d/store.db" "$c" serve --read-only --port 9871 >"$d/srv.log" 2>&1 & pid=$!; h=0; for i in $(seq 1 60); do curl -s -m 1 -o /dev/null "http://127.0.0.1:9871/healthz" && { h=1; break; }; sleep 0.5; done; curl -s -m 5 "http://127.0.0.1:9871/healthz" > "$d/h.json"; sid=$(curl -s -m 5 -D - -o /dev/null -X POST "http://127.0.0.1:9871/mcp" -H 'Content-Type: application/json' -H "$A" -d "$I" | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); curl -s -m 5 -o /dev/null -X POST "http://127.0.0.1:9871/mcp" -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'; w=$(curl -s -m 15 -X POST "http://127.0.0.1:9871/mcp" -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"record_memory","arguments":{"type":"decision","title":"qa ro probe","body":"must not persist"}}}'); kill "$pid" 2>/dev/null; test "$h" = 1 && test -n "$sid" && printf '%s' "$w" | grep -q '"isError":true' && .venv/bin/python -c 'import json,sys; j=json.load(open(sys.argv[1])); assert j["read_only"] is True, j' "$d/h.json" && echo READ_ONLY_OPT_OUT_OK`

## TC-006 — Default daemon's live watcher picks up a newly added file

- **Story**: US1 · **Traces to**: FR-002
- **Given** the read-write daemon already running and warmed over a built
  store, and a brand-new source file with a never-seen symbol created
  after startup.
- **When** a client asks for that symbol's definition, polled for up to
  eighty seconds.
- **Then** the live file watcher has indexed the new file and the symbol
  resolves to it, with no manual rebuild — the daemon stays current
  between tool calls.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir -p "$d/ws/src" "$d/home"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/ws/src/m.py"; git -C "$d/ws" init -q; export HOME="$d/home" CAIRN_HOME="$d/cairn-home" CAIRN_WORKSPACE="$d/ws"; unset CAIRN_MCP_API_KEY; ( cd "$d/ws" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); A='Accept: application/json, text/event-stream'; I='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}'; CAIRN_DB="$d/store.db" "$c" serve --port 9871 >"$d/srv.log" 2>&1 & pid=$!; h=0; for i in $(seq 1 60); do curl -s -m 1 -o /dev/null "http://127.0.0.1:9871/healthz" && { h=1; break; }; sleep 0.5; done; printf 'def zzq_watch9symbol(x):\n    return x\n' > "$d/ws/src/zz_watch9probe.py"; sid=$(curl -s -m 5 -D - -o /dev/null -X POST "http://127.0.0.1:9871/mcp" -H 'Content-Type: application/json' -H "$A" -d "$I" | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); curl -s -m 5 -o /dev/null -X POST "http://127.0.0.1:9871/mcp" -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'; found=0; for i in $(seq 1 40); do r=$(curl -s -m 10 -X POST "http://127.0.0.1:9871/mcp" -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"find_definition","arguments":{"name":"zzq_watch9symbol"}}}'); printf '%s' "$r" | grep -q 'zz_watch9probe' && { found=1; break; }; sleep 2; done; kill "$pid" 2>/dev/null; test "$h" = 1 && test "$found" = 1 && echo LIVE_WATCHER_OK`

## TC-007 — `connect` starts one detached daemon, then reports reuse

- **Story**: US2 · **Traces to**: FR-004, NFR-005, AC1
- **Given** a registered workspace with a built store and no daemon
  running.
- **When** `cairn connect` runs, and then runs again once the daemon is
  healthy.
- **Then** the first run starts exactly one daemon that stays up after the
  command exits (detached), emits the URL, and reports that it started;
  the second run emits the same URL, reports that it reused the running
  daemon, and leaves exactly one daemon process — the same one — on the
  port.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; ! lsof -nP -iTCP:9876 -sTCP:LISTEN | grep -q LISTEN || { echo PORT9876_BUSY; exit 1; }; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir -p "$d/ws/src" "$d/home"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/ws/src/m.py"; git -C "$d/ws" init -q; export HOME="$d/home" CAIRN_HOME="$d/cairn-home" CAIRN_DB="$d/store.db" CAIRN_WORKSPACE="$d/ws"; unset CAIRN_MCP_API_KEY; ( cd "$d/ws" && "$c" build >/dev/null 2>&1 ); "$c" connect >"$d/o1" 2>&1; rc1=$?; h=0; for i in $(seq 1 80); do curl -s -m 1 -o /dev/null "http://127.0.0.1:9876/healthz" && { h=1; break; }; sleep 0.5; done; pid1=$(lsof -nP -t -iTCP:9876 -sTCP:LISTEN | sort -u); n1=$(printf '%s\n' "$pid1" | grep -c .); "$c" connect >"$d/o2" 2>&1; rc2=$?; pid2=$(lsof -nP -t -iTCP:9876 -sTCP:LISTEN | sort -u); "$c" serve stop >/dev/null 2>&1; test "$rc1" = 0 && test "$rc2" = 0 && test "$h" = 1 && grep -q 'http://127.0.0.1:9876' "$d/o1" && grep -q 'http://127.0.0.1:9876' "$d/o2" && grep -qiE 'start' "$d/o1" && grep -qiE 'reus|already|existing|running' "$d/o2" && test "$n1" = 1 && test "$pid1" = "$pid2" && echo CONNECT_START_REUSE_OK`

## TC-008 — Concurrent connects converge to one daemon; a crashed daemon is replaceable

- **Story**: US2 · **Traces to**: FR-004, NFR-004, AC2
- **Given** a registered workspace with no daemon, two `cairn connect`
  invocations racing from a cold start, and later a hard-killed daemon.
- **When** both connects run simultaneously, and after convergence the
  daemon is killed with an uncatchable signal and connect runs once more.
- **Then** both racing invocations succeed, exactly one daemon exists
  afterwards (one listener pid on the port), and the post-crash connect
  brings up a fresh healthy daemon with no manual cleanup — the crashed
  instance left nothing that blocks replacement.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; ! lsof -nP -iTCP:9876 -sTCP:LISTEN | grep -q LISTEN || { echo PORT9876_BUSY; exit 1; }; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir -p "$d/ws/src" "$d/home"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/ws/src/m.py"; git -C "$d/ws" init -q; export HOME="$d/home" CAIRN_HOME="$d/cairn-home" CAIRN_DB="$d/store.db" CAIRN_WORKSPACE="$d/ws"; unset CAIRN_MCP_API_KEY; ( cd "$d/ws" && "$c" build >/dev/null 2>&1 ); ( "$c" connect >"$d/c1" 2>&1; echo $? > "$d/r1" ) & ( "$c" connect >"$d/c2" 2>&1; echo $? > "$d/r2" ) & wait; h=0; for i in $(seq 1 80); do curl -s -m 1 -o /dev/null "http://127.0.0.1:9876/healthz" && { h=1; break; }; sleep 0.5; done; p=$(lsof -nP -t -iTCP:9876 -sTCP:LISTEN | sort -u); n=$(printf '%s\n' "$p" | grep -c .); kill -9 $p 2>/dev/null; back=0; for i in $(seq 1 60); do lsof -nP -iTCP:9876 -sTCP:LISTEN | grep -q LISTEN && { back=1; break; }; sleep 0.5; done; "$c" connect >"$d/c3" 2>&1; rc3=$?; h2=0; for i in $(seq 1 80); do curl -s -m 1 -o /dev/null "http://127.0.0.1:9876/healthz" && { h2=1; break; }; sleep 0.5; done; "$c" serve stop >/dev/null 2>&1; test "$(cat "$d/r1")" = 0 && test "$(cat "$d/r2")" = 0 && test "$h" = 1 && test "$n" = 1 && test "$back" = 1 && test "$rc3" = 0 && test "$h2" = 1 && grep -qiE 'reus|already|existing|running' "$d/c3" && echo CONVERGE_CRASH_OK`

## TC-009 — Orphaned serve process holding the store is evicted by connect

- **Story**: US2 · **Traces to**: FR-008, AC3
- **Given** an orphaned (reparented) serve process holding the scratch
  store's database open on a side port, and no daemon on the main port.
- **When** `cairn connect` runs for that store.
- **Then** the orphan is evicted (it terminates within a bounded wait) and
  the new daemon comes up healthy on the main port — no duplicate serves
  survive the first dial.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; ! lsof -nP -iTCP:9876 -sTCP:LISTEN | grep -q LISTEN || { echo PORT9876_BUSY; exit 1; }; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir -p "$d/ws/src" "$d/home"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/ws/src/m.py"; git -C "$d/ws" init -q; export HOME="$d/home" CAIRN_HOME="$d/cairn-home" CAIRN_DB="$d/store.db" CAIRN_WORKSPACE="$d/ws"; unset CAIRN_MCP_API_KEY; ( cd "$d/ws" && "$c" build >/dev/null 2>&1 ); ( cd / && CAIRN_DB="$d/store.db" "$c" serve --port 9878 >"$d/orphan.log" 2>&1 & ); oh=0; for i in $(seq 1 60); do curl -s -m 1 -o /dev/null "http://127.0.0.1:9878/healthz" && { oh=1; break; }; sleep 0.5; done; opid=$(lsof -nP -t -iTCP:9878 -sTCP:LISTEN | head -1); "$c" connect >"$d/o" 2>&1; rc=$?; dead=0; for i in $(seq 1 60); do kill -0 "$opid" 2>/dev/null || { dead=1; break; }; sleep 0.5; done; h=0; for i in $(seq 1 60); do curl -s -m 1 -o /dev/null "http://127.0.0.1:9876/healthz" && { h=1; break; }; sleep 0.5; done; "$c" serve stop >/dev/null 2>&1; kill "$opid" 2>/dev/null; test "$oh" = 1 && test -n "$opid" && test "$rc" = 0 && test "$dead" = 1 && test "$h" = 1 && echo ORPHAN_EVICTED_OK`

## TC-010 — Stray-sweeper safety pins stay green (standing guard)

- **Story**: US2 · **Traces to**: FR-008
- **Given** the existing sweeper safety suite covering eviction of orphaned
  serve processes holding the store database.
- **When** the suite runs.
- **Then** it stays green untouched: extending the sweeper to the HTTP
  daemon changes neither the eviction discipline nor its safety rails.
  Standing regression guard over the already-partial behavior.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_stray_sweeper_safety.py -q`

## TC-011 — Bind policy preserved: loopback default, keyless loopback, keyed gate, pre-bind refusal

- **Story**: US4 · **Traces to**: FR-005, NFR-001, AC2
- **Given** three launch shapes: a keyed loopback daemon, an unkeyed
  loopback daemon, and an unkeyed non-loopback bind request; plus an
  attacker probing the keyed daemon with no credential and a wrong one.
- **When** the health probe is hit without credentials, MCP calls arrive
  keyless / wrongly keyed / correctly keyed, and the unkeyed wildcard
  launch is attempted.
- **Then** the health probe stays exempt from auth, the wrong credential
  is rejected with the unauthorized error and the right one admitted, the
  unkeyed loopback dial still succeeds (local-first workflow preserved),
  and the unkeyed non-loopback launch refuses to start — naming the API
  key — with nothing left listening. (Abuse case: credential-less
  attacker against a keyed daemon and an unkeyed network exposure
  attempt.)
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir -p "$d/ws/src" "$d/home"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/ws/src/m.py"; git -C "$d/ws" init -q; export HOME="$d/home" CAIRN_HOME="$d/cairn-home"; unset CAIRN_MCP_API_KEY; ( cd "$d/ws" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); A='Accept: application/json, text/event-stream'; I='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}'; CAIRN_MCP_API_KEY=qakey11 CAIRN_DB="$d/store.db" "$c" serve --port 9872 >"$d/k.log" 2>&1 & p1=$!; k=0; for i in $(seq 1 60); do curl -s -m 1 -o /dev/null "http://127.0.0.1:9872/healthz" && { k=1; break; }; sleep 0.5; done; hz=$(curl -s -m 2 -o /dev/null -w '%{http_code}' "http://127.0.0.1:9872/healthz"); wcode=$(curl -s -m 5 -o "$d/b1" -w '%{http_code}' -X POST "http://127.0.0.1:9872/mcp" -H 'Authorization: Bearer wrongcred9' -H 'Content-Type: application/json' -H "$A" -d "$I"); sid=$(curl -s -m 5 -D - -o /dev/null -X POST "http://127.0.0.1:9872/mcp" -H 'Authorization: Bearer qakey11' -H 'Content-Type: application/json' -H "$A" -d "$I" | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); kill "$p1" 2>/dev/null; CAIRN_DB="$d/store.db" "$c" serve --port 9873 >"$d/u.log" 2>&1 & p2=$!; u=0; for i in $(seq 1 60); do curl -s -m 1 -o /dev/null "http://127.0.0.1:9873/healthz" && { u=1; break; }; sleep 0.5; done; sid2=$(curl -s -m 5 -D - -o /dev/null -X POST "http://127.0.0.1:9873/mcp" -H 'Content-Type: application/json' -H "$A" -d "$I" | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); kill "$p2" 2>/dev/null; sleep 1; CAIRN_DB="$d/store.db" "$c" serve --host 0.0.0.0 --port 9874 >"$d/w.log" 2>&1; wrc=$?; test "$k" = 1 && test "$hz" = 200 && test "$wcode" = 401 && grep -q '"error":"unauthorized"' "$d/b1" && test -n "$sid" && test "$u" = 1 && test -n "$sid2" && test "$wrc" -ne 0 && grep -qiE 'api[ _-]?key' "$d/w.log" && ! curl -s -m 1 -o /dev/null "http://127.0.0.1:9874/healthz" 2>/dev/null && echo BIND_POLICY_OK`

## TC-012 — `init` generates an API key and the emitted MCP config is URL + key form

- **Story**: US3 · **Traces to**: FR-005
- **Given** a fresh workspace registered by `cairn init` under a scratch
  home.
- **When** the system's MCP configuration snippet is requested.
- **Then** initialization succeeds and the snippet is a remote/URL entry
  pointing at the default local daemon address carrying an embedded
  credential — no command-style server spawn — proving the key was
  generated and wired into generated configs at init time.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir -p "$d/ws/src" "$d/home"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/ws/src/m.py"; git -C "$d/ws" init -q; export HOME="$d/home" CAIRN_HOME="$d/cairn-home" CAIRN_WORKSPACE="$d/ws"; unset CAIRN_MCP_API_KEY; "$c" init --workspace "$d/ws" --no-build >"$d/init.log" 2>&1; rc=$?; "$c" config --mcp-config >"$d/mc.json" 2>/dev/null; test "$rc" = 0 && .venv/bin/python -c 'import json,re,sys; j=json.load(open(sys.argv[1])); e=j["mcpServers"]["cairn"]; assert "url" in e and "9876" in e["url"], e; s=json.dumps(e); assert re.search(r"(?i)bearer|authoriz|key", s), e; assert "command" not in e, e' "$d/mc.json" && echo INIT_KEY_OK`

## TC-013 — Generated key is stored user-only and never surfaces in daemon logs

- **Story**: US3 · **Traces to**: NFR-002
- **Given** a key generated by `cairn init` (read out of the emitted MCP
  config), the files the system persisted under its scratch home, a keyed
  daemon serving one deliberately bad-credential request, and an attacker
  who can read the daemon's log and the health probe. (Abuse case:
  secret-scraping of logs and stored files.)
- **When** every file containing the key material is permission-checked,
  and the daemon log and health payload are searched for the key.
- **Then** the key is persisted (at least one file carries it), every such
  file is readable by its owner only (no group or other bits), and neither
  the log — including the auth-failure line — nor the health payload ever
  contains the key value.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir -p "$d/ws/src" "$d/home"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/ws/src/m.py"; git -C "$d/ws" init -q; export HOME="$d/home" CAIRN_HOME="$d/cairn-home" CAIRN_WORKSPACE="$d/ws"; unset CAIRN_MCP_API_KEY; "$c" init --workspace "$d/ws" --no-build >/dev/null 2>&1; key=$("$c" config --mcp-config 2>/dev/null | .venv/bin/python -c 'import json,re,sys; j=json.load(sys.stdin); e=j["mcpServers"]["cairn"]; s=json.dumps(e); u=e.get("url",""); t=[x for x in re.findall(r"[A-Za-z0-9_\-]{16,}", s) if x not in u]; print(max(t, key=len) if t else "")'); test -n "$key" || exit 1; files=$(grep -rlF -- "$key" "$d/cairn-home" 2>/dev/null); bad=0; for f in $files; do m=$(stat -f %Lp "$f"); [ $((8#$m & 63)) -ne 0 ] && bad=1; done; ( cd "$d/ws" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY="$key" CAIRN_DB="$d/store.db" "$c" serve --port 9873 >"$d/srv.log" 2>&1 & pid=$!; k=0; for i in $(seq 1 60); do curl -s -m 1 -o /dev/null "http://127.0.0.1:9873/healthz" && { k=1; break; }; sleep 0.5; done; curl -s -m 5 -o /dev/null -X POST "http://127.0.0.1:9873/mcp" -H 'Authorization: Bearer wrongcred9' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'; curl -s -m 5 "http://127.0.0.1:9873/healthz" > "$d/h.txt"; kill "$pid" 2>/dev/null; test "$k" = 1 && test -n "$files" && test "$bad" = 0 && ! grep -qF -- "$key" "$d/srv.log" && ! grep -qF -- "$key" "$d/h.txt" && echo KEY_PRIVACY_OK`

## TC-014 — Installers write URL + key registrations for every supported client; verification tracks daemon health

- **Story**: US3 · **Traces to**: FR-006, AC1, AC2
- **Given** an initialized system (key generated), a healthy daemon on the
  default port, and a scratch home with no prior client configuration.
- **When** `cairn install-agents` runs for all clients, then the daemon is
  stopped and the installer runs again.
- **Then** the first run succeeds and at least eight distinct client
  configuration files each carry the daemon address together with the
  generated key embedded; the in-run registration verification passes
  while the daemon is healthy and, on the second run with the daemon
  down, reports the daemon as unreachable — verification probes the
  daemon's health, never a spawned process.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; ! lsof -nP -iTCP:9876 -sTCP:LISTEN | grep -q LISTEN || { echo PORT9876_BUSY; exit 1; }; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir -p "$d/ws/src" "$d/home"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/ws/src/m.py"; git -C "$d/ws" init -q; export HOME="$d/home" CAIRN_HOME="$d/cairn-home" CAIRN_WORKSPACE="$d/ws"; unset CAIRN_MCP_API_KEY; "$c" init --workspace "$d/ws" --no-build >/dev/null 2>&1; key=$("$c" config --mcp-config 2>/dev/null | .venv/bin/python -c 'import json,re,sys; j=json.load(sys.stdin); e=j["mcpServers"]["cairn"]; s=json.dumps(e); u=e.get("url",""); t=[x for x in re.findall(r"[A-Za-z0-9_\-]{16,}", s) if x not in u]; print(max(t, key=len) if t else "")'); test -n "$key" || exit 1; ( cd "$d/ws" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_DB="$d/store.db" "$c" serve >"$d/srv.log" 2>&1 & pid=$!; h=0; for i in $(seq 1 60); do curl -s -m 1 -o /dev/null "http://127.0.0.1:9876/healthz" && { h=1; break; }; sleep 0.5; done; ( cd "$d/ws" && "$c" install-agents --client all --scope global --yes >"$d/inst1" 2>&1 ); rc1=$?; urlf=$(grep -rl '127.0.0.1:9876' "$d/home" 2>/dev/null); cnt=$(printf '%s\n' "$urlf" | grep -c .); keyed=1; for f in $urlf; do grep -qF -- "$key" "$f" || keyed=0; done; kill "$pid" 2>/dev/null; sleep 1; ( cd "$d/ws" && "$c" install-agents --client all --scope global --yes --force >"$d/inst2" 2>&1 ); test "$h" = 1 && test "$rc1" = 0 && test "$cnt" -ge 7 && test "$keyed" = 1 && grep -q "Installing for: agy, claude, claude-desktop, cursor, droid, kilo, omp, opencode, zcode" "$d/inst1" && grep -qiE 'unreach|not running|unhealthy|down|unavailable' "$d/inst2" && echo INSTALL_URL_KEY_OK`

## TC-015 — Claude Desktop reported unsupported, nothing written for it

- **Story**: US3 · **Traces to**: FR-007, AC3
- **Given** a scratch home with no client configuration and an install run
  targeting only the client that supports no remote/URL MCP
  registration.
- **When** `cairn install-agents` runs for that client.
- **Then** the installer names the client and reports it as unsupported
  (skipped), and no configuration file anywhere in the scratch home
  contains a command-style cairn registration for it.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir -p "$d/ws/src" "$d/home"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/ws/src/m.py"; git -C "$d/ws" init -q; export HOME="$d/home" CAIRN_HOME="$d/cairn-home" CAIRN_WORKSPACE="$d/ws"; unset CAIRN_MCP_API_KEY; "$c" init --workspace "$d/ws" --no-build >/dev/null 2>&1; ( cd "$d/ws" && "$c" install-agents --client claude-desktop --scope global --yes >"$d/inst" 2>&1 ); rc=$?; cmdcfg=$(grep -rl --null '"command"' "$d/home" 2>/dev/null | xargs -0 -r grep -l '"cairn"' 2>/dev/null); test "$rc" = 0 && grep -qi 'claude-desktop' "$d/inst" && grep -qiE 'unsupport|not supported|skip' "$d/inst" && test -z "$cmdcfg" && echo DESKTOP_UNSUPPORTED_OK`

## TC-016 — Doctor flags a leftover stdio registration and `--fix` repoints it to URL + key

- **Story**: US4 · **Traces to**: FR-009, AC1
- **Given** an initialized system (key generated) and a client
  configuration file still carrying a cairn stdio registration from the
  pre-HTTP era.
- **When** `cairn doctor` runs, and then runs with `--fix`.
- **Then** the first run flags the stdio-era registration by client; the
  fix rewrites that registration into the URL + key form — daemon address
  and embedded key present, command-style spawn gone.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir -p "$d/ws/src" "$d/home" "$d/home/.cursor"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/ws/src/m.py"; git -C "$d/ws" init -q; export HOME="$d/home" CAIRN_HOME="$d/cairn-home" CAIRN_WORKSPACE="$d/ws"; unset CAIRN_MCP_API_KEY; "$c" init --no-build >/dev/null 2>&1; key=$("$c" config --mcp-config 2>/dev/null | .venv/bin/python -c 'import json,re,sys; j=json.load(sys.stdin); e=j["mcpServers"]["cairn"]; s=json.dumps(e); u=e.get("url",""); t=[x for x in re.findall(r"[A-Za-z0-9_\-]{16,}", s) if x not in u]; print(max(t, key=len) if t else "")'); test -n "$key" || exit 1; printf '{"mcpServers":{"cairn":{"command":"cairn","args":["serve"]}}}' > "$d/home/.cursor/mcp.json"; touch -t "$(date -v-5M +%Y%m%d%H%M.%S)" "$d/home/.cursor/mcp.json"; ( cd "$d/ws" && "$c" doctor >"$d/doc1" 2>&1 ); ( cd "$d/ws" && "$c" doctor --fix >"$d/doc2" 2>&1 ); rc=$?; f="$d/home/.cursor/mcp.json"; test "$rc" = 0 && grep -qi 'cursor' "$d/doc1" && grep -qiE 'stdio|stale|legacy|migrat|repoint|deprecat|flag' "$d/doc1" && grep -q '127.0.0.1:9876' "$f" && grep -qF -- "$key" "$f" && ! grep -q '"command"' "$f" && echo DOCTOR_REPOINT_OK`

## TC-017 — Daemon manager start/status/restart/stop drives the HTTP daemon

- **Story**: US2 · **Traces to**: FR-010
- **Given** the platform's native service mechanism (as used by the
  existing daemon manager) and no daemon currently loaded, with the
  manager's state redirected to a scratch home.
- **When** `cairn serve start`, `status`, `restart`, and `stop` run in
  sequence.
- **Then** start brings up a daemon that answers the health probe on the
  default port, status reports it running, restart leaves a healthy
  daemon again, and stop releases the port with status reporting it not
  running — the manager commands drive the HTTP daemon end to end.
  (Auto on macOS; the non-macOS fallback is TC-018.)
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; ! lsof -nP -iTCP:9876 -sTCP:LISTEN | grep -q LISTEN || { echo PORT9876_BUSY; exit 1; }; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir -p "$d/ws/src" "$d/home"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/ws/src/m.py"; git -C "$d/ws" init -q; export HOME="$d/home" CAIRN_HOME="$d/cairn-home" CAIRN_DB="$d/store.db" CAIRN_WORKSPACE="$d/ws"; unset CAIRN_MCP_API_KEY; ( cd "$d/ws" && "$c" build >/dev/null 2>&1 ); "$c" serve stop >/dev/null 2>&1 || true; "$c" serve start >"$d/st" 2>&1; rc1=$?; h1=0; for i in $(seq 1 80); do curl -s -m 1 -o /dev/null "http://127.0.0.1:9876/healthz" && { h1=1; break; }; sleep 0.5; done; "$c" serve status >"$d/s1" 2>&1; "$c" serve restart >"$d/rt" 2>&1; rc3=$?; h2=0; for i in $(seq 1 80); do curl -s -m 1 -o /dev/null "http://127.0.0.1:9876/healthz" && { h2=1; break; }; sleep 0.5; done; "$c" serve stop >"$d/sp" 2>&1; rc4=$?; gone=0; for i in $(seq 1 20); do lsof -nP -iTCP:9876 -sTCP:LISTEN | grep -q LISTEN || { gone=1; break; }; sleep 0.5; done; "$c" serve status >"$d/s2" 2>&1; p=$(lsof -nP -t -iTCP:9876 -sTCP:LISTEN); kill $p 2>/dev/null; test "$rc1" = 0 && test "$h1" = 1 && grep -qiE 'runn|active|loaded' "$d/s1" && test "$rc3" = 0 && test "$h2" = 1 && test "$rc4" = 0 && test "$gone" = 1 && grep -qiE 'not|stop|inactive' "$d/s2" && echo MANAGE_OK`

## TC-018 — Daemon manager daemonize fallback on non-macOS (manual)

- **Story**: US2 · **Traces to**: FR-010
- **Given** a Linux machine (no macOS service mechanism) with the cairn
  CLI installed and a registered workspace.
- **When** `cairn serve start`, `status`, `restart`, and `stop` run there,
  and the launching shell is exited between commands.
- **Then** start leaves a detached daemon that survives its launching
  shell and answers the health probe on the default port; status reports
  it running; restart replaces it with a healthy daemon; stop releases
  the port — the daemonize fallback provides the same lifecycle without
  the macOS mechanism.
- **Pass condition**: manual on any Linux box (the harness runs on macOS,
  where the fallback path cannot execute): run the TC-017 command
  sequence there, exit the shell that ran `serve start`, and observe the
  daemon still answers the health probe, status reports running,
  restart leaves a healthy daemon, and stop frees the port. Observable
  pass: human confirmation of that sequence on the Linux machine; the
  macOS half is TC-017's auto proof.

## TC-019 — No hand-rolled MCP transport (standing guard)

- **Story**: US1 · **Traces to**: FR-011
- **Given** the shipped source of the MCP serving package.
- **When** it is searched for hand-implemented JSON-RPC protocol
  machinery — the mark of a transport reimplementation instead of the
  pinned SDK's generated surface.
- **Then** none exists: the protocol surface remains entirely the SDK's.
  Fails the moment anyone hand-rolls transport code; the served protocol
  behavior itself (initialize round-trip, catalog) is carried by every
  other TC in this suite.
- **Pass condition**: `! grep -rq "jsonrpc" src/cairn/mcp_server --include='*.py' && echo SDK_SURFACE_OK`

## TC-020 — Auth/bind/healthz policy suites stay green (standing guard)

- **Story**: US4 · **Traces to**: NFR-001
- **Given** the existing test suites pinning the HTTP authentication,
  bind-class, and health-probe policy (bearer middleware with health
  exemption, constant-time credential check, loopback-vs-wildcard
  transport security, pre-bind refusal of unkeyed non-loopback binds).
- **When** the suites run.
- **Then** they stay green: the transport consolidation preserves the
  existing auth/bind policy unchanged. The suites' stdio-parity cases are
  expected to be reshaped by the removal; the auth/bind/healthz cases
  themselves must survive and pass.
- **Pass condition**: `CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_auth.py tests/test_http_transport.py -q`

## TC-021 — Warm daemon serves a second client's first call without rebooting

- **Story**: US1 · **Traces to**: NFR-003, AC1
- **Given** a daemon that has completed boot (built store, warmed) and is
  serving a first client, with the serving process id noted.
- **When** a brand-new second client establishes its own session and
  makes its first tool call, timed.
- **Then** the call succeeds within a small fraction of the harness cap,
  and the serving process is unchanged — no second server process, no
  per-client boot: the amortization is the point. The at-scale contract —
  two concurrent agents against one workspace with cold-boot-vs-warm-dial
  comparison — is a standing verify deferred to the execution phase's
  performance checkpoint, not an auto assertion here.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir -p "$d/ws/src" "$d/home"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/ws/src/m.py"; git -C "$d/ws" init -q; export HOME="$d/home" CAIRN_HOME="$d/cairn-home"; unset CAIRN_MCP_API_KEY; ( cd "$d/ws" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); A='Accept: application/json, text/event-stream'; I='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}'; CAIRN_DB="$d/store.db" "$c" serve --port 9871 >"$d/srv.log" 2>&1 & pid=$!; h=0; for i in $(seq 1 60); do curl -s -m 1 -o /dev/null "http://127.0.0.1:9871/healthz" && { h=1; break; }; sleep 0.5; done; p1=$(lsof -nP -t -iTCP:9871 -sTCP:LISTEN | sort -u); sid=$(curl -s -m 5 -D - -o /dev/null -X POST "http://127.0.0.1:9871/mcp" -H 'Content-Type: application/json' -H "$A" -d "$I" | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); curl -s -m 5 -o /dev/null -X POST "http://127.0.0.1:9871/mcp" -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'; el=$(curl -s -m 15 -o "$d/r.txt" -w '%{time_total}' -X POST "http://127.0.0.1:9871/mcp" -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"find_definition","arguments":{"name":"qadd"}}}'); p2=$(lsof -nP -t -iTCP:9871 -sTCP:LISTEN | sort -u); kill "$pid" 2>/dev/null; test "$h" = 1 && test -n "$sid" && grep -q '"result"' "$d/r.txt" && awk -v e="$el" 'BEGIN{exit !(e<10)}' && test "$p1" = "$p2" && echo WARM_SECOND_CLIENT_OK`

## Coverage matrix

<!-- Every FR and applicable NFR appears; `check.py` fails a requirement with no TC. -->

| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001 HTTP-only serve path, single MCP path, full catalog | TC-001, TC-002 | auto |
| FR-002 Read-write default (watcher, catch-up, writes); `--read-only` opt-out | TC-004, TC-005, TC-006 | auto |
| FR-003 Refuse stdio/SSE options, name HTTP replacement | TC-003 | auto |
| FR-004 `connect` starts one detached daemon / reuses healthy one, emits URL | TC-007, TC-008 | auto |
| FR-005 Loopback:9876 default, bind policy, init-generated key in configs, unkeyed loopback OK | TC-001, TC-011, TC-012 | auto |
| FR-006 All supported clients registered URL + key; verification probes healthz | TC-014 | auto |
| FR-007 No-URL clients (Claude Desktop) reported unsupported, nothing written | TC-015 | auto |
| FR-008 Stray sweeper evicts DB-holding orphans, extended to HTTP daemon | TC-009, TC-010 | auto |
| FR-009 Doctor flags stdio/SSE registrations; `--fix` repoints to URL+key | TC-016 | auto |
| FR-010 `serve start\|stop\|status\|restart` manage the HTTP daemon | TC-017, TC-018 | auto (macOS) + manual (fallback) |
| FR-011 SDK-generated protocol surface, no hand-rolled transport | TC-019 | auto (standing guard) |
| NFR-001 Security: existing auth/bind policy unchanged | TC-011, TC-020 | auto |
| NFR-002 Privacy: key user-only permissions, never logged | TC-013 | auto |
| NFR-003 Performance: warm daemon amortizes boot for later clients | TC-021 | auto (+ standing at-scale verify) |
| NFR-004 Reliability: concurrent connects converge; crashed daemon replaceable | TC-008 | auto |
| NFR-005 Observability: healthz + readiness line; connect reports started vs reused | TC-001, TC-007 | auto |
| NFR-006 Accessibility | — | excluded: not applicable per spec (no new user-facing UI surface) |

No requirement is `⚠ MISSING`: all 11 FRs and all 5 applicable NFRs have at
least one TC with an observable pass condition. TC-010, TC-019, and TC-020
are standing regression guards over already-green behavior (survey: FR-008
partial, FR-011 done, NFR-001 done) and must stay green throughout the
implementation; every other case is red at baseline and goes green only
when its requirement lands. TC-018 is manual by environment (the daemonize
fallback only executes on non-macOS; no Linux runner exists in this
harness); TC-017 carries the macOS half automatically. One spec smell
worth noting, not a gap: FR-011's "no hand-rolled transport" is a
standing negative guard (TC-019) — positive protocol behavior is pinned
transitively by every MCP-speaking TC in this suite.

Priorities: P1 × 14 (TC-001–TC-009, TC-011–TC-015), P2 × 7 (TC-010,
TC-016–TC-021).