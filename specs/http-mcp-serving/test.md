# Test Cases: http-mcp-serving

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-04
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details — cases observe
only the `cairn serve` CLI, HTTP requests, log output, and container
packaging.

## Conventions

- Auto pass-condition commands run from the repo root and are self-contained:
  each verifies the venv CLI up front, builds a throwaway one-file
  git-inited workspace store under `mktemp -d` (the indexer needs a git
  workspace; builds in under a second), starts the server on its own port
  (8371–8399, one per TC; TC-012 adds 8398 for its second bind class),
  probes it with `curl`, and tears the server down before asserting.
- MCP-over-HTTP probes POST JSON-RPC to `http://127.0.0.1:<port>/mcp` with
  `Accept: application/json, text/event-stream` (the SDK rejects a
  single-type Accept). A response rides plain JSON or SSE `data:` framing;
  probes accept either. A stateful server hands out an `mcp-session-id` on
  `initialize` and rejects session-less calls; `--stateless` accepts
  session-less calls directly (that difference is itself under test).
- Auto commands finish well under the 120 s harness cap (typical run: a few
  seconds). Assertions exit nonzero on violation and end with an `..._OK`
  marker; success is exit 0. Keyless/refusal cases clear
  `CAIRN_MCP_API_KEY` explicitly so an inherited value cannot flip the
  contract.
- The repo ships no property-based library, so the contract edges are pinned
  with fixed boundary examples; no test-only dependency is added.
- The HTTP surface has landed: every auto case is expected green today.
  TC-011, TC-017, and TC-018's stdio half are standing regression guards
  over already-green behavior.

## TC-001 — Tool surface over HTTP identical to stdio

- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** a cairn store built from one tiny source file, and an HTTP server
  started with `--transport http` and an API key.
- **When** the tool catalog is listed over stdio and over an authenticated
  Streamable HTTP session for the same store.
- **Then** both surfaces offer exactly the same tool set, proving the HTTP
  transport exposes the same tool registry without superseding stdio.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); A='Accept: application/json, text/event-stream'; I='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}'; CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8391 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8391/healthz" && break; sleep 0.5; done; sid=$(curl -s -m 5 -D - -o /dev/null -X POST "http://127.0.0.1:8391/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H "$A" -d "$I" | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); curl -s -m 5 -o /dev/null -X POST "http://127.0.0.1:8391/mcp" -H 'Authorization: Bearer qakey' -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'; curl -s -m 10 -X POST "http://127.0.0.1:8391/mcp" -H 'Authorization: Bearer qakey' -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | grep -o '"name":"[a-z_]*"' | sort -u > "$d/http.txt"; kill "$pid" 2>/dev/null; printf '%s\n%s\n%s\n' "$I" '{"jsonrpc":"2.0","method":"notifications/initialized"}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | "$c" serve --db "$d/store.db" 2>/dev/null | grep '"id":2[,}]' | grep -o '"name":"[a-z_]*"' | sort -u > "$d/stdio.txt"; diff "$d/stdio.txt" "$d/http.txt" && test "$(wc -l < "$d/http.txt" | tr -d ' ')" -ge 20 && echo PARITY_OK`

## TC-002 — Health probe answers without authentication

- **Story**: US1 · **Traces to**: FR-005, AC2
- **Given** a running HTTP server.
- **When** `/healthz` is probed with no credential at all.
- **Then** it returns an HTTP 200 whose body is machine-readable JSON naming
  exactly the documented concepts — status, store reachability, read-only
  mode, degradation count — and nothing else.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8392 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8392/healthz" && break; sleep 0.5; done; code=$(curl -s -o "$d/h.json" -w '%{http_code}' "http://127.0.0.1:8392/healthz"); kill "$pid" 2>/dev/null; test "$code" = 200 && .venv/bin/python -c 'import json,sys; j=json.load(open(sys.argv[1])); assert set(j)=={"status","store_reachable","read_only","degradations"} and j["store_reachable"] is True, j' "$d/h.json" && echo HEALTH_OK`

## TC-003 — Explicit host and port flags are honored

- **Story**: US1 · **Traces to**: FR-001
- **Given** `--host 127.0.0.1 --port 8393` passed with `--transport http`.
- **When** the server starts and the health probe is aimed at exactly that
  host and port.
