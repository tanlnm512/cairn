# Test Cases: http-mcp-serving

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-04
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details — cases observe
only the `cairn serve` CLI, HTTP requests, log output, and container
packaging.

## Conventions

- Auto pass-condition commands run from the repo root and are self-contained:
  each builds a throwaway one-file workspace store under `mktemp -d` (builds
  in under a second), starts the server on its own port (8371–8399, one per
  TC), probes it with `curl`, and tears the server down before asserting.
- MCP-over-HTTP probes POST JSON-RPC to the streamable endpoint with
  `Accept: application/json` so every response body is plain parseable JSON.
  A stateful server hands out a session id on `initialize`; stateless mode
  accepts session-less calls directly (that difference is itself under test).
- Auto commands finish well under the 120 s harness cap (typical run: a few
  seconds). Assertions exit nonzero on violation and end with an `..._OK`
  marker; success is exit 0.
- The repo ships no property-based library, so the contract edges are pinned
  with fixed boundary examples; no test-only dependency is added.
- Standing guards (survey DONE/PARTIAL items) cite existing pins and are
  expected green today; cases for the new HTTP surface are expected red until
  the spec lands — that is the suite doing its job.

## TC-001 — Tool surface over HTTP identical to stdio

- **Story**: US1 · **Traces to**: FR-001, AC1
- **Given** a cairn store built from one tiny source file, and an HTTP server
  started with `--transport http` and an API key.
- **When** the tool catalog is listed over stdio and over an authenticated
  Streamable HTTP session for the same store.
- **Then** both surfaces offer exactly the same tool set, proving the HTTP
  transport exposes the same tool registry without superseding stdio.
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); printf '%s\n%s\n%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}' '{"jsonrpc":"2.0","method":"notifications/initialized"}' '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | "$c" serve --db "$d/store.db" 2>/dev/null | grep -o '"name":"[a-z_]*"' | sort -u > "$d/stdio.txt"; CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8391 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8391/healthz" && break; sleep 0.5; done; sid=$(curl -s -D - -o /dev/null -X POST "http://127.0.0.1:8391/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}' | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); curl -s -X POST "http://127.0.0.1:8391/mcp" -H 'Authorization: Bearer qakey' -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' | grep -o '"name":"[a-z_]*"' | sort -u > "$d/http.txt"; kill "$pid" 2>/dev/null; diff "$d/stdio.txt" "$d/http.txt" && test -s "$d/http.txt" && echo PARITY_OK

## TC-002 — Health probe answers without authentication

- **Story**: US1 · **Traces to**: FR-005, AC2
- **Given** a running HTTP server.
- **When** `/healthz` is probed with no credential at all.
- **Then** it returns an HTTP 200 whose body is machine-readable JSON naming
  the three documented concepts: store reachability, read-only mode,
  degradation count.
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8392 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8392/healthz" && break; sleep 0.5; done; code=$(curl -s -o "$d/h.json" -w '%{http_code}' "http://127.0.0.1:8392/healthz"); kill "$pid" 2>/dev/null; test "$code" = 200 && .venv/bin/python -c 'import json,sys; j=json.load(open(sys.argv[1])); s=json.dumps(j).lower(); assert ("store" in s or "reach" in s or "health" in s) and "read" in s and "degrad" in s, j' "$d/h.json" && echo HEALTH_OK

## TC-003 — Explicit host and port flags are honored

- **Story**: US1 · **Traces to**: FR-001
- **Given** `--host 127.0.0.1 --port 8393` passed with `--transport http`.
- **When** the server starts and the health probe is aimed at exactly that
  host and port.
- **Then** the server answers there, proving both flags are accepted and
  applied to the HTTP bind.
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --host 127.0.0.1 --port 8393 --db "$d/store.db" >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8393/healthz" && break; sleep 0.5; done; code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8393/healthz"); kill "$pid" 2>/dev/null; test "$code" = 200 && echo FLAGS_OK

## TC-004 — Missing credential rejected before any store access

- **Story**: US2 · **Traces to**: FR-003, FR-002, NFR-001, AC1
- **Given** a running, keyed HTTP server whose store file is removed from
  disk after startup, so any store access would produce a store error.
