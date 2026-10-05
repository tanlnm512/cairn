# Test Cases: bench-worked-artifacts

**Spec**: [spec.md](spec.md) | **Created**: 2026-10-05
Black-box, business-language verification traced to requirements. Each case
has an observable pass condition. No implementation details.
"Minimal deterministic run" = `cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1` — the cheapest deterministic suite invocation (dep-free hash embeddings, smallest synthetic corpus, fewest repeats; seconds, not minutes).
"Worked sandbox" = a throwaway directory standing in for the repo or benchmarks root, so no case writes into the real tree.
"Bundle" = the three artifacts of FR-001: the raw result JSON, the inputs manifest, and the README companion.
These are infrastructure-tier cases: every assertion is structure or determinism only — no wall-clock, throughput, or timing assert appears anywhere in this suite.

## TC-001 — A worked run ships its evidence: result JSON, inputs manifest, companion README
- **Story**: US1 · **Traces to**: FR-001 (AC1)
- **Given** a worked sandbox and a minimal deterministic run
- **When** the user runs the bench with `--worked` pointed at a directory
- **Then** that directory holds the raw result JSON, an inputs manifest, and a README companion — and the companion names every other file present
- **Pass condition**: `cd "$(mktemp -d)" && d="$(pwd)" && cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/bundle" >"$d/out" 2>&1 && test -f "$d/bundle/README.md" && test "$(find "$d/bundle" -type f ! -name "README.md" | wc -l | tr -d " ")" -ge 2 && ok=""; for f in "$d/bundle"/*; do case "${f##*/}" in README.md) ;; *) grep -qF "${f##*/}" "$d/bundle/README.md" || exit 1; python3 -m json.tool "$f" >/dev/null 2>&1 && ok=1 || true ;; esac; done; test -n "$ok"`

## TC-002 — The manifest is self-describing: suite, dataset version, repeats, backend, machine stamp, command line
- **Story**: US1 · **Traces to**: FR-001
- **Given** a completed worked run in a worked sandbox
- **When** a reader opens the bundle's inputs manifest
- **Then** it states the suite that ran, the dataset version, the repeat count, the embedding backend, the machine stamp, and the full command line that produced the run
- **Pass condition**: `cd "$(mktemp -d)" && d="$(pwd)" && cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/bundle" >/dev/null 2>&1 && grep -rq "perf" "$d/bundle" && grep -rqiE "dataset|DS-v" "$d/bundle" && grep -rq -- "--n-files 8" "$d/bundle" && grep -rq -- "--repeats 1" "$d/bundle" && grep -rqi "hash" "$d/bundle" && grep -rqiE "machine|platform|host|cpu" "$d/bundle" && grep -rq -- "--worked" "$d/bundle"`

## TC-003 — The bundle's raw result is the same payload `--save` writes
- **Story**: US1 · **Traces to**: FR-001 (AC1)
- **Given** two minimal deterministic runs of the same suite and inputs — one saved the baseline way, one bundled the worked way
- **When** the two raw result payloads are compared
- **Then** they carry the same payload structure (same field set, same sections); per-run values may differ only in the advisory timing fields
- **Pass condition**: `cd "$(mktemp -d)" && a="$(pwd)" && b=$(mktemp -d) && cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --save "$a/base.json" >/dev/null 2>&1 && cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$b/bundle" >/dev/null 2>&1 && test -s "$a/base.json" && python3 -c "import json,glob,sys;base=json.load(open(sys.argv[1]));ks=sorted(base);hit=[p for p in glob.glob(sys.argv[2]+'/*.json') if sorted(json.load(open(p)))==ks];sys.exit(0 if hit else 1)" "$a/base.json" "$b/bundle"`

## TC-004 — Pointed at a benchmarks root, each run gets its own suite-datasetversion directory
- **Story**: US1 · **Traces to**: FR-002
- **Given** a directory serving as the benchmarks root (it carries the inventory README)
- **When** a minimal deterministic run completes with `--worked` pointed at that root
- **Then** a worked area exists under it holding one new directory named for the suite and dataset version (perf-…), and that directory holds the bundle
- **Pass condition**: `cd "$(mktemp -d)" && d="$(pwd)" && mkdir -p "$d/benchmarks" && printf '# Benchmarks\n\nInventory of committed artifacts.\n' > "$d/benchmarks/README.md" && cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/benchmarks" >/dev/null 2>&1 && test -d "$d/benchmarks/worked" && ls "$d/benchmarks/worked" | grep -q "^perf-" && test -n "$(find "$d/benchmarks/worked" -name "README.md")"`

## TC-005 — Pointed at a specific directory, the bundle lands exactly there (created on demand)
- **Story**: US1 · **Traces to**: FR-002
- **Given** a target directory path that does not exist yet
- **When** a minimal deterministic run completes with `--worked` pointed at it
- **Then** the target is created and the bundle sits directly inside it — no extra per-run subdirectory is invented beneath it
- **Pass condition**: `cd "$(mktemp -d)" && d="$(pwd)" && cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/bundle" >/dev/null 2>&1 && test -f "$d/bundle/README.md" && test -z "$(find "$d/bundle" -mindepth 1 -type d)"`