- **Then** the server answers there, proving both flags are accepted and
  applied to the HTTP bind.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --host 127.0.0.1 --port 8393 --db "$d/store.db" >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8393/healthz" && break; sleep 0.5; done; code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8393/healthz"); kill "$pid" 2>/dev/null; test "$code" = 200 && echo FLAGS_OK`

## TC-004 — Missing credential rejected before any store access

- **Story**: US2 · **Traces to**: FR-003, FR-002, NFR-001, AC1
- **Given** a running, keyed HTTP server whose store file is removed from
  disk after startup, so any store access would produce a store error.
- **When** a request with no credential — and a request with a wrong
  credential — hits the MCP endpoint.
- **Then** both are answered with HTTP 401 carrying the
  `{"error":"unauthorized"}` body, never a store error: rejection
  demonstrably happens before the store is touched. (Abuse case:
  credential-less attacker.)
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8394 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8394/healthz" && break; sleep 0.5; done; mv "$d/store.db" "$d/store.db.gone"; c1=$(curl -s -m 5 -o "$d/b1" -w '%{http_code}' -X POST "http://127.0.0.1:8394/mcp" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'); c2=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:8394/mcp" -H 'Authorization: Bearer wrongcred9' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'); kill "$pid" 2>/dev/null; test "$c1" = 401 && test "$c2" = 401 && grep -q '"error":"unauthorized"' "$d/b1" && echo AUTH_FIRST_OK`

## TC-005 — Wrong credential rejected, no tool execution

- **Story**: US2 · **Traces to**: FR-002, FR-003, AC1
- **Given** a healthy, keyed HTTP server.
- **When** a request carrying an incorrect bearer key asks for the tool
  catalog.
- **Then** the answer is a 401 rejection with no tool listing in the body
  (empty-input boundary of the credential check).
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8381 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8381/healthz" && break; sleep 0.5; done; code=$(curl -s -m 5 -o "$d/b" -w '%{http_code}' -X POST "http://127.0.0.1:8381/mcp" -H 'Authorization: Bearer totallywrong' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'); kill "$pid" 2>/dev/null; test "$code" = 401 && ! grep -q '"result"' "$d/b" && echo WRONG_CRED_REJECTED`

## TC-006 — API-key flag takes precedence over the environment

- **Story**: US2 · **Traces to**: FR-002, AC2
- **Given** the server started with key A in the environment and key B on
  the `--api-key` flag.
- **When** the endpoint is probed with B and then with A.
- **Then** B's initialize succeeds (200) and A's is rejected with 401:
  the flag demonstrably wins.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); I='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}'; CAIRN_MCP_API_KEY=envkeyvalue "$c" serve --transport http --api-key flagkeyvalue --db "$d/store.db" --port 8382 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8382/healthz" && break; sleep 0.5; done; cflag=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:8382/mcp" -H 'Authorization: Bearer flagkeyvalue' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$I"); cenv=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:8382/mcp" -H 'Authorization: Bearer envkeyvalue' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$I"); kill "$pid" 2>/dev/null; test "$cflag" = 200 && test "$cenv" = 401 && echo PRECEDENCE_OK`

## TC-007 — Environment-sourced key activates the gate

- **Story**: US2 · **Traces to**: FR-002, AC2
- **Given** the server started with only `CAIRN_MCP_API_KEY` in the
  environment (no flag) on a loopback bind.
- **When** the endpoint is probed with the environment key and then with no
  key.
- **Then** the environment key's initialize succeeds (200) and the keyless
  request is rejected with 401: the env source alone configures the
  credential.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); I='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}'; CAIRN_MCP_API_KEY=envonlykey "$c" serve --transport http --db "$d/store.db" --port 8383 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8383/healthz" && break; sleep 0.5; done; cgood=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:8383/mcp" -H 'Authorization: Bearer envonlykey' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$I"); cnone=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:8383/mcp" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$I"); kill "$pid" 2>/dev/null; test "$cgood" = 200 && test "$cnone" = 401 && echo ENV_KEY_OK`

## TC-008 — Keyless operation allowed on loopback only

- **Story**: US3 · **Traces to**: FR-004, AC1
- **Given** the HTTP server started on loopback with no key from any source.
- **When** the server is probed and the MCP endpoint is called without any
  credential.