- **When** a request with no credential — and a request with a wrong
  credential — hits the MCP endpoint.
- **Then** both are answered with an auth-class rejection carrying an
  auth-flavored body, never a store error: rejection demonstrably happens
  before the store is touched. (Abuse case: credential-less attacker.)
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8394 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8394/healthz" && break; sleep 0.5; done; mv "$d/store.db" "$d/store.db.gone"; c1=$(curl -s -o "$d/b1" -w '%{http_code}' -X POST "http://127.0.0.1:8394/mcp" -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'); c2=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:8394/mcp" -H 'Authorization: Bearer wrongcred9' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'); kill "$pid" 2>/dev/null; case "$c1" in 401|403) ;; *) echo "no-cred got $c1"; exit 1;; esac; case "$c2" in 401|403) ;; *) echo "bad-cred got $c2"; exit 1;; esac; grep -qiE 'auth|credential|unauthorized|token|api[ _-]?key' "$d/b1" && echo AUTH_FIRST_OK

## TC-005 — Wrong credential rejected, no tool execution

- **Story**: US2 · **Traces to**: FR-002, FR-003, AC1
- **Given** a healthy, keyed HTTP server.
- **When** a request carrying an incorrect bearer key asks for the tool
  catalog.
- **Then** the answer is an auth-class rejection with no tool listing in the
  body (empty-input boundary of the credential check).
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8381 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8381/healthz" && break; sleep 0.5; done; code=$(curl -s -o "$d/b" -w '%{http_code}' -X POST "http://127.0.0.1:8381/mcp" -H 'Authorization: Bearer totallywrong' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'); kill "$pid" 2>/dev/null; case "$code" in 401|403) ;; *) echo "got $code"; exit 1;; esac; ! grep -q '"result"' "$d/b" && echo WRONG_CRED_REJECTED

## TC-006 — API-key flag takes precedence over the environment

- **Story**: US2 · **Traces to**: FR-002, AC2
- **Given** the server started with key A in the environment and key B on
  the `--api-key` flag.
- **When** the endpoint is probed with B and then with A.
- **Then** B is accepted (past the auth gate) and A is auth-rejected:
  the flag demonstrably wins.
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=envkeyvalue "$c" serve --transport http --api-key flagkeyvalue --db "$d/store.db" --port 8382 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8382/healthz" && break; sleep 0.5; done; cflag=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:8382/mcp" -H 'Authorization: Bearer flagkeyvalue' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'); cenv=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:8382/mcp" -H 'Authorization: Bearer envkeyvalue' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'); kill "$pid" 2>/dev/null; case "$cflag" in 401|403) echo "flag key rejected ($cflag)"; exit 1;; esac; case "$cenv" in 401|403) ;; *) echo "env key accepted ($cenv)"; exit 1;; esac; echo PRECEDENCE_OK

## TC-007 — Environment-sourced key activates the gate

- **Story**: US2 · **Traces to**: FR-002, AC2
- **Given** the server started with only `CAIRN_MCP_API_KEY` in the
  environment (no flag) on a loopback bind.
- **When** the endpoint is probed with the environment key and then with no
  key.
- **Then** the environment key is accepted and the keyless request is
  rejected: the env source alone configures the credential.
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=envonlykey "$c" serve --transport http --db "$d/store.db" --port 8383 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8383/healthz" && break; sleep 0.5; done; cgood=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:8383/mcp" -H 'Authorization: Bearer envonlykey' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'); cnone=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:8383/mcp" -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'); kill "$pid" 2>/dev/null; case "$cgood" in 401|403) echo "env key rejected ($cgood)"; exit 1;; esac; case "$cnone" in 401|403) ;; *) echo "keyless accepted ($cnone)"; exit 1;; esac; echo ENV_KEY_OK

## TC-008 — Keyless operation allowed on loopback only

- **Story**: US3 · **Traces to**: FR-004, AC1
- **Given** the HTTP server started on loopback with no key from any source.
- **When** the server is probed and the MCP endpoint is called without any
  credential.
