# Plan: symbol-communities

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-04
**Branch**: `feat/symbol-communities` (survey baseline 0.21.2 @ 2894c1f) | **Team**: solo, PR-per-milestone

## Milestones
<!-- Each milestone = a phase in task.md. -->
| Phase | Milestone | Delivers (demoable) | Requirements | Depends on |
|-------|-----------|---------------------|--------------|------------|
| 1 | Analytics extra + communities core | `[graph-analytics]` extra pinned `networkx>=3.4`; without it `cairn communities` fails with an install hint and the store is byte-unchanged; with it, the command computes Louvain over structural edges (kind-weighted) and populates `communities` + `symbol_communities` (additive schema, full refresh), printing community count and sizes | FR-001, FR-002, FR-004 | — |
| 2 | Deterministic hubs and labels | Fixed-seed double run on an unchanged graph produces identical partition, labels, and printed output; global + per-community top-K hubs (K=10) by combined structural degree; deterministic LLM-free labels persisted in the tables | FR-003, FR-007, FR-008 | Phase 1 |
| 3 | Compass Subsystems section | A compass generated with community data carries a "Subsystems" section (labels + representative hubs); without data the section is absent and output is unchanged; critic vocabulary recognizes the new section | FR-005 | Phase 2 |
| 4 | Dashboard /communities view | `/communities` renders communities, sizes, hubs, and member drill-down; registered in nav with icon; every view-inventory/accessibility pin updated (14→15 nav ids, 16→17 crawled paths) and the full dashboard suites pass at the new counts | FR-006, NFR-006 | Phase 2 |
| 5 | Performance gate + docs closeout | Communities budget row at the 1000-file scaling point (≤10s wall) enforced like the closure gate; docs/CHANGELOG reflect the command, extra, and view | NFR-003 | Phase 2 |

Already in tree per survey evidence — **reused, not re-implemented** (task-breaker cites these as context):
- `STRUCTURAL_EDGE_KINDS` (src/cairn/graph/traversal.py:8-12) — the exact FR-002 input set, closure/taint-consumed.
- Full-refresh precedent: DELETE + one sorted executemany (src/cairn/graph/dataflow.py:485); additive-table precedent: plain `CREATE TABLE IF NOT EXISTS` with no MIGRATIONS entry (src/cairn/graph/schema.py:214-217, exemplars `dataflow` :343-349, `transitive_edges` :420).
- Optional-extra probe convention with actionable hint (src/cairn/bench/swe_bench.py:fetch_dataset_rows:89, hint at :95-101).
- Structural-only in-degree precedent (src/cairn/pack.py:_structural_indegree:282); deterministic label precedent (src/cairn/graph/repo_map.py:_dominant_segment:99, share threshold :13).
- Read-path degrade convention (src/cairn/dashboard/data.py:_knowledge_table_present:951); scaling-gate shape (src/cairn/bench/scaling_suite.py:15, :84-98; tests/test_scaling_gate.py:15 GATE_FILES=1000, :69-73).
- CLI registration rides the side-effect imports (src/cairn/cli/__init__.py:14-41); invocation telemetry is inherited free via `_RecordingGroup` (src/cairn/cli/main.py:71, NFR-005 — no work).

## Dependencies
```
Phase 1  FR-001 + FR-002 + FR-004 — extra, probe, schema, compute, CLI
   │  strictly ordered: same new module + schema blocks + CLI file;
   │  Phase 2 pins determinism of Phase 1's compute and extends its tables
   v
Phase 2  FR-003 + FR-007 + FR-008 — seed, hubs, labels
   │  fan-out point: tables + labels + hubs exist; downstream surfaces read them
   v
Phase 3  FR-005 (compass) ∥ Phase 4  FR-006 + NFR-006 (dashboard) ∥ Phase 5  NFR-003 (perf/docs)
```
- **Phase 1 → Phase 2, strictly ordered**: both own the new communities module, the schema.py additive blocks, and the CLI module; Phase 2's seeded double-run test exists only against Phase 1's compute path, and hub/label outputs extend the exact tables Phase 1 creates. Nothing in Phase 2 is demoable without a populated partition.
- **Phase 2 → Phases 3/4/5**: the Subsystems section lists "communities with representative hub symbols" (spec US2 AC1) and the dashboard renders hubs — both consume FR-003/FR-008's persisted outputs; FR-007 must be pinned first so no downstream surface bakes in unstable labels/ordering. Phase 5 times the whole pipeline (compute + persist + hubs + labels), so it also waits for Phase 2.
- **Phase 3 ∥ Phase 4 ∥ Phase 5, independent**: disjoint file sets (evidence below); each consumes the frozen Phase 1-2 tables read-only.

