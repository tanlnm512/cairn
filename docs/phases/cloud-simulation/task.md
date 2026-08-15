# Tasks: Cloud Multi-Service Simulation

Companion: [spec.md](spec.md) · [plan.md](plan.md). Status reflects code
state on v0.10.0 @ `7e90628` (surveyed 2026-08-15), not intent.

## Burndown

| Status | Count |
|--------|-------|
| done | 0 |
| partial | 0 |
| todo | 17 |
| **total** | **17** |

---

## SIM-1 — Cloud-sim orchestration driver

Done when: `make cloud-sim-up` / `make cloud-sim-down` bring up and tear
down gateway (SSE on a published port), indexer, and worker containers
against a prepared sample workspace, and `container system status` gate +
`--uid/--gid` bind-mount ownership follow the ci-local.sh pattern verbatim.

- [ ] **S1.1 — Workspace prep subcommand.** `scripts/cloud-sim.sh prepare`:
      clone/copy a sample repo (default: cairn itself) into
      `~/cairn-cloud-sim/workspace`, set `CAIRN_HOME=~/cairn-cloud-sim/home`,
      run a first `cairn update` on the host so the store exists before any
      container starts.
      verify: `ls ~/cairn-cloud-sim/home/<store-key>/.kg` exists; re-run is
      idempotent.
- [ ] **S1.2 — Driver skeleton.** `scripts/cloud-sim.sh` with
      `up|down|status|logs|check` subcommands + `Makefile` targets
      (`cloud-sim-up` etc.). `up` gates on `container system status` (start
      if down, ci-local.sh lines 63–67 pattern), then launches services with
      `container run --rm -d … --mount type=bind --uid "$(id -u)" --gid
      "$(id -g)"`.
      verify: `make cloud-sim-up && make cloud-sim-status && make
      cloud-sim-down`; after down, no lingering containers.
- [ ] **S1.3 — Service images.** `deploy/cloud-sim/{gateway,indexer,worker}.Containerfile`
      (or shared base): python:3.12-bookworm + uv-synced cairn from the
      bind-mounted source with per-service extras (gateway: minimal +
      `CAIRN_EMBED_BACKEND=hash`; indexer: full; worker: minimal).
      verify: each image boots `cairn --version`; image sizes recorded in
      findings.md.
- [ ] **S1.4 — Health checks.** Gateway probe reusing the
      `lifecycle.sse_responds` raw-socket approach against the published
      port; indexer/worker liveness via heartbeat files on the shared
      volume.
      verify: `make cloud-sim-status` reports per-service state; killing a
      service flips it to down within 10 s.

## SIM-2 — Gateway service (read-only SSE)

Done when: an MCP client inside the container network (or host, via
published port) connects to the gateway's SSE endpoint and completes one
`explore` + one `find_definition` call round-trip against the shared store,
with `CAIRN_READ_ONLY=1` set and zero `lock_contention` events emitted by
the gateway during a concurrent indexer run.

- [ ] **S2.1 — Gateway launch config.** Env: `CAIRN_READ_ONLY=1`,
      `CAIRN_WORKSPACE=/sim/workspace`, `CAIRN_HOME=/sim/home`,
      `CAIRN_SESSION=cloud-sim-gateway`; command: `cairn serve run --port
      9876`; publish the port.
      verify: SSE URL reachable; `cairn serve status`-equivalent from inside
      reports read-only mode.
- [ ] **S2.2 — MCP round-trip check.** One-shot client container using the
      mcp SDK's SSE client against the gateway; asserts `explore` and
      `find_definition` return structured results for seeded queries.
      verify: `scripts/cloud-sim.sh check` exits 0.
- [ ] **S2.3 — Reader-isolation proof.** While the indexer runs `cairn
      update`, run 100 gateway queries; assert zero `lock_contention`
      events attributable to the gateway (query the `events` table on the
      shared store; note C-6: read-only servers may not record events — if
      so, capture gateway stderr `note_contention` warnings instead and
      record the darkness as a finding).
      verify: scenario script asserts count == 0; finding logged either way.

## SIM-3 — Indexer service (single writer)