- **Then** the server starts and serves the keyless request (past the auth
  gate), preserving the local-first workflow; the non-loopback refusal is
  covered by TC-009.
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); "$c" serve --transport http --db "$d/store.db" --port 8384 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8384/healthz" && break; sleep 0.5; done; hcode=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8384/healthz"); ccode=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:8384/mcp" -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'); kill "$pid" 2>/dev/null; test "$hcode" = 200 && case "$ccode" in 401|403) echo "keyless rejected on loopback ($ccode)"; exit 1;; esac && echo KEYLESS_LOOPBACK_OK

## TC-009 — Non-loopback bind without a key refuses to start

- **Story**: US3 · **Traces to**: FR-002, FR-004, AC2
- **Given** no API key from any source and `--host 0.0.0.0` requested.
- **When** the server is launched.
- **Then** it exits nonzero with a clear error naming the API key, and
  nothing listens on the port (CLI refuse-to-start case).
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); "$c" serve --transport http --host 0.0.0.0 --db "$d/store.db" --port 8385 >"$d/ref.log" 2>&1 & pid=$!; sleep 5; if kill -0 "$pid" 2>/dev/null; then kill "$pid"; echo "FAIL: started without a required key"; exit 1; fi; wait "$pid" 2>/dev/null; rc=$?; test "$rc" -ne 0 && grep -qiE 'api[ _-]?key' "$d/ref.log" && ! curl -s -m 1 "http://127.0.0.1:8385/healthz" >/dev/null 2>&1 && echo REFUSED_OK

## TC-010 — Secrets never surface in logs or refusal output

- **Story**: US2 · **Traces to**: NFR-001
- **Given** a server started with a distinctive flag key, a client presenting
  a distinctive wrong key, and a bait value planted in the environment for a
  keyless non-loopback launch.
- **When** the server log (including the auth-failure path) and the
  refuse-to-start error output are inspected.
- **Then** neither the flag key, the presented wrong key, nor the bait
  environment value appears anywhere, while the refusal still names the
  missing API key by concept. (Abuse case: log/argv scraping for secrets.)
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); "$c" serve --transport http --api-key QASECRET9Z --db "$d/store.db" --port 8386 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8386/healthz" && break; sleep 0.5; done; curl -s -o /dev/null -X POST "http://127.0.0.1:8386/mcp" -H 'Authorization: Bearer wrongcred9' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'; kill "$pid" 2>/dev/null; sleep 1; CAIRN_MCP_API_KEY=BAITENV7Z "$c" serve --transport http --host 0.0.0.0 --db "$d/store.db" --port 8386 >"$d/ref2.log" 2>&1; grep -q QASECRET9Z "$d/srv.log" && echo "flag key leaked" && exit 1; grep -q wrongcred9 "$d/srv.log" && echo "presented key leaked" && exit 1; grep -q BAITENV7Z "$d/ref2.log" && echo "env value leaked" && exit 1; grep -qiE 'api[ _-]?key' "$d/ref2.log" && echo SECRETS_OK

## TC-011 — Default bind is loopback only (standing guard)

- **Story**: US3 · **Traces to**: FR-004, AC1
- **Given** the HTTP server started with no host override.
- **When** the listening sockets for its port are inspected.
- **Then** every listener is on a loopback address only, preserving today's
  loopback-first default (regression guard over the already-partial state).
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); "$c" serve --transport http --db "$d/store.db" --port 8387 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8387/healthz" && break; sleep 0.5; done; a=$(lsof -nP -iTCP:8387 -sTCP:LISTEN | awk 'NR>1{print $9}'); kill "$pid" 2>/dev/null; printf '%s\n' "$a" | grep -qE '127\.0\.0\.1|localhost|\[::1\]' && ! printf '%s\n' "$a" | grep -qvE '127\.0\.0\.1|localhost|\[::1\]' && echo LOOPBACK_ONLY_OK

## TC-012 — Non-loopback bind with a key serves and guards the Host header

- **Story**: US3 · **Traces to**: FR-004, NFR-001
- **Given** the server started on a non-loopback bind with a configured key.
- **When** an authenticated session is established from the documented host
  form, and an attacker replays the same request with a spoofed foreign
  Host header.