- **Then** the server starts and the keyless initialize succeeds with a
  session id (200), preserving the local-first workflow; the non-loopback
  refusal is covered by TC-009.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); I='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}'; env -u CAIRN_MCP_API_KEY "$c" serve --transport http --db "$d/store.db" --port 8384 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8384/healthz" && break; sleep 0.5; done; hcode=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:8384/healthz"); sid=$(curl -s -m 5 -D - -o /dev/null -X POST "http://127.0.0.1:8384/mcp" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$I" | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); kill "$pid" 2>/dev/null; test "$hcode" = 200 && test -n "$sid" && echo KEYLESS_LOOPBACK_OK`

## TC-009 — Non-loopback bind without a key refuses to start

- **Story**: US3 · **Traces to**: FR-002, FR-004, AC2
- **Given** no API key from any source and `--host 0.0.0.0` requested.
- **When** the server is launched.
- **Then** it exits nonzero with a clear error naming the API key, and
  nothing listens on the port (CLI refuse-to-start case).
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); env -u CAIRN_MCP_API_KEY "$c" serve --transport http --host 0.0.0.0 --db "$d/store.db" --port 8385 >"$d/ref.log" 2>&1; rc=$?; test "$rc" -ne 0 && grep -qiE 'api[ _-]?key' "$d/ref.log" && ! curl -s -m 1 -o /dev/null "http://127.0.0.1:8385/healthz" 2>/dev/null && echo REFUSED_OK`

## TC-010 — Secrets never surface in logs or refusal output

- **Story**: US2 · **Traces to**: NFR-001
- **Given** a server started with a distinctive flag key, a client presenting
  a distinctive wrong key, and a bait value planted in the process
  environment (a non-credential variable) for the keyless non-loopback
  launch.
- **When** the server log (including the auth-failure path) and the
  refuse-to-start error output are inspected.
- **Then** neither the flag key, the presented wrong key, nor the bait
  environment value appears anywhere — while the refusal still names the
  missing API key by concept and the log carries the rejection line.
  (Abuse case: log/argv scraping for secrets.)
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); "$c" serve --transport http --api-key QASECRET9Z --db "$d/store.db" --port 8386 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8386/healthz" && break; sleep 0.5; done; curl -s -m 5 -o /dev/null -X POST "http://127.0.0.1:8386/mcp" -H 'Authorization: Bearer wrongcred9' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'; kill "$pid" 2>/dev/null; sleep 1; env -u CAIRN_MCP_API_KEY QABAIT=BAITENV7Z "$c" serve --transport http --host 0.0.0.0 --db "$d/store.db" --port 8386 >"$d/ref2.log" 2>&1; rc=$?; test "$rc" -ne 0 && ! grep -q QASECRET9Z "$d/srv.log" && ! grep -q wrongcred9 "$d/srv.log" && ! grep -q BAITENV7Z "$d/ref2.log" && grep -qiE 'api[ _-]?key' "$d/ref2.log" && grep -qi 'unauthorized' "$d/srv.log" && echo SECRETS_OK`

## TC-011 — Default bind is loopback only (standing guard)

- **Story**: US3 · **Traces to**: FR-004, AC1
- **Given** the HTTP server started with no host override.
- **When** the listening sockets for its port are inspected.
- **Then** every listener is on a loopback address only, preserving today's
  loopback-first default (regression guard over the already-partial state).
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); env -u CAIRN_MCP_API_KEY "$c" serve --transport http --db "$d/store.db" --port 8387 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8387/healthz" && break; sleep 0.5; done; a=$(lsof -nP -iTCP:8387 -sTCP:LISTEN | awk 'NR>1{print $9}'); kill "$pid" 2>/dev/null; test -n "$a" && printf '%s\n' "$a" | grep -qE '127\.0\.0\.1|localhost|\[::1\]' && ! printf '%s\n' "$a" | grep -qvE '127\.0\.0\.1|localhost|\[::1\]' && echo LOOPBACK_ONLY_OK`

## TC-012 — Non-loopback bind with a key serves; Host-header protections match the bind class

- **Story**: US3 · **Traces to**: FR-004, NFR-001
- **Given** a server on a wildcard (`0.0.0.0`) bind with a configured key,
  and a keyed loopback server.
- **When** an authenticated initialize arrives from the documented host
  form, an attacker replays it keyless with a spoofed foreign Host header,
  and a valid-key initialize carries a spoofed foreign Host header at the
  loopback bind.