Done when: the indexer container runs `cairn update` on the shared
workspace volume, a second concurrent indexer start exits immediately with
the documented `build_lock` RuntimeError (single-writer proof), and the
gateway serves the updated symbols within one boot-cycle or watcher cycle
(ties into FRESH-1 if shipped).

- [ ] **S3.1 — Indexer loop.** Container command: bounded loop of `cairn
      update` (sleep between passes; optionally `git -C … pull` if the
      sample workspace is a clone).
      verify: after editing a file in the workspace, a later indexer pass
      picks it up (symbol count changes).
- [ ] **S3.2 — Single-writer race scenario.** `make cloud-sim-race`: start
      two indexers; assert the second exits non-zero with the `build_lock`
      RuntimeError string in logs, the first completes.
      verify: scenario script greps logs; exit codes asserted.
- [ ] **S3.3 — Reader staleness measurement.** Measure
      edit-visible-to-gateway latency: (a) gateway restart cycle (today's
      boot-time `ensure_fresh_force`), (b) watcher cycle if FRESH-1 shipped.
      verify: numbers recorded in findings.md under "staleness window".

## SIM-4 — Worker service (task queue)

Done when: a task enqueued on the shared volume (`.knowledge/_tasks/`) is
claimed by exactly one of two concurrently running worker containers (claim
marker atomicity), completed via `cairn task complete`, and the losing
worker's poll observes the claim.

- [ ] **S4.1 — Worker loop.** Container command: poll `cairn task list
      --status pending` every N seconds; claim+complete using a stub agent
      (deterministic result file) so no real LLM is needed.
      verify: `cairn task list --status done` shows the completed task with
      the worker's id.
- [ ] **S4.2 — Claim race scenario.** Two worker replicas + one task;
      assert exactly one winner (claim marker `O_EXCL` semantics across
      containers on the shared volume), loser's next poll shows non-pending.
      verify: scenario script asserts single completion; no duplicate
      `complete_task` accepted (ownership-mismatch refusal path).

## SIM-5 — Telemetry correlation

Done when: an OTLP receiver (svc-otel, e.g. an OpenTelemetry collector
container or a local HTTP sink) receives log records from gateway and
indexer tagged `service.name` + distinct `CAIRN_SESSION` values, and the
findings doc records what a read-only gateway *cannot* emit today (C-6) as
a numbered gap.

- [ ] **S5.1 — OTLP sink.** Fourth container running an OTLP HTTP receiver
      (otel collector image if pullable via container CLI, else a ~30-line
      Python sink printing received log records).
      verify: sink log shows records from both services.
- [ ] **S5.2 — Correlation + gap record.** Distinct `CAIRN_SESSION` per
      service; static `service.name="cairn"` collision documented; C-6
      (read-only emits nothing via `sink.is_read_only()` /
      `metric_buffering` gates) written into findings.md as a numbered gap
      with the exact symbols.
      verify: findings.md contains both entries; sink output attached.

## SIM-6 — Findings report gates the Postgres decision

Done when: `docs/phases/cloud-simulation/findings.md` records measured
numbers for: WAL/lock contention on the shared volume, reader staleness
window, claim-race outcomes, telemetry coverage gaps (C-1..C-6 each mapped
to observed-or-not-observed), and an explicit go/no-go recommendation for
the `postgres-backend` phase's trigger conditions.

- [ ] **S6.1 — Write findings.md** from the scenario outputs (contention
      counts, staleness numbers, race outcomes, telemetry gaps, image
      sizes).
      verify: every number traces to a scenario log kept under
      `~/cairn-cloud-sim/logs/`.
- [ ] **S6.2 — Postgres gate criteria.** Translate findings into explicit
      triggers for the `postgres-backend` phase (e.g. "N+ contention events
      per hour under M concurrent readers" / "staleness unbounded without
      watcher"). Cross-link from
      `docs/phases/postgres-backend/spec.md` §2.
      verify: cross-link resolves; criteria are numeric.
- [ ] **S6.3 — Record learnings.** `cairn memory record decision
      "cloud-sim architecture + postgres gate"` after running the full
      scenario set once.

---

## Post-phase hygiene

- `cairn update` on the cairn repo itself after the scripts land; `cairn
  doctor` exit 0.
- `scripts/verify_no_code_change.py` green unless a task explicitly touched
  `src/` (none planned).