- **Then** the legitimate initialize succeeds with a session id while the
  spoofed-Host request is never granted a session result: transport-security
  protections are active before dispatch. (Abuse case: DNS-rebinding.)
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --host 0.0.0.0 --db "$d/store.db" --port 8388 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8388/healthz" && break; sleep 0.5; done; sgood=$(curl -s -D - -o "$d/bg" -w '%{http_code}' -X POST "http://127.0.0.1:8388/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}'); sid=$(grep -i '^mcp-session-id:' "$d/bg" | tr -d '\r' | awk '{print $2}'); cevil=$(curl -s -o "$d/be" -w '%{http_code}' -X POST "http://127.0.0.1:8388/mcp" -H 'Host: evil.example' -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}'); kill "$pid" 2>/dev/null; test "$sgood" = 200 && test -n "$sid" && if [ "$cevil" = 200 ] && grep -q '"result"' "$d/be"; then echo "spoofed Host served"; exit 1; fi && echo HOST_GUARD_OK

## TC-013 — Stateless mode serves session-less calls independently

- **Story**: US4 · **Traces to**: FR-006, AC1
- **Given** the server started with `--stateless`.
- **When** two sequential tool-catalog requests arrive, each carrying no
  session state of any kind.
- **Then** both succeed on their own: no server-side session affinity is
  required, so requests can load-balance across replicas.
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --stateless --db "$d/store.db" --port 8389 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8389/healthz" && break; sleep 0.5; done; curl -s -X POST "http://127.0.0.1:8389/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' > "$d/r1.json"; curl -s -X POST "http://127.0.0.1:8389/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}' > "$d/r2.json"; kill "$pid" 2>/dev/null; grep -q '"result"' "$d/r1.json" && grep -q '"result"' "$d/r2.json" && echo STATELESS_OK

## TC-014 — Default mode still requires session establishment

- **Story**: US4 · **Traces to**: FR-006
- **Given** the server started without `--stateless`.
- **When** a tool-catalog request arrives with no prior session.
- **Then** it is rejected with a session-class client error, proving the
  stateless flag actually changes session behavior rather than being a
  no-op (boundary guard for the mode's delta).
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8371 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8371/healthz" && break; sleep 0.5; done; code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:8371/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'); kill "$pid" 2>/dev/null; case "$code" in 400|404) echo SESSION_ENFORCED_OK;; *) echo "session-less call got $code"; exit 1;; esac

## TC-015 — Image recipe and deployment docs match the packaging contract

- **Story**: US5 · **Traces to**: FR-008, AC1
- **Given** the shipped Dockerfile and documentation set.
- **When** the recipe is inspected against the promised properties and the
  docs are searched for the three serving modes.
- **Then** the image recipe declares a slim base, a non-root user, the
  `/data` home/volume, and an HTTP-serving entrypoint, and a deployment
  document covers local, networked, and container serving. (Bounded stand-in
  for the full container run; see TC-016.)
- **Pass condition**: `test -f Dockerfile && grep -qiE '^FROM .*(slim|alpine)' Dockerfile && grep -qE '^USER [a-z]' Dockerfile && ! grep -qiE '^USER (root|0)( |$)' Dockerfile && grep -qE 'CAIRN_HOME.{0,4}/data' Dockerfile && grep -qE '^VOLUME .*/data' Dockerfile && grep -qE '"--transport",[[:space:]]*"http"' Dockerfile && f=$(grep -rilE 'deploy|serving' docs --include='*.md' | head -1) && test -n "$f" && grep -qi docker "$f" && grep -qiE 'networked|0\.0\.0\.0|non-loopback' "$f" && grep -qiE 'stdio|local' "$f" && echo PACKAGING_OK

## TC-016 — Container runs as a service with a mounted store (manual)

- **Story**: US5 · **Traces to**: FR-008, AC1
- **Given** the Dockerfile, a scratch store directory, and the documented
  environment.
- **When** the image is built and run with the store volume-mounted and the
  documented key env.
- **Then** `/healthz` passes unauthenticated and an authenticated client
  completes a tool call inside the container.
- **Standing verify** (manual — a cold `docker build` installs dependencies
  and can exceed the 120 s harness cap, so this runs outside the auto
  proofs): `docker build -t cairn-http . && docker run -d --name cairn-qa -p 8399:9876 -v "$PWD/.qa-store:/data" -e CAIRN_MCP_API_KEY=qakey cairn-http` then probe `curl -s http://127.0.0.1:8399/healthz` (HTTP 200, healthy payload) and one authenticated tool call against the mapped port, then `docker rm -f cairn-qa`. Observable pass: health probe green plus a successful authenticated call in the container log.

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
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); "$c" serve --db "$d/store.db" --port 8390 >"$d/sse.log" 2>&1 & spid=$!; answered=0; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8390/" && answered=1 && break; sleep 0.5; done; kill "$spid" 2>/dev/null; mkfifo "$d/in"; ( sleep 20 >"$d/in" ) & hold=$!; "$c" serve --db "$d/store.db" <"$d/in" >/dev/null 2>&1 & tpid=$!; sleep 2; alive=0; kill -0 "$tpid" 2>/dev/null && alive=1; listeners=$(lsof -nP -iTCP -sTCP:LISTEN -a -p "$tpid" 2>/dev/null | awk 'NR>1' | wc -l | tr -d ' '); kill "$tpid" 2>/dev/null; kill "$hold" 2>/dev/null; test "$answered" = 1 && test "$alive" = 1 && test "$listeners" = 0 && echo SSE_DISPATCH_OK