- **Then** the wildcard bind serves the legitimate initialize with a session
  id, the keyless spoofed replay is rejected 401 at the auth gate, and the
  loopback bind's transport-security guard refuses the spoofed-Host request
  outright (no session result): protections appropriate to each bind class
  are active before dispatch. (Abuse case: DNS-rebinding.)
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); A='Accept: application/json, text/event-stream'; I='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}'; CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --host 0.0.0.0 --db "$d/store.db" --port 8388 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8388/healthz" && break; sleep 0.5; done; sidw=$(curl -s -m 5 -D - -o /dev/null -X POST "http://127.0.0.1:8388/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H "$A" -d "$I" | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); crep=$(curl -s -m 5 -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:8388/mcp" -H 'Host: evil.example' -H 'Content-Type: application/json' -H "$A" -d "$I"); kill "$pid" 2>/dev/null; sleep 1; CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8398 >"$d/srv2.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8398/healthz" && break; sleep 0.5; done; sid2=$(curl -s -m 5 -D - -o /dev/null -X POST "http://127.0.0.1:8398/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H "$A" -d "$I" | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); cevil=$(curl -s -m 5 -o "$d/be" -w '%{http_code}' -X POST "http://127.0.0.1:8398/mcp" -H 'Host: evil.example' -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H "$A" -d "$I"); kill "$pid" 2>/dev/null; test -n "$sidw" && test "$crep" = 401 && test -n "$sid2" && test "$cevil" != 200 && grep -q 'Invalid Host' "$d/be" && echo HOST_GUARD_OK`

## TC-013 — Stateless mode serves session-less calls independently

- **Story**: US4 · **Traces to**: FR-006, AC1
- **Given** the server started with `--stateless`.
- **When** two sequential tool-catalog requests arrive, each carrying no
  session state of any kind.
- **Then** both succeed on their own: no server-side session affinity is
  required, so requests can load-balance across replicas.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --stateless --db "$d/store.db" --port 8389 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8389/healthz" && break; sleep 0.5; done; curl -s -m 10 -X POST "http://127.0.0.1:8389/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' > "$d/r1.json"; curl -s -m 10 -X POST "http://127.0.0.1:8389/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' > "$d/r2.json"; kill "$pid" 2>/dev/null; grep -q '"result"' "$d/r1.json" && grep -q '"result"' "$d/r2.json" && echo STATELESS_OK`

## TC-014 — Default mode still requires session establishment