## Parallelization map
<!-- Which work areas are independent (different files/subsystems, no shared
     state) and can be developed concurrently, and which are strictly
     sequential. The task-breaker turns this into [P] markers per task. -->
Parallel is the default; this map names where it yields. Phases 3, 4, 5 carry [P]; their file sets are disjoint and checkable against the survey:

- **Independent: Phase 3 (compass) ∥ Phase 4 (dashboard) ∥ Phase 5 (perf/docs)** — disjoint files:
  - Phase 3 owns `src/cairn/compass/generator.py` (`_template_body:277`), `src/cairn/compass/critic.py` (`_DEFAULT_SECTION_VOCAB:75-85`, the `total = 5.0` default-vocab accounting at `critic_concept:133`), `src/cairn/llm/tasks.py` (prompt section enumerations :821-822, :833), and their test files `tests/test_compass_generator.py`, `tests/test_compass_critic.py`, plus the vocab pin `tests/test_wiki_promotion.py` (`test_default_vocab_bit_identical_for_existing_callers:383`). Any community read helper it needs lives compass-side, not in the communities module.
  - Phase 4 owns `src/cairn/dashboard/shell.py` (NAV_SECTIONS/NAV_LABELS :18-41), `src/cairn/dashboard/app.py` (route import + register :332-365), `src/cairn/dashboard/data.py` (new `get_*` readers beside `_knowledge_table_present:951`), new `src/cairn/dashboard/routes/communities.py` + `templates/communities.html`, `src/cairn/dashboard/templates/_icons.html` (:8-34, 14→15 arms), and the pin tests `tests/test_dashboard_shell.py` (:41 count, :50 labels), `tests/test_dashboard_accessibility.py` (:28-45), `tests/test_dashboard_restyle.py` (:23-40), `tests/test_dashboard_assets.py` (:23-38), `tests/test_dashboard_readonly.py` (:27-56 ROUTES).
  - Phase 5 owns `src/cairn/bench/scaling_suite.py` (budget row beside the closure budget, :15, :84-98), `tests/test_scaling_gate.py`, plus docs/CHANGELOG — no file either other track touches.
- **Strictly ordered**:
  - Phase 1 → Phase 2: Phase 1 produces the partition + derived tables; Phase 2 consumes them for hubs, labels, and the determinism pin (same module/schema/CLI files — see Dependencies).
  - Phase 2 → 3/4/5: rendering and timing read what Phase 2 persists.
  - Read-side helpers stay in the consumer's area (compass-side for Phase 3, `dashboard/data.py` for Phase 4); `src/cairn/graph/communities.py` and `schema.py` are frozen once Phase 2 lands, which is what keeps 3 ∥ 4 ∥ 5 file-disjoint.
- **Decision sequenced early (Phase 1, before any fan-out)**: reconcile reuse/extension of `cairn map`'s existing per-cluster hubs (`src/cairn/graph/repo_map.py:_clusters:111`, DEFAULT_HUB_CAP=3 :11) vs a standalone clustering module. Default stance: standalone, importing `STRUCTURAL_EDGE_KINDS` and copying the `_dominant_segment` labeling precedent — repo_map's degrees count ALL edge kinds (subquery repo_map.py:52-56, no kind filter), so they cannot satisfy FR-002/FR-003 as-is. If tech overturns toward extending repo_map, Phase 1 is still serial, so no parallel track is affected — but the decision must land in Phase 1 because it fixes the degree/weighting definitions Phase 2's hubs and Phase 5's timing measure.

## Checkpoints
<!-- Exit condition per phase; verify before starting the next. -->
Run from the repo root with the repo venv (`uv run`); pin `CAIRN_LIB=/tmp/__no_such_lib__` for dashboard/compass suites per survey convention.