## TC-006 — Re-running the same run overwrites it cleanly — never appends, never leaves debris
- **Story**: US1 · **Traces to**: FR-002
- **Given** a worked sandbox where one minimal deterministic run already wrote its bundle
- **When** the identical run writes to the same target again
- **Then** the set of files in the bundle is unchanged (same names, same count — overwritten, not appended) and no partial/temporary files remain behind
- **Pass condition**: `cd "$(mktemp -d)" && d="$(pwd)" && cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/run" >/dev/null 2>&1 && find "$d/run" -type f | sed "s|$d/run/||" | sort > "$d/l1" && cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/run" >/dev/null 2>&1 && find "$d/run" -type f | sed "s|$d/run/||" | sort > "$d/l2" && diff "$d/l1" "$d/l2" && ! find "$d/run" -type f \( -name "*.tmp" -o -name "*.part" -o -name "*.bak" \) | grep -q .`

## TC-007 — Every new artifact gets exactly one inventory row, keyed by its repo-relative path
- **Story**: US2 · **Traces to**: FR-003 (AC1)
- **Given** a benchmarks root whose inventory README exists with no worked rows
- **When** a minimal deterministic run completes pointed at that root
- **Then** the inventory gains one row per new bundle artifact, and each row keys the artifact by its path from the benchmarks root (never a bare filename) with a companion note
- **Pass condition**: `cd "$(mktemp -d)" && d="$(pwd)" && mkdir -p "$d/benchmarks" && printf '# Benchmarks\n\nInventory of committed artifacts.\n' > "$d/benchmarks/README.md" && cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/benchmarks" >/dev/null 2>&1 && test "$(grep -cE "worked/perf-[^/ ]+/[^/ ]+" "$d/benchmarks/README.md")" -ge 2`

## TC-008 — Re-runs never duplicate inventory rows
- **Story**: US2 · **Traces to**: FR-003 (AC1)
- **Given** a benchmarks root where a minimal deterministic run already appended its inventory rows
- **When** the identical run writes the same run again into the same root
- **Then** the inventory is unchanged — the same artifacts are not indexed twice
- **Pass condition**: `cd "$(mktemp -d)" && d="$(pwd)" && mkdir -p "$d/benchmarks" && printf '# Benchmarks\n' > "$d/benchmarks/README.md" && cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/benchmarks" >/dev/null 2>&1 && cp "$d/benchmarks/README.md" "$d/snap" && cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/benchmarks" >/dev/null 2>&1 && diff "$d/snap" "$d/benchmarks/README.md"`

## TC-009 — A worked run touches nothing in the inventory except its own appended rows
- **Story**: US2 · **Traces to**: FR-004
- **Given** a benchmarks root holding a pre-existing inventory row for a committed baseline and a separate companion notes document
- **When** a minimal deterministic run completes pointed at that root
- **Then** the inventory changes by additions only — no existing row is edited, reordered, or removed — and the companion notes document is byte-for-byte untouched
- **Pass condition**: `cd "$(mktemp -d)" && d="$(pwd)" && mkdir -p "$d/benchmarks" && printf '# Benchmarks\n\n| Path | Note |\n|---|---|\n| benchmarks/baselines/DS-v1.1/perf.json | Committed baseline |\n' > "$d/benchmarks/README.md" && printf 'campaign notes\n' > "$d/benchmarks/NOTES.md" && cp "$d/benchmarks/README.md" "$d/before" && cp "$d/benchmarks/NOTES.md" "$d/notes-before" && cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/benchmarks" >/dev/null 2>&1 && ! cmp -s "$d/before" "$d/benchmarks/README.md" && ! diff "$d/before" "$d/benchmarks/README.md" | grep -q "^<" && cmp -s "$d/notes-before" "$d/benchmarks/NOTES.md" && grep -q "baselines/DS-v1.1/perf.json" "$d/benchmarks/README.md"`

## TC-010 — Two worked runs at once into one benchmarks root both succeed and never corrupt the inventory
- **Story**: US2 · **Traces to**: FR-003, FR-002
- **Given** a benchmarks root, and two identical minimal deterministic runs started at the same time pointed at it
- **When** both complete
- **Then** both succeed, and the inventory holds each artifact path at most once — no duplicated or interleaved rows from the concurrent appends
- **Pass condition**: `cd "$(mktemp -d)" && d="$(pwd)" && mkdir -p "$d/benchmarks" && printf '# Benchmarks\n' > "$d/benchmarks/README.md" && (cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/benchmarks" >"$d/a" 2>&1 & p1=$!; cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/benchmarks" >"$d/b" 2>&1 & p2=$!; wait "$p1" && wait "$p2") && test "$(grep -cE "worked/perf-" "$d/benchmarks/README.md")" -le 4 && ! sort "$d/benchmarks/README.md" | uniq -d | grep -q "worked/"`