- **Story**: US4 · **Traces to**: FR-006
- **Given** the server started without `--stateless`.
- **When** a tool-catalog request arrives with no prior session.
- **Then** it is rejected with the session-class client error (400, "Missing
  session ID"), proving the stateless flag actually changes session
  behavior rather than being a no-op (boundary guard for the mode's delta).
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8371 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8371/healthz" && break; sleep 0.5; done; code=$(curl -s -m 5 -o "$d/b" -w '%{http_code}' -X POST "http://127.0.0.1:8371/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'); kill "$pid" 2>/dev/null; test "$code" = 400 && grep -q 'Missing session ID' "$d/b" && echo SESSION_ENFORCED_OK`

## TC-015 — Image recipe and deployment docs match the packaging contract

- **Story**: US5 · **Traces to**: FR-008, AC1
- **Given** the shipped Dockerfile and the deployment documentation.
- **When** the recipe is inspected against the promised properties and the
  deployment document is searched for the three serving modes.
- **Then** the image recipe declares a slim base, a non-root user, the
  `/data` home/volume, and an HTTP-serving entrypoint, and
  `docs/deployment.md` covers local, networked, and container serving.
  (Bounded stand-in for the full container run; see TC-016.)
- **Pass condition**: `test -f Dockerfile && grep -qiE '^FROM .*(slim|alpine)' Dockerfile && grep -qE '^USER [a-z]' Dockerfile && ! grep -qiE '^USER (root|0)( |$)' Dockerfile && grep -qE 'CAIRN_HOME.{0,4}/data' Dockerfile && grep -qE '^VOLUME .*/data' Dockerfile && grep -qE '"--transport",[[:space:]]*"http"' Dockerfile && f=docs/deployment.md && test -f "$f" && grep -qi docker "$f" && grep -qiE 'networked|0\.0\.0\.0|non-loopback' "$f" && grep -qiE 'stdio|local' "$f" && echo PACKAGING_OK`

## TC-016 — Container acceptance owned by the CI container job (manual)

- **Story**: US5 · **Traces to**: FR-008, AC1
- **Given** the shipped Dockerfile, the CI pipeline, and the auto HTTP
  proofs.
- **When** the CI `container` job builds the image on every push and runs
  its smoke steps (keyless wildcard entrypoint refuses with exit 1; keyed
  entrypoint answers `/healthz`), beside the static recipe checks (TC-015)
  and the live HTTP-surface equivalents (the auto cases over the same
  auth, refusal, and health contracts).
- **Then** container acceptance is the green CI `container` job plus the
  green live HTTP equivalents: the Dockerfile is exercised automatically
  without a local container runtime, and D-004's refuse-to-start behavior
  is proven inside the image. The full in-container authenticated
  tool-call walkthrough remains a human step for the first
  runtime-capable environment.
- **Standing verify** (manual — a cold `docker build` installs dependencies
  and exceeds the 120 s harness cap, so this runs outside the auto
  proofs, on any container-capable machine): `docker build -t cairn-http . && docker run -d --name cairn-qa -p 8399:9876 -v "$PWD/.qa-store:/data" -e CAIRN_MCP_API_KEY=qakey cairn-http` then probe `curl -s http://127.0.0.1:8399/healthz` (HTTP 200, `status: ok`) and one authenticated initialize/tools/call against the mapped port, then `docker rm -f cairn-qa`. Observable pass: the green CI `container` job for the same recipe, plus locally a green health probe and a successful authenticated call in the container log.

## TC-017 — stdio default path unchanged (standing guard)

- **Story**: US6 · **Traces to**: FR-001, AC1
- **Given** no HTTP flags anywhere.
- **When** the existing server-robustness pins run against the current
  stdio dispatch and boot guards.
- **Then** they stay green untouched: the new transport supersedes nothing.
  Standing regression guard — fails if the default path ever drifts.
- **Pass condition**: `.venv/bin/python -m pytest tests/test_server_robustness.py -q`

## TC-018 — `--port` dispatches SSE, no port stays stdio

- **Story**: US6 · **Traces to**: FR-001, AC1
- **Given** a store built from one tiny source file.
- **When** `cairn serve --port` is launched, and separately plain
  `cairn serve` is launched with its stdin held open.
- **Then** the port-annotated run answers HTTP on exactly that port within a
  short timeout, while the plain run listens on no TCP port at all: the
  `--port`-means-network dispatch and the stdio default are both pinned end
  to end, not assumed from a unit-level probe.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); "$c" serve --db "$d/store.db" --port 8390 >"$d/sse.log" 2>&1 & spid=$!; answered=0; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8390/" && answered=1 && break; sleep 0.5; done; kill "$spid" 2>/dev/null; mkfifo "$d/in"; ( sleep 20 >"$d/in" ) & hold=$!; "$c" serve --db "$d/store.db" <"$d/in" >/dev/null 2>&1 & tpid=$!; sleep 2; alive=0; kill -0 "$tpid" 2>/dev/null && alive=1; listeners=$(lsof -nP -iTCP -sTCP:LISTEN -a -p "$tpid" 2>/dev/null | awk 'NR>1' | wc -l | tr -d ' '); kill "$tpid" 2>/dev/null; kill "$hold" 2>/dev/null; test "$answered" = 1 && test "$alive" = 1 && test "$listeners" = 0 && echo SSE_DISPATCH_OK`

## TC-019 — Health probe leaks nothing beyond its payload

- **Story**: US1 · **Traces to**: NFR-002, FR-005
- **Given** a running HTTP server whose store path carries a distinctive
  scratch name.
- **When** `/healthz` is fetched unauthenticated and inspected.
- **Then** the payload carries exactly the four documented keys and never
  the store name, scratch directory, hostname, or the richer telemetry
  counters the internal status data exposes. (Abuse case: reconnaissance
  via the public probe.)
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/qaprivacy7z.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/qaprivacy7z.db" --port 8372 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8372/healthz" && break; sleep 0.5; done; curl -s -m 5 "http://127.0.0.1:8372/healthz" > "$d/h.json"; kill "$pid" 2>/dev/null; .venv/bin/python -c 'import json,sys,re; j=json.load(open(sys.argv[1])); s=json.dumps(j); assert set(j)=={"status","store_reachable","read_only","degradations"}, j; assert not re.search(r"qaprivacy7z|24h|error_rate|tool_calls|last_build|pending_sync|workspace|db_path|hostname|/users/", s, re.I), s' "$d/h.json" && ! grep -q "$(basename "$d")" "$d/h.json" && echo PRIVACY_OK`

## TC-020 — Bounded smoke: repeated HTTP tool calls complete

- **Story**: US1 · **Traces to**: NFR-003
- **Given** a stateless HTTP server over one store (session handling excluded
  from the contract by design).
- **When** twenty sequential authenticated tool calls are issued and timed.
- **Then** every call succeeds and the whole batch finishes far inside the
  harness cap: a bounded smoke proving the HTTP surface completes repeated
  tool calls against the live store. The at-scale contract — per-call
  latency of the SSE and HTTP transports over the same store — is a Standing
  verify deferred to the execution phase's performance checkpoint, not an
  auto assertion here.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --stateless --db "$d/store.db" --port 8373 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8373/healthz" && break; sleep 0.5; done; t0=$(date +%s); ok=1; for i in $(seq 1 20); do curl -sf -m 15 -X POST "http://127.0.0.1:8373/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' >/dev/null 2>&1 || ok=0; done; el=$(( $(date +%s) - t0 )); kill "$pid" 2>/dev/null; test "$ok" = 1 && test "$el" -lt 60 && echo "PERF_OK elapsed=${el}s"`

## TC-021 — Store unavailable reported unhealthy on the probe

- **Story**: US1 · **Traces to**: NFR-004, FR-005
- **Given** a running HTTP server whose store file is removed from disk
  after startup (mid-run outage boundary).
- **When** `/healthz` is probed.
- **Then** the machine-readable payload reports the unhealthy/unreachable
  state instead of claiming healthy.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8374 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8374/healthz" && break; sleep 0.5; done; mv "$d/store.db" "$d/store.db.gone"; curl -s -m 5 "http://127.0.0.1:8374/healthz" > "$d/h2.json"; kill "$pid" 2>/dev/null; grep -q '"status":"unhealthy"' "$d/h2.json" && grep -q '"store_reachable":false' "$d/h2.json" && echo UNHEALTHY_REPORTED_OK`

## TC-022 — Tool calls during a store outage answer cleanly, no crash

- **Story**: US1 · **Traces to**: NFR-004
- **Given** the same mid-run outage as TC-021 and an authenticated client
  with an established session.
- **When** a store-reading tool is invoked.
- **Then** the server still answers with a structured, parseable JSON-RPC
  result marked as a tool error, the process stays alive, and the health
  endpoint still responds afterward: a clean error path, never a crash or
  hang.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); A='Accept: application/json, text/event-stream'; I='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}'; CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8375 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8375/healthz" && break; sleep 0.5; done; sid=$(curl -s -m 5 -D - -o /dev/null -X POST "http://127.0.0.1:8375/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H "$A" -d "$I" | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); curl -s -m 5 -o /dev/null -X POST "http://127.0.0.1:8375/mcp" -H 'Authorization: Bearer qakey' -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'; mv "$d/store.db" "$d/store.db.gone"; r=$(curl -s -m 10 -X POST "http://127.0.0.1:8375/mcp" -H 'Authorization: Bearer qakey' -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search_symbols","arguments":{"pattern":"qadd"}}}'); hcode=$(curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:8375/healthz"); alive=0; kill -0 "$pid" 2>/dev/null && alive=1; kill "$pid" 2>/dev/null; printf '%s\n' "$r" | grep '^data:' | sed 's/^data: //' | .venv/bin/python -c 'import json,sys; m=json.load(sys.stdin); assert m["result"]["isError"] is True and m["result"]["content"][0]["text"], m'; test "$alive" = 1 && test "$hcode" = 200 && echo CLEAN_ERROR_OK`

