# Plan: Cloud Multi-Service Simulation

Companion: [spec.md](spec.md) · [task.md](task.md). "Done when" wording is
owned by spec.md and repeated verbatim in task.md.

## 0. Evidence base (Phase-A survey, 2026-08-15)

Facts the plan relies on (all verified in code or on this machine):

- `mcp_server/server.py` — `run(transport, port)` supports SSE; mcp SDK
  1.29.0 (`uv.lock`) makes uvicorn/starlette core deps, so the serving image
  needs no extra web stack.
- `mcp_server/lifecycle.py` — `sse_url()` → `http://<host>:<port>/sse`;
  `sse_responds()` is a raw-socket HTTP probe we can reuse as a health check.
- `graph/schema.py` — `get_db(read_only=True)` opens `file:…?mode=ro`;
  `build_lock` is `fcntl.flock` LOCK_EX|LOCK_NB, second writer raises
  RuntimeError immediately.
- `llm/tasks.py` — `claim_task` uses `O_CREAT|O_EXCL` claim markers,
  `CLAIM_STALE_SECONDS=3600`; tasks live in `.knowledge/_tasks/`.
- `telemetry/otel.py` — `CAIRN_OTEL_ENDPOINT` opts into synchronous
  OTLP log export; `service.name="cairn"` static; `CAIRN_SESSION` is the
  only correlation id.
- `paths.py` — `CAIRN_HOME`, `CAIRN_WORKSPACE`, `CAIRN_DB`/`CAIRN_KNOWLEDGE`
  hard overrides exist as env — enough to point all services at the shared
  volume **without code changes**.
- Machine: Apple `container` CLI v1.0.0 verified at `/usr/local/bin/container`;
  `scripts/ci-local.sh` is the in-repo reference for `container system
  status/start`, `container run --rm --mount type=bind --workdir --uid --gid
  [--arch amd64 --rosetta] [--cpus --memory]`, image
  `python:${PY}-bookworm`. No docker on this host.
- Cross-check: WAL-mode SQLite requires a filesystem with working shared
  mmap locks; Apple container bind mounts are local-filesystem passes, so
  WAL across the shared volume is exercised on real disk, not NFS.

## 1. Implementation options

### Option A — Shared-volume 3-service rehearsal (Apple container, bash driver)

One CAIRN_HOME bind volume; gateway (read-only SSE), indexer (writer loop),
worker (task loop) as separate `container run` invocations orchestrated by
`scripts/cloud-sim.sh`; optional OTLP sink container. Images: bind-mounted
source + `uv sync` at boot (exact ci-local.sh pattern, cacheable under
`.cache/cloud-sim/`).

- **Pros:** zero cairn code changes to reach day one (env overrides +
  existing transports/locks/queue are sufficient); faithfully exercises the
  *real* production hazards (WAL on shared disk, flock across containers,
  claim races, read-only darkness); reuses a proven in-repo invocation
  pattern; disposable and cheap; produces exactly the evidence the Postgres
  phase needs as its gate.
- **Cons:** not a real cloud (single host, local disk ≠ networked storage —
  SQLite-over-network, the actual C-1 production blocker, is only
  *represented*, not reproduced); no compose ergonomics; arm64/amd64 parity
  needs explicit `--arch` flags; findings must be honest that "shared local
  volume" is the optimistic case.
- **Cost:** ~1 week; ~300 lines of scripts + 2 Containerfiles.

### Option B — Full microservice decomposition now (API gateway, per-layer
services, message queue, Postgres)

Split graph/compass/memory/knowledge layers into services behind an HTTP
API, RabbitMQ/Redis queue, Postgres+pgvector storage.

- **Pros:** the "real" end-state; solves C-1..C-3 properly.
- **Cons:** requires the storage migration first (C-1) — which is precisely
  the decision this phase is supposed to *inform*; the survey found no
  GraphStore seam (the `9efaf55` protocol was never merged; every query
  function takes a raw `sqlite3.Connection`), so layer-splitting means
  rewriting every call site; multi-tenant routing (C-3) changes every tool
  signature; estimated months, not weeks, and it would invalidate the
  local-first single-user default that is cairn's current identity.
- **Cost:** months; high regression risk; contradicts verification-first
  roadmap.

### Option C — Remote-host rehearsal on a real cloud VM (rented Linux box,
docker-compose, real network)

Run the same 3 services on a small cloud VM with docker compose.

- **Pros:** reproduces the network boundary (real NFS/EBS latency options,
  real DNS/TLS concerns); compose ergonomics.
- **Cons:** no docker on this machine (memory-verified) — and more
  importantly the deliverable shifts from *evidence* to *ops*; cost and
  secrets handling for an experiment; the interesting failures (C-2 flock
  over network, SQLite WAL on NFS) are already well-documented upstream
  failures that don't need renting a VM to confirm; contradicts the "use
  apple container or relevant tools" constraint in the requirement.
- **Cost:** ~1–2 weeks + infra cost; findings contaminated by ops noise.

## 2. Recommendation: **Option A**

The requirement's own wording ("*simulate*… *verify* this requirement… use
apple container or relevant tools") asks for a faithful rehearsal that
produces a go/no-go signal, not production decomposition. Option A is the
only option that (a) runs today with zero core-code changes, (b) exercises
the genuine cross-process hazards on real shared disk, and (c) yields the
measured findings (SIM-6) that legitimately gate the `postgres-backend`
phase. Options B and C spend weeks reproducing failures whose existence is
already established; what's unknown is their *magnitude and frequency*,
which A measures.

## 3. Sequencing (chosen path)

```
Week 1
  SIM-1  scripts/cloud-sim.sh skeleton + Makefile targets (up/down/logs/status)
         health checks reuse lifecycle.sse_responds-style probes
  SIM-2  gateway container: uv-synced env, CAIRN_READ_ONLY=1, published SSE port
         → first MCP round-trip (explore + find_definition) over SSE
  SIM-3  indexer container: cairn update loop on shared workspace
         → single-writer proof (second indexer must fail fast on build_lock)

Week 2
  SIM-4  worker container(s): task claim/complete race with 2 replicas
  SIM-5  OTLP sink + CAIRN_SESSION correlation; read-only darkness recorded
  SIM-6  findings.md: contention counts, staleness windows, claim races,
         C-1..C-6 observed-or-not table, Postgres go/no-go criteria
```

Each `make cloud-sim-*` target is idempotent and safe to re-run
(`container system status` gate first, exactly like ci-local.sh).

## 4. Verification commands

- Bring-up: `make cloud-sim-up && make cloud-sim-status` (all three healthy).
- Round-trip: `scripts/cloud-sim.sh check` — one `explore` + one
  `find_definition` via SSE from a one-shot client container.
- Single-writer: `make cloud-sim-race` — second indexer exits non-zero with
  the `build_lock` RuntimeError string in its logs.
- Claim race: `make cloud-sim-tasks` — two workers, one task, exactly one
  `complete_task` success asserted via `cairn task list --status done`.
- Teardown: `make cloud-sim-down` — no leftover containers
  (`container ls`-equivalent empty).
- Repo hygiene: `scripts/verify_no_code_change.py` (existing) must still
  pass — the simulation is scripts + docs only unless a task explicitly
  says otherwise.