- **After Phase 1** — command exists, gated, and persists (FR-001/002/004):
  ```
  grep -rn "communities\|louvain" src/cairn tests --include="*.py" | wc -l        # > 0 (survey absence verify, inverted)
  python3 -c "import tomllib; d=tomllib.load(open('pyproject.toml','rb')); print(d['project']['optional-dependencies'].get('graph-analytics'))"   # includes 'networkx>=3.4'
  uv run --no-sync python -c "import cairn"                                       # core install imports without networkx
  # without the extra: `cairn communities` prints an install hint, exits non-zero, store file hash unchanged
  # with the extra: run on a built store -> sqlite3 "$STORE_DB" "SELECT COUNT(*) FROM communities"  > 0
  ```
  Plus the new Phase 1 tests green.
- **After Phase 2** — deterministic, hubs, labels (FR-003/007/008):
  ```
  uv run --no-sync python -c "from networkx.algorithms.community import louvain_communities; import inspect; print('seed' in inspect.signature(louvain_communities).parameters)"   # True (survey FR-007 verify)
  # two consecutive `cairn communities` runs on an unchanged graph -> diff of printed output is empty; table contents identical
  # global + per-community hub lists present in output, K=10 default
  ```
  Plus the seeded double-run owner test green.
- **After Phase 3** — compass section coupled to critic (FR-005):
  ```
  CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_compass_critic.py tests/test_compass_generator.py -q    # green (baseline: 42 passed)
  grep -rn "Subsystems" src/cairn/compass/generator.py src/cairn/compass/critic.py    # hits in both (body + vocab)
  ```
  Owner test `tests/test_compass_generator.py:test_deterministic_template_passes_own_critic:204` green both with and without community data.
- **After Phase 4** — dashboard view at the new pin counts (FR-006, NFR-006):
  ```
  CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_dashboard_shell.py tests/test_dashboard_accessibility.py tests/test_dashboard_restyle.py tests/test_dashboard_assets.py tests/test_dashboard_readonly.py -q
  # green at 15 nav ids / 17 crawled paths (survey baseline: 10 passed + 31 passed at 14/16)
  ```
- **After Phase 5** — budget gate + docs (NFR-003):
  ```
  CAIRN_LIB=/tmp/__no_such_lib__ uv run --extra test pytest tests/test_scaling_gate.py -q -m "not infra"   # green with the communities ≤10s row at GATE_FILES=1000
  ```

## Risks & mitigations
- Risk: partition quality varies with edge density and kind weighting, and no weighting prior art exists in-repo (survey FR-002: "unknown — verify") → mitigation: weighting schema is a Phase 1 tech-spec decision documented there; tests are deterministic fixture assertions, never quality judgments (spec risk note).
- Risk: `louvain_communities` presence unverified on the py<3.11 lock arm (networkx 3.4.2, uv.lock:3470; survey FR-007) → mitigation: the `[graph-analytics]` extra pins `networkx>=3.4`, which guarantees `louvain_communities(seed=...)` on every CI Python arm; the Phase 2 double-run test is the behavioral proof.
- Risk: dashboard pin surface is the widest in the repo (spec risk) → mitigation: all six pin moves (shell count, icon branches, three `_MAIN_VIEWS` tuples, readonly ROUTES) enumerated in the survey and updated in one Phase 4 task.
- Risk: critic vocab change has hidden pins beyond the compass suites — `critic_concept` has 48 exact callers (LLM task gate at `src/cairn/llm/tasks.py:433,437`, MCP tools, `cairn validate`), and `tests/test_wiki_promotion.py:383` pins the default vocab byte-identically → mitigation: vocab + `total` accounting + tasks.py enumerations move in one Phase 3 change set; the owner test at `tests/test_compass_generator.py:204` and the wiki-promotion pin are updated in the same task, never left to CI to catch.
- Risk: pr-tooling (downstream spec) will consume the `communities`/`symbol_communities` tables → mitigation: Phase 1 fixes the additive schema once; later phases extend output columns only via the same additive discipline.

## Delivery
Solo, branch `feat/symbol-communities`, one PR per milestone in phase order (Phases 3/4/5 may land in any order after Phase 2 — interleave freely). Every PR follows the shipping workflow: pre-commit run --all-files, conventional commit + PR title, audit checklist; `cairn update` + `record_memory` after each merge.