## TC-023 — Lifecycle milestones logged, auth failures at WARN, no request noise

- **Story**: US1 · **Traces to**: NFR-005
- **Given** a keyed HTTP server logging to a captured file, one deliberately
  bad-credential request, and one legitimate request.
- **When** the server starts, serves both requests, and is shut down.
- **Then** the log contains a bind/listen milestone and a WARN-level
  unauthorized-request line while carrying zero per-request access-log
  lines, and the server terminates promptly on SIGTERM (clean shutdown, no
  hang).
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); I='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}'; env -u CAIRN_LOG_LEVEL CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8376 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8376/healthz" && break; sleep 0.5; done; curl -s -m 5 -o /dev/null -X POST "http://127.0.0.1:8376/mcp" -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'; curl -s -m 5 -o /dev/null -X POST "http://127.0.0.1:8376/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d "$I"; kill "$pid" 2>/dev/null; gone=0; for i in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || { gone=1; break; }; sleep 0.25; done; grep -qiE 'listen|bind|serving|started|running on' "$d/srv.log" && grep -i warn "$d/srv.log" | grep -qiE 'unauthorized|auth' && test "$(grep -cE '"(GET|POST) /' "$d/srv.log")" = 0 && test "$gone" = 1 && echo OBSERVABILITY_OK`

