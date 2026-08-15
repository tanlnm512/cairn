# Phase: Cloud Multi-Service Simulation (requirement verification + local rehearsal)

- **Status:** planned (not started) — **requirement verified VALID with a
  narrowed scope** (see §2)
- **Date drafted:** 2026-08-15
- **Code state baseline:** v0.10.0 @ `7e90628` (main)
- **Companion docs:** [plan.md](plan.md) · [task.md](task.md)

## 1. The requirement, as asked

> "Simulate how to make this run on cloud, separate this into multi
> services — verify this requirement; if valid, use Apple container or
> relevant tools to serve it."

Verification splits into three questions: (a) is a multi-service split
technically coherent for cairn today, (b) what is the honest minimal
decomposition, (c) can it be rehearsed faithfully on this machine. Answers
follow, each with the code evidence that forces it.

## 2. Verification verdict

### VALID — as a **simulation/rehearsal phase** with a 3-service thin slice

The codebase already partitions by *process role*, and that partition maps
1:1 onto services without changing any core logic:

1. **A serving tier already exists and is already read-isolated.** The SSE
   transport is implemented (`mcp_server/server.py` `run(transport="sse")`,
   opt-in daemon on port 9876 via `mcp_server/lifecycle.py`
   `render_plist`/`DEFAULT_PORT`), and `CAIRN_READ_ONLY` opens the DB as
   `file:…?mode=ro` so the daemon "can never contend with writers" (surveyed
   docstrings in `graph/schema.py` `get_db`). This is a reader service in
   all but packaging.
2. **Writes are already a separate role.** All mutations flow through CLI
   processes under a cross-process `fcntl.flock` single-writer lock
   (`graph/schema.py` `build_lock`); the SSE daemon deliberately never
   writes.
3. **The LLM task queue is already agent-decoupled.** `llm/client.py`
   `FileQueueBackend` "writes a Task, waits for any agent to complete it…
   No subprocess spawn"; claiming is atomic `O_CREAT|O_EXCL` markers
   (`llm/tasks.py` `claim_task`). A dedicated worker service is the same
   protocol with a loop around it.
4. **Remote-facing observability exists.** OTLP log-record export via
   `CAIRN_OTEL_ENDPOINT` (`telemetry/otel.py`), with
   `service.name="cairn"` already set — multi-instance telemetry is a
   config change, not new code.
5. **Heavy compute is already env-swappable.** `CAIRN_EMBED_BACKEND=openai`
   (`graph/embeddings.py` `_backend_name`) removes torch from the serving
   path.

### INVALID — as a **production decomposition today**

Six hard couplings make anything beyond the thin slice a storage rewrite,
not a deployment change (this is why the `postgres-backend` phase exists and
why this simulation gates it):

| # | Coupling | Evidence | Consequence across a network boundary |
|---|----------|----------|----------------------------------------|
| C-1 | One SQLite file is four stores (graph + FTS5 + vec0 + telemetry) | `graph/schema.py` `SCHEMA_SQL` creates `symbols_fts` and telemetry tables in the same `.kg`; `paths.py` `StorePaths` | no network-transparent shared disk; SQLite on NFS is explicitly unsupported for WAL writes |
| C-2 | All cross-process coordination is POSIX filesystem | `build_lock` (flock), `okf/bundle.py` `_okf_bundle_lock` (flock, 5 s), `claim_task` (O_EXCL markers) | flock/O_EXCL do not span hosts |
| C-3 | Workspace is process-local, not request-borne | `paths.py` `resolve_workspace` (arg > env > registered ancestor > cwd); **no tool takes a workspace/tenant parameter** | a remote server cannot select a store per request |
| C-4 | stdio lifetime bound to local parent | `server.py` `_install_exit_watchdog` polls `os.getppid()`; strays swept via `pgrep`/`ps`/`lsof` (`lifecycle.find_strays`) | pid-based lifecycle is meaningless off-host |
| C-5 | Freshness is boot-time, in-process | `watcher.py` — "A long-running `cairn serve` process does NOT see source edits made after it started"; boot runs `ensure_fresh_force` | reader staleness unbounded without a watcher service |
| C-6 | Read-only tier emits no telemetry | `sink.is_read_only()` and `metric_buffering._log_metric` skip under `CAIRN_READ_ONLY` | the exact instances you'd most want to observe are dark |

**Verdict:** run the 3-service simulation to *quantify* C-1..C-6 (they
become measured findings, not hypotheses), and let those findings gate the
Postgres phase. Do not attempt a full decomposition in this phase.

## 3. Simulation architecture (what gets built)

Three services over one shared volume (CAIRN_HOME), mirroring the existing
role partition — **no core cairn code changes except where a task says so**:

```
┌────────────────────────┐        ┌────────────────────────┐
│ svc-gateway (reader)   │        │ svc-indexer (writer)   │
│ cairn serve run        │        │ loop: git pull/checkout│
│  --port 9876 (SSE)     │        │  + cairn update        │
│ CAIRN_READ_ONLY=1      │        │  (holds build_lock)    │
└───────────┬────────────┘        └───────────┬────────────┘
            │        shared bind volume        │
            └────────────┬─────────────────────┘
                         ▼
         ~/cairn-cloud-sim/workspace  (the repo being indexed)
         CAIRN_HOME (the .kg store + .knowledge/ + _tasks/)
┌────────────────────────┐
│ svc-worker (tasks)     │  polls .knowledge/_tasks, claims via
│ loop: cairn task list  │  claim_task markers, runs critic on
│  → claim → complete    │  completion (any agent CLI or stub)
└────────────────────────┘
  + svc-otel (optional 4th): OTLP receiver, fed by CAIRN_OTEL_ENDPOINT
```