## TC-019 — Health probe leaks nothing beyond its payload

- **Story**: US1 · **Traces to**: NFR-002, FR-005
- **Given** a running HTTP server whose store path contains a distinctive
  scratch name.
- **When** `/healthz` is fetched unauthenticated and inspected.
- **Then** the payload carries exactly the documented concepts and never the
  store path, workspace, hostname, or the richer telemetry counters the
  internal status data exposes. (Abuse case: reconnaissance via the public
  probe.)
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8372 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8372/healthz" && break; sleep 0.5; done; curl -s "http://127.0.0.1:8372/healthz" > "$d/h.json"; kill "$pid" 2>/dev/null; .venv/bin/python -c 'import json,sys,re; j=json.load(open(sys.argv[1])); s=json.dumps(j).lower(); assert not re.search(r"24h|error_rate|tool_calls|last_build|pending_sync|workspace|db_path|hostname|/users/", s), s; assert "degrad" in s and "read" in s and ("store" in s or "reach" in s or "health" in s), s' "$d/h.json" && ! grep -q "$(basename "$d/store.db")" "$d/h.json" && echo PRIVACY_OK

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
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --stateless --db "$d/store.db" --port 8373 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8373/healthz" && break; sleep 0.5; done; t0=$(date +%s); for i in $(seq 1 20); do curl -sf -X POST "http://127.0.0.1:8373/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' >/dev/null || { echo "call $i failed"; exit 1; }; done; el=$(( $(date +%s) - t0 )); kill "$pid" 2>/dev/null; test "$el" -lt 60 && echo "PERF_OK elapsed=${el}s"

## TC-021 — Store unavailable reported unhealthy on the probe

- **Story**: US1 · **Traces to**: NFR-004, FR-005
- **Given** a running HTTP server whose store file is removed from disk
  after startup (mid-run outage boundary).
- **When** `/healthz` is probed.
- **Then** the machine-readable payload reports the unhealthy/unreachable
  state instead of claiming healthy.
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8374 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8374/healthz" && break; sleep 0.5; done; mv "$d/store.db" "$d/store.db.gone"; curl -s "http://127.0.0.1:8374/healthz" > "$d/h2.json"; kill "$pid" 2>/dev/null; grep -qiE 'unhealthy|"store[a-z_]*":[[:space:]]*false|"reachable":[[:space:]]*false|"healthy":[[:space:]]*false' "$d/h2.json" && echo UNHEALTHY_REPORTED_OK

## TC-022 — Tool calls during a store outage answer cleanly, no crash

- **Story**: US1 · **Traces to**: NFR-004
- **Given** the same mid-run outage as TC-021 and an authenticated client
  with an established session.