## TC-024 — Multiple concurrent agents share one HTTP server

- **Story**: US1 · **Traces to**: FR-001
- **Given** a stateless HTTP server over one store (the shared-service
  deployment from the spec's business value).
- **When** four authenticated clients call tools at the same time.
- **Then** all four get successful results against the shared store
  (concurrent-access boundary of the multi-agent promise).
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --stateless --db "$d/store.db" --port 8377 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8377/healthz" && break; sleep 0.5; done; rm -f "$d"/r?.json; for i in 1 2 3 4; do curl -s -m 15 -X POST "http://127.0.0.1:8377/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' > "$d/r$i.json" & done; n=0; for t in $(seq 1 40); do n=0; for i in 1 2 3 4; do grep -q '"result"' "$d/r$i.json" 2>/dev/null && n=$((n+1)); done; test "$n" = 4 && break; sleep 0.5; done; kill "$pid" 2>/dev/null; test "$n" = 4 && echo CONCURRENT_OK`

## TC-025 — HTTP defaults to read-only mode

- **Story**: US1 · **Traces to**: FR-007
- **Given** an HTTP server started with no read-only/read-write flags.
- **When** the health payload is read.
- **Then** it reports `read_only: true` — HTTP defaults to the read-only
  tri-state exactly like the SSE daemon, with the mode visible in the
  machine-readable health contract (the explicit override is TC-026's
  contract; the existing read-only enforcement pin
  `tests/test_core_smoke.py::test_read_only_mode_blocks_db_writes` stays
  green as the tri-state machinery's standing guard).
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8378 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8378/healthz" && break; sleep 0.5; done; curl -s -m 5 "http://127.0.0.1:8378/healthz" > "$d/h.json"; kill "$pid" 2>/dev/null; .venv/bin/python -c 'import json,sys; j=json.load(open(sys.argv[1])); assert j["read_only"] is True and j["status"]=="ok" and j["store_reachable"] is True, j' "$d/h.json" && echo READ_ONLY_DEFAULT_OK`

## TC-026 — Explicit read-write flag overrides the HTTP default

- **Story**: US1 · **Traces to**: FR-007
- **Given** an HTTP server started with the existing explicit read-write
  flag and a scratch knowledge store.
- **When** the health payload is read and a write-purpose knowledge tool is
  invoked, then the new document is searched for.
- **Then** the payload reports `read_only: false`, the write succeeds, and
  the subsequent read finds it: the explicit flag overrides the HTTP
  read-only default.
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); A='Accept: application/json, text/event-stream'; I='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}'; CAIRN_MCP_API_KEY=qakey CAIRN_KNOWLEDGE="$d/knowledge" "$c" serve --transport http --read-write --db "$d/store.db" --port 8379 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8379/healthz" && break; sleep 0.5; done; sid=$(curl -s -m 5 -D - -o /dev/null -X POST "http://127.0.0.1:8379/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H "$A" -d "$I" | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); curl -s -m 5 -o /dev/null -X POST "http://127.0.0.1:8379/mcp" -H 'Authorization: Bearer qakey' -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'; curl -s -m 10 -X POST "http://127.0.0.1:8379/mcp" -H 'Authorization: Bearer qakey' -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"knowledge_add","arguments":{"title":"qa rw probe","body":"qa rw probe body","doc_type":"decision"}}}' > "$d/w.json"; curl -s -m 20 -X POST "http://127.0.0.1:8379/mcp" -H 'Authorization: Bearer qakey' -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"knowledge_search","arguments":{"query":"qa rw probe"}}}' > "$d/s.json"; curl -s -m 5 "http://127.0.0.1:8379/healthz" > "$d/h.json"; kill "$pid" 2>/dev/null; grep -q '"isError":false' "$d/w.json" && grep -q 'qa rw probe' "$d/s.json" && grep -q '"read_only":false' "$d/h.json" && echo READ_WRITE_OVERRIDE_OK`

## TC-027 — Tool answer payload identical over stdio and HTTP

- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** a cairn store built from one tiny git-inited source file, and a
  stdio server and an authenticated Streamable HTTP server over that same
  store.
- **When** one read-only tool (`find_definition` on the known symbol) is
  invoked over a stdio JSON-RPC session and over an HTTP session.
- **Then** both return a success result carrying equivalent payload content
  once the transport envelope is normalized: the HTTP surface answers with
  the same tool output the stdio surface answers with, not merely the same
  tool catalog (catalog parity is TC-001's contract).
- **Pass condition**: `test -x .venv/bin/cairn || exit 1; d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); A='Accept: application/json, text/event-stream'; I='{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}'; CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8380 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 40); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8380/healthz" && break; sleep 0.5; done; sid=$(curl -s -m 5 -D - -o /dev/null -X POST "http://127.0.0.1:8380/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H "$A" -d "$I" | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); curl -s -m 5 -o /dev/null -X POST "http://127.0.0.1:8380/mcp" -H 'Authorization: Bearer qakey' -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'; curl -s -m 10 -X POST "http://127.0.0.1:8380/mcp" -H 'Authorization: Bearer qakey' -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H "$A" -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"find_definition","arguments":{"name":"qadd"}}}' | grep '^data:' | sed 's/^data: //' > "$d/http.json"; kill "$pid" 2>/dev/null; printf '%s\n%s\n%s\n' "$I" '{"jsonrpc":"2.0","method":"notifications/initialized"}' '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"find_definition","arguments":{"name":"qadd"}}}' | "$c" serve --db "$d/store.db" 2>/dev/null | grep '"id":3[,}]' | tail -1 > "$d/stdio.json"; .venv/bin/python -c 'import json,sys; a=json.load(open(sys.argv[1]))["result"]; b=json.load(open(sys.argv[2]))["result"]; assert not a.get("isError") and not b.get("isError"), (a,b); t=a["content"][0]["text"].strip(); u=b["content"][0]["text"].strip(); assert "m.py" in t and "qadd" in t, t; assert t==u, (t,u)' "$d/stdio.json" "$d/http.json" && echo ANSWER_PARITY_OK`

## Coverage matrix

<!-- Every FR and applicable NFR appears; `check.py` fails a requirement with no TC. -->

| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001 HTTP transport, same registry, stdio/SSE preserved | TC-001, TC-003, TC-017, TC-018, TC-024, TC-027 | auto |
| FR-002 Bearer key sourcing, precedence, refuse-to-start | TC-004, TC-005, TC-006, TC-007, TC-009, TC-010 | auto |
| FR-003 Auth rejection before store access | TC-004, TC-005 | auto |
| FR-004 Loopback default, keyless only loopback, Host-header protections | TC-008, TC-009, TC-011, TC-012 | auto |
| FR-005 /healthz machine-readable payload, no auth | TC-002, TC-019, TC-021 | auto |
| FR-006 Stateless mode | TC-013, TC-014 | auto |
| FR-007 Read-only default, explicit override | TC-025, TC-026 | auto |
| FR-008 Dockerfile + deployment docs | TC-015, TC-016 | auto + manual |
| NFR-001 Security: gate + Host-header before dispatch, no secret leaks | TC-004, TC-010, TC-012 | auto |
| NFR-002 Privacy: no leaks beyond documented payload | TC-019 | auto |
| NFR-003 Performance: bounded HTTP call smoke; at-scale SSE-vs-HTTP latency comparison is a Standing verify deferred to the execution phase's perf checkpoint | TC-020 | auto (+ standing verify) |
| NFR-004 Reliability: unhealthy probe, clean tool errors | TC-021, TC-022 | auto |
| NFR-005 Observability: milestones, WARN auth failures, no noise | TC-023 | auto |
| NFR-006 Accessibility | — | excluded: not applicable per spec (no UI surface touched) |

No requirement is `⚠ MISSING`: all 8 FRs and all 5 applicable NFRs have at
least one TC with an observable pass condition. TC-011, TC-017, and
TC-018's stdio half are standing regression guards over already-green
behavior; TC-018 pins the live `--port`-dispatch and stdio-default contract
end to end. TC-016 is manual by design (D-017): the CI `container` job owns
the cold-build proof on every push and the auto TCs carry the live
HTTP-surface equivalents; only the in-container authenticated tool-call
walkthrough stays a human step (container build time exceeds the 120 s auto
cap).

Priorities: P1 × 19 (TC-001–TC-012, TC-017, TC-018, TC-021, TC-022, TC-025,
TC-026, TC-027), P2 × 8 (TC-013–TC-016, TC-019, TC-020, TC-023, TC-024).