Invariants rehearsed: single-writer (C-2's `build_lock` on shared volume —
valid because all mounts are local-NVMe bind mounts, **not** NFS), reader
isolation (`mode=ro` + WAL), queue claim atomicity across containers,
session correlation of telemetry (`CAIRN_SESSION` per service).

**Vehicle: Apple `container` CLI** (verified present: `container` CLI
v1.0.0, `/usr/local/bin/container`). The repo already has a proven
invocation pattern in `scripts/ci-local.sh`: `container system status` /
`container system start` (idempotent), `container run --rm --mount
type=bind,… --workdir … --uid "$(id -u)" --gid "$(id -g)" [--arch amd64
--rosetta] [--cpus/--memory] python:${PY}-bookworm`. No docker exists on
this machine; no docker-compose either — orchestration is a shell/make
driver following the ci-local.sh pattern (see plan.md).

## 4. Items and "Done when"

Each item appears verbatim in [plan.md](plan.md) and [task.md](task.md).

### SIM-1 — Cloud-sim orchestration driver

A `scripts/cloud-sim.sh` (+ Makefile target) that boots the container
system, builds/starts the three services with correct env/mounts, waits for
health, runs scenarios, tears down.

- **Done when**: `make cloud-sim-up` / `make cloud-sim-down` bring up and
  tear down gateway (SSE on a published port), indexer, and worker
  containers against a prepared sample workspace, and `container system
  status` gate + `--uid/--gid` bind-mount ownership follow the ci-local.sh
  pattern verbatim.

### SIM-2 — Gateway service (read-only SSE)

- **Done when**: an MCP client inside the container network (or host, via
  published port) connects to the gateway's SSE endpoint and completes one
  `explore` + one `find_definition` call round-trip against the shared
  store, with `CAIRN_READ_ONLY=1` set and zero `lock_contention` events
  emitted by the gateway during a concurrent indexer run.

### SIM-3 — Indexer service (single writer)

- **Done when**: the indexer container runs `cairn update` on the shared
  workspace volume, a second concurrent indexer start exits immediately
  with the documented `build_lock` RuntimeError (single-writer proof), and
  the gateway serves the updated symbols within one boot-cycle or watcher
  cycle (ties into FRESH-1 if shipped).

### SIM-4 — Worker service (task queue)

- **Done when**: a task enqueued on the shared volume
  (`.knowledge/_tasks/`) is claimed by exactly one of two concurrently
  running worker containers (claim marker atomicity), completed via
  `cairn task complete`, and the losing worker's poll observes the claim.

### SIM-5 — Telemetry correlation

- **Done when**: an OTLP receiver (svc-otel, e.g. an OpenTelemetry
  collector container or a local HTTP sink) receives log records from
  gateway and indexer tagged `service.name` + distinct `CAIRN_SESSION`
  values, and the findings doc records what a read-only gateway *cannot*
  emit today (C-6) as a numbered gap.

### SIM-6 — Findings report gates the Postgres decision

- **Done when**: `docs/phases/cloud-simulation/findings.md` records
  measured numbers for: WAL/lock contention on the shared volume, reader
  staleness window, claim-race outcomes, telemetry coverage gaps (C-1..C-6
  each mapped to observed-or-not-observed), and an explicit
  go/no-go recommendation for the `postgres-backend` phase's trigger
  conditions.

## 5. Scope

**In scope:** the six items; containerfiles for the three services; the
orchestration driver; findings report. Container images build from the
local source tree (bind-mounted, as ci-local.sh does — no registry push).

**Out of scope:**
- Any change of storage engine (Postgres is the *gated* next phase, not
  this one).
- Kubernetes, Terraform, real cloud providers, CI integration.
- Multi-tenant routing (C-3 fix) — recorded as a finding, not built.
- Auth/TLS on the SSE endpoint (single-user local sim; noted as a
  production gap in findings).
- Windows/linux-at-scale portability of the driver (Apple container +
  arm64 default, `--arch amd64 --rosetta` available for parity checks).

## 6. Risks

| Risk | Mitigation |
|------|------------|
| Apple container has no compose; multi-container orchestration is hand-rolled | Driver is a ~150-line bash script modeled on ci-local.sh (proven); each service is independently runnable |
| Bind-mount SQLite WAL semantics | All mounts are local filesystems (container CLI bind mounts), never NFS — WAL safe; still assert with a wal-integrity check in scenarios |
| SSE client from host | Publish the port (container CLI supports publish; verified in SIM-2 bring-up) or run the MCP client inside a fourth one-shot container on the same network |
| torch/heavy deps in images | Gateway uses `CAIRN_EMBED_BACKEND=hash` or `openai` for the sim; indexer image installs full extras — sizes recorded in findings |
| Sim diverges from real cloud | Explicit non-goals + findings doc maps each simplification to the production gap it hides |