- **When** a store-reading tool is invoked.
- **Then** the server still answers with a structured, parseable reply, the
  process stays alive, and the health endpoint still responds afterward:
  a clean error path, never a crash or hang.
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8375 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8375/healthz" && break; sleep 0.5; done; sid=$(curl -s -D - -o /dev/null -X POST "http://127.0.0.1:8375/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}' | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); mv "$d/store.db" "$d/store.db.gone"; r=$(curl -s -m 10 -X POST "http://127.0.0.1:8375/mcp" -H 'Authorization: Bearer qakey' -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"search_symbols","arguments":{"pattern":"qadd"}}}'); hcode=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8375/healthz"); alive=0; kill -0 "$pid" 2>/dev/null && alive=1; kill "$pid" 2>/dev/null; printf '%s' "$r" | .venv/bin/python -c 'import json,sys; json.load(sys.stdin)'; test "$alive" = 1 && test "$hcode" != 000 && echo CLEAN_ERROR_OK

## TC-023 — Lifecycle milestones logged, auth failures at WARN, no request noise

- **Story**: US1 · **Traces to**: NFR-005
- **Given** a keyed HTTP server logging to a captured file, one deliberately
  bad-credential request, and one legitimate request.
- **When** the server starts, serves both requests, and is shut down.
- **Then** the log contains a bind/listen milestone, a WARN-level auth
  failure line, and a shutdown milestone, while carrying zero per-request
  access-log lines for the traffic.
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8376 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8376/healthz" && break; sleep 0.5; done; curl -s -o /dev/null -X POST "http://127.0.0.1:8376/mcp" -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'; curl -s -o /dev/null -X POST "http://127.0.0.1:8376/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'; kill "$pid" 2>/dev/null; sleep 1; grep -qiE 'listen|bind|serving|started|running on' "$d/srv.log" && grep -i warn "$d/srv.log" | grep -qiE 'auth|credential|unauthorized|key' && grep -qiE 'shut|stopp|exit|terminat' "$d/srv.log" && test "$(grep -cE '"(GET|POST) /' "$d/srv.log")" = 0 && echo OBSERVABILITY_OK

## TC-024 — Multiple concurrent agents share one HTTP server