## TC-011 — Re-running the manifest's command reproduces the run's deterministic numbers
- **Story**: US1 · **Traces to**: FR-001 (AC2)
- **Given** a completed worked run whose bundle carries the exact command line that produced it, from a run started with a repo-relative worked target
- **When** that recorded command is taken from the bundle and re-run from the same working directory
- **Then** it completes green, and the operation-count and estimated-token figures in the re-run's raw result equal the original's — only the advisory timing fields may drift
- **Pass condition**: `cd "$(mktemp -d)" && d="$(pwd)" && mkdir -p "$d/repo/benchmarks" && cd "$d/repo" && cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked benchmarks/worked >"$d/r1" 2>&1 && cmd=$(grep -rhoE "cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked [^\"']*" benchmarks/worked | head -1) && test -n "$cmd" && cp -r benchmarks/worked "$d/snap" && eval "$cmd" >"$d/r2" 2>&1 && python3 -c "import sys;exec(\"import json,glob,os,sys\nbad=0\nfor p in glob.glob(sys.argv[1]+'/**/*.json',recursive=True):\n q=os.path.join(sys.argv[2],os.path.relpath(p,sys.argv[1]))\n if not os.path.exists(q):\n  continue\n a=[]\n b=[]\n for t,o in ((a,json.load(open(p))),(b,json.load(open(q)))):\n  st=[('',o)]\n  while st:\n   k,v=st.pop()\n   if isinstance(v,dict):\n    st+=[(k+'.'+kk,vv) for kk,vv in v.items()]\n   elif isinstance(v,list):\n    st+=[(k+'.'+str(i),x) for i,x in enumerate(v)]\n   elif isinstance(v,(int,float)) and ('call' in k.lower() or 'est_token' in k.lower()):\n    t.append((k,v))\n  t.sort()\n if a and a!=b:\n  bad=1\nsys.exit(bad)\")" "$d/snap" benchmarks/worked`

## TC-012 — Standing guard: the manifest never carries absolute home paths or secrets
- **Story**: US1 · **Traces to**: NFR-002
- **Given** a hostile environment: the home directory is redirected to a sentinel path and token-carrying variables hold a sentinel secret, then a minimal deterministic run completes
- **When** the whole bundle is searched afterward
- **Then** the sentinel home path, the sentinel secret, and any absolute home-style path appear nowhere in the bundle — every recorded path is repo-relative or variable-templated (standing guard: fails if the manifest ever starts embedding the writer's home directory or environment secrets)
- **Pass condition**: `cd "$(mktemp -d)" && d="$(pwd)" && mkdir -p "$d/h" && HOME="$d/h" GH_TOKEN=sekrit-sentinel-9 CAIRN_TOKEN=sekrit-sentinel-9 cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --worked "$d/bundle" >/dev/null 2>&1 && ! grep -rqF "$d/h" "$d/bundle" && ! grep -rq "sekrit-sentinel-9" "$d/bundle" && ! grep -rqE "/Users/|/home/" "$d/bundle"`

## TC-013 — An unwritable worked target fails cleanly after the run, results still on stdout
- **Story**: US1 · **Traces to**: NFR-004
- **Given** a worked target path that cannot be written (an existing file blocks the directory)
- **When** a minimal deterministic run completes with `--worked` pointed at it
- **Then** the command exits non-zero with a clean failure naming the blocked target — no traceback — and the bench results were still reported on stdout: the run happened first, the bundle failure came after
- **Pass condition**: `cd "$(mktemp -d)" && d="$(pwd)" && touch "$d/obstacle" && if cairn bench --suite perf --n-files 8 --embed-backend hash --repeats 1 --json --worked "$d/obstacle" >"$d/out" 2>&1; then exit 1; fi && grep -q '"suite"' "$d/out" && grep -qF "$d/obstacle" "$d/out" && ! grep -qi "traceback" "$d/out"`

## Coverage matrix
<!-- Every FR and applicable NFR appears; `check.py` fails a requirement with no TC. -->
| Requirement | Test cases | Type (auto/manual) |
|-------------|------------|--------------------|
| FR-001      | TC-001, TC-002, TC-003, TC-011 | auto |
| FR-002      | TC-004, TC-005, TC-006, TC-010 | auto |
| FR-003      | TC-007, TC-008, TC-010 | auto |
| FR-004      | TC-009 | auto |
| NFR-002     | TC-012 | auto (standing guard) |
| NFR-004     | TC-013 | auto |

Not applicable (recorded in spec.md with reasons): NFR-001 (security), NFR-003 (performance), NFR-005 (observability), NFR-006 (accessibility) — no TCs required.

Boundary notes: the empty-input edge (target directory not yet existing) is pinned by TC-005; the concurrent-access edge by TC-010; the hostile-environment edge by TC-012; the unwritable-target edge by TC-013. The huge-corpus edge is deliberately out of this tier — the suite is determinism/structure only, and these cases intentionally use the minimal corpus; at-scale behavior remains the standing bench suite's concern, not this one.

AC trace: US1-AC1 → TC-001, TC-003 · US1-AC2 → TC-011 · US2-AC1 → TC-007, TC-008.