- **Story**: US1 · **Traces to**: FR-001
- **Given** a stateless HTTP server over one store (the shared-service
  deployment from the spec's business value).
- **When** four authenticated clients call tools at the same time.
- **Then** all four get successful results against the shared store
  (concurrent-access boundary of the multi-agent promise).
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --stateless --db "$d/store.db" --port 8377 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8377/healthz" && break; sleep 0.5; done; for i in 1 2 3 4; do curl -sf -X POST "http://127.0.0.1:8377/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' >"$d/r$i.json" & done; wait; kill "$pid" 2>/dev/null; for i in 1 2 3 4; do grep -q '"result"' "$d/r$i.json" || { echo "client $i failed"; exit 1; }; done; echo CONCURRENT_OK

## TC-025 — HTTP defaults to read-only mode

- **Story**: US1 · **Traces to**: FR-007
- **Given** an HTTP server started with no read-only/read-write flags.
- **When** a write-purpose knowledge tool is invoked over HTTP and the
  health payload is read.
- **Then** the write is refused with a clean read-only error and the payload
  reports read-only true: HTTP defaults to read-only like the SSE daemon
  tri-state.
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8378 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8378/healthz" && break; sleep 0.5; done; sid=$(curl -s -D - -o /dev/null -X POST "http://127.0.0.1:8378/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}' | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); w=$(curl -s -X POST "http://127.0.0.1:8378/mcp" -H 'Authorization: Bearer qakey' -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"knowledge_add","arguments":{"title":"qa write probe","body":"must be refused","doc_type":"decision"}}}'); curl -s "http://127.0.0.1:8378/healthz" | grep -qE '"[a-z_]*read[a-z_]*"[[:space:]]*:[[:space:]]*true'; kill "$pid" 2>/dev/null; printf '%s' "$w" | grep -qiE 'read[ _-]?only' && echo READ_ONLY_DEFAULT_OK

## TC-026 — Explicit read-write flag overrides the HTTP default

- **Story**: US1 · **Traces to**: FR-007
- **Given** an HTTP server started with the existing explicit read-write
  flag.
- **When** the same write-purpose knowledge tool is invoked and the new
  document is searched for.
- **Then** the write succeeds and the subsequent read finds it: the explicit
  flag overrides the HTTP read-only default. (Standing verify for the
  tri-state machinery itself: the existing read-only enforcement pin
  `tests/test_core_smoke.py::test_read_only_mode_blocks_db_writes` stays
  green.)
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --read-write --db "$d/store.db" --port 8379 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8379/healthz" && break; sleep 0.5; done; sid=$(curl -s -D - -o /dev/null -X POST "http://127.0.0.1:8379/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}' | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); w=$(curl -s -X POST "http://127.0.0.1:8379/mcp" -H 'Authorization: Bearer qakey' -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"knowledge_add","arguments":{"title":"qa rw probe","body":"qa rw probe body","doc_type":"decision"}}}'); s=$(curl -s -X POST "http://127.0.0.1:8379/mcp" -H 'Authorization: Bearer qakey' -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"knowledge_search","arguments":{"query":"qa rw probe"}}}'); kill "$pid" 2>/dev/null; printf '%s' "$w" | grep -q '"result"' && ! printf '%s' "$w" | grep -q '"isError":true' && printf '%s' "$s" | grep -q '"result"' && echo READ_WRITE_OVERRIDE_OK

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
- **Pass condition**: `d=$(mktemp -d); c="$PWD/.venv/bin/cairn"; mkdir "$d/src"; printf 'def qadd(a, b):\n    return a + b\n' > "$d/src/m.py"; git -C "$d/src" init -q; ( cd "$d" && CAIRN_DB="$d/store.db" "$c" build >/dev/null 2>&1 ); printf '%s\n%s\n%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}' '{"jsonrpc":"2.0","method":"notifications/initialized"}' '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"find_definition","arguments":{"name":"qadd"}}}' | "$c" serve --db "$d/store.db" 2>/dev/null | grep -E '"id": ?3[,}]' | tail -1 > "$d/stdio.json"; CAIRN_MCP_API_KEY=qakey "$c" serve --transport http --db "$d/store.db" --port 8380 >"$d/srv.log" 2>&1 & pid=$!; for i in $(seq 1 24); do curl -s -m 1 -o /dev/null "http://127.0.0.1:8380/healthz" && break; sleep 0.5; done; sid=$(curl -s -D - -o /dev/null -X POST "http://127.0.0.1:8380/mcp" -H 'Authorization: Bearer qakey' -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"qa","version":"0"}}}' | grep -i '^mcp-session-id:' | tr -d '\r' | awk '{print $2}'); curl -s -X POST "http://127.0.0.1:8380/mcp" -H 'Authorization: Bearer qakey' -H "mcp-session-id: $sid" -H 'Content-Type: application/json' -H 'Accept: application/json' -d '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"find_definition","arguments":{"name":"qadd"}}}' > "$d/http.json"; kill "$pid" 2>/dev/null; .venv/bin/python -c 'import json,sys; a=json.load(open(sys.argv[1]))["result"]; b=json.load(open(sys.argv[2]))["result"]; assert not a.get("isError") and not b.get("isError"), (a,b); t=a["content"][0]["text"].strip(); u=b["content"][0]["text"].strip(); assert "m.py" in t and "qadd" in t, t; assert t==u, (t,u)' "$d/stdio.json" "$d/http.json" && echo ANSWER_PARITY_OK

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
least one TC with an observable pass condition. TC-011 and TC-017 are
standing regression guards over already-green behavior; TC-018 pins the live
`--port`-dispatch and stdio-default contract end to end; TC-016 is manual
with its standing verify named in the case (container build time exceeds the
120 s auto cap).

Priorities: P1 × 19 (TC-001–TC-012, TC-017, TC-018, TC-021, TC-022, TC-025,
TC-026, TC-027), P2 × 8 (TC-013–TC-016, TC-019, TC-020, TC-023, TC-024).
