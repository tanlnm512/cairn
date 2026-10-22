#!/usr/bin/env bash
# G2 gate (rust-migration 2c): one corpus, two fresh stores, canonical dump diff.
#
# Builds CORPUS (default: the cairn self-store source, i.e. this repo) into two
# scratch stores — Python `cairn build` and the Rust `cairn build-graph` — then
# runs kg_dump.py on both, normalizes volatile content at dump time (plan.md 2c:
# "id determinism handled at dump time"), and requires kg_diff.py to be empty.
# Prints per-stage timings for both builds. Exit 0 = gate pass, 1 = fail.
#
# Normalization is symmetric and content-derived, applied to BOTH dumps:
#   - dropped sections: build_runs / events / tool_metrics (run telemetry) and
#     dataflow (CLI post-build derived index, outside the Phase 2 build path);
#   - uuid/counter row ids rewritten to content keys (files/symbols/edges/
#     imports/rationale/parse_errors/skipped_files);
#   - volatile stamps blanked: indexed_at / recorded_at / timestamp -> <TS>,
#     parse_errors.stack_trace -> <TRACE>.
# The Python leg runs with --no-lsp: the LSP ambiguous-edge upgrade is not part
# of the Phase 2 build path either side ports.
#
# G2_LEGS selects comma-separated legs (default "base"):
#   base     - the python vs rust fresh-store comparison above
#   rebuild  - rust `cairn build` twice into one store; dump must equal a fresh build
#   staging  - rust `cairn build --staging`; live db must exist, no .tmp left, dump must equal a fresh build
set -uo pipefail

export PATH="$HOME/.cargo/bin:$PATH"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CORPUS="${1:-$REPO_ROOT}"
WORKERS="${CAIRN_WORKERS:-$(sysctl -n hw.ncpu 2>/dev/null || echo 8)}"
PY="$REPO_ROOT/.venv/bin/python"
KG_DUMP="$REPO_ROOT/scripts/rust_migration/kg_dump.py"
KG_DIFF="$REPO_ROOT/scripts/rust_migration/kg_diff.py"
RS_CAIRN="$REPO_ROOT/rust/target/release/cairn"
WORK="$(mktemp -d /tmp/cairn-g2.XXXXXX)"
trap 'rm -rf "$WORK"' EXIT

fail() { echo "G2: FAIL — $*"; exit 1; }
leg_on() { [[ ",$LEGS," == *",$1,"* ]]; }

parse_legs() {
  local token
  local -a requested selected=()
  IFS=',' read -ra requested <<< "${G2_LEGS:-base}"
  for token in "${requested[@]}"; do
    token="${token//[[:space:]]/}"
    [ -n "$token" ] || continue
    case " base rebuild staging " in
      *" $token "*) selected+=("$token") ;;
      *) echo "G2: FAIL — unknown leg '$token' (known: base rebuild staging)"; return 1 ;;
    esac
  done
  [ "${#selected[@]}" -gt 0 ] || { echo "G2: FAIL — no legs selected in G2_LEGS='${G2_LEGS}'"; return 1; }
  LEGS=",$(IFS=,; echo "${selected[*]}"),"
}

parse_legs || exit 1
[ -x "$PY" ] || fail "repo venv python not found at $PY"
[ -d "$CORPUS" ] || fail "corpus not a directory: $CORPUS"

# --- rust binary -------------------------------------------------------------
if [ ! -x "$RS_CAIRN" ]; then
  echo "== building rust cairn binary =="
  cargo build --release --manifest-path "$REPO_ROOT/rust/Cargo.toml" -p cairn-cli \
    || fail "cargo build -p cairn-cli"
fi
[ -x "$RS_CAIRN" ] || fail "rust binary missing at $RS_CAIRN"

# --- dump-time normalization (plan.md 2c: id determinism at dump time) -------
normalize() {
  "$PY" - "$1" "$2" <<'PYNORMALIZE'
"""Rewrite volatile content in a canonical dump: content-keyed ids, blank stamps."""
import sys
from collections import defaultdict

src, dst = sys.argv[1], sys.argv[2]
DROP_TABLES = {"build_runs", "events", "tool_metrics", "dataflow"}
TS = "<TS>"

sections = []  # (table or None, header lines, rows as cell lists)
current, header, rows = None, [], []
with open(src, encoding="utf-8") as fh:
    for line in fh.read().split("\n"):
        if line.startswith("[table ") and line.endswith("]"):
            if current is not None:
                sections.append((current, header, rows))
            current, header, rows = line[len("[table "):-1], [], []
        elif line.startswith("# columns: ") and current is not None:
            header = line[len("# columns: "):].split("\t")
        elif current is not None and line:
            cells = line.split("\t")
            if cells[0].startswith("\\[table "):
                cells[0] = cells[0][1:]
            rows.append(cells)
if current is not None:
    sections.append((current, header, rows))

def col(header, name):
    return header.index(name) if name in header else None

# Content keys per table, applied identically to both dumps.
fkey = {}   # files.id -> key
skey = {}   # symbols.id -> key
dupes = defaultdict(int)

def ordinal(key):
    n = dupes[key]
    dupes[key] += 1
    return key if n == 0 else f"{key}#{n}"

by_table = {t: (h, r) for t, h, r in sections}
if "files" in by_table:
    h, rows = by_table["files"]
    for r in rows:
        fkey[r[0]] = ordinal("@F|" + r[col(h, "repo_id")] + "|" + r[col(h, "path")])
if "symbols" in by_table:
    h, rows = by_table["symbols"]
    for r in rows:
        skey[r[0]] = ordinal(
            "@S|" + fkey.get(r[col(h, "file_id")], r[col(h, "file_id")])
            + "|" + r[col(h, "name")] + "|" + r[col(h, "kind")]
            + "|" + r[col(h, "line_start")] + "|" + r[col(h, "line_end")]
            + "|" + r[col(h, "column_start")] + "|" + r[col(h, "column_end")]
        )

def ref(table, header, r, name):
    i = col(header, name)
    if i is None:
        return r
    raw = r[i]
    if raw == "\\N":
        return r
    mapped = (skey if name in ("source_id", "target_id", "symbol_id") else fkey).get(raw, raw)
    r[i] = mapped
    return r

out = ["# cairn-canonical-dump 1"]
dupes.clear()
for table, header, rows in sections:
    if table in DROP_TABLES:
        continue
    out.append(f"[table {table}]")
    out.append("# columns: " + "\t".join(header))
    if not header:
        out.extend("\t".join(r) for r in sorted(rows))
        continue
    stamped = col(header, "indexed_at")
    if stamped is None:
        stamped = col(header, "recorded_at")
    if stamped is None:
        stamped = col(header, "timestamp")
    trace = col(header, "stack_trace")
    idcol = col(header, "id")
    rewritten = []
    for r in rows:
        r = list(r)
        if stamped is not None:
            r[stamped] = TS
        if trace is not None:
            r[trace] = "<TRACE>"
        if table == "files":
            r[idcol] = fkey.get(r[idcol], r[idcol])
        elif table == "symbols":
            r = ref(table, header, r, "file_id")
            r[idcol] = skey.get(r[idcol], r[idcol])
        elif table == "edges":
            raw_source = r[col(header, "source_id")]
            raw_target = r[col(header, "target_id")]
            tname = r[col(header, "target_name")]
            r = ref(table, header, r, "source_id")
            r = ref(table, header, r, "target_id")
            target = (
                skey.get(raw_target, raw_target) if raw_target != "\\N"
                else ("n|" + tname if tname != "\\N" else "nn")
            )
            r[idcol] = ordinal(
                "@E|" + r[col(header, "source_id")] + "|" + r[col(header, "kind")]
                + "|" + r[col(header, "line")] + "|" + r[col(header, "column")] + "|" + target
            )
        elif table == "imports":
            r = ref(table, header, r, "file_id")
            r[idcol] = ordinal(
                "@I|" + r[col(header, "file_id")] + "|" + r[col(header, "imported_path")]
                + "|" + r[col(header, "line")] + "|" + r[col(header, "local_alias")]
            )
        elif table == "parse_errors":
            r[idcol] = ordinal("@P|" + r[col(header, "repo_id")] + "|" + r[col(header, "file_path")])
        elif table == "skipped_files":
            r[idcol] = ordinal(
                "@SK|" + r[col(header, "repo_id")] + "|" + r[col(header, "path")]
                + "|" + r[col(header, "reason")]
            )
        elif table == "rationale":
            r = ref(table, header, r, "file_id")
            r = ref(table, header, r, "symbol_id")
            r[idcol] = ordinal(
                "@R|" + r[col(header, "file_id")] + "|" + r[col(header, "line")]
                + "|" + r[col(header, "kind")] + "|" + r[col(header, "text")]
            )
        rewritten.append("\t".join(r))
    out.extend(sorted(rewritten))
with open(dst, "w", encoding="utf-8") as fh:
    fh.write("\n".join(out) + "\n")
print(f"normalized {src} -> {dst} ({len(out)} lines)", file=sys.stderr)
PYNORMALIZE
}

dump_normalized() {
  "$PY" "$KG_DUMP" "$1" -o "$WORK/$2.dump" || return 1
  normalize "$WORK/$2.dump" "$WORK/$2.norm"
}

# --- leg base: python vs rust fresh stores -----------------------------------
leg_base() {
  # --- python build ----------------------------------------------------------
  echo "== python cairn build (workers=$WORKERS) =="
  local t0 t1 PY_WALL RS_WALL RS_TIMINGS RS_SUMMARY
  t0=$(date +%s.%N)
  CAIRN_WORKERS="$WORKERS" "$PY" -m cairn.cli build \
    --workspace "$CORPUS" --db "$WORK/py.kg" --no-lsp \
    > "$WORK/py.build.log" 2>&1 \
    || { tail -20 "$WORK/py.build.log"; echo "G2 base: FAIL — python cairn build failed"; return 1; }
  t1=$(date +%s.%N)
  PY_WALL=$("$PY" -c "print(f'{$t1 - $t0:.3f}')")
  "$PY" - "$WORK/py.kg" <<'PY' > "$WORK/py.phases.json" || { echo "G2 base: FAIL — phase-timing read failed"; return 1; }
import json, sqlite3, sys
conn = sqlite3.connect(sys.argv[1])
row = conn.execute(
    "SELECT duration_s, phase_timings FROM build_runs ORDER BY id DESC LIMIT 1"
).fetchone()
print(json.dumps({
    "wall_s": row[0] if row else None,
    "phases": json.loads(row[1]) if row and row[1] else {},
}))
PY
  echo "python build wall: ${PY_WALL}s  phases: $(cat "$WORK/py.phases.json")"

  # --- rust build ------------------------------------------------------------
  echo "== rust cairn build-graph =="
  t0=$(date +%s.%N)
  CAIRN_WORKERS="$WORKERS" "$RS_CAIRN" build-graph \
    --workspace "$CORPUS" --db "$WORK/rs.kg" \
    > "$WORK/rs.build.log" 2>&1 \
    || { tail -20 "$WORK/rs.build.log"; echo "G2 base: FAIL — rust build-graph failed"; return 1; }
  t1=$(date +%s.%N)
  RS_WALL=$("$PY" -c "print(f'{$t1 - $t0:.3f}')")
  RS_TIMINGS=$(grep "^timings:" "$WORK/rs.build.log" | tail -1)
  RS_SUMMARY=$(grep '^{"repos"' "$WORK/rs.build.log" | tail -1)
  echo "rust build wall: ${RS_WALL}s  $RS_SUMMARY"
  echo "  $RS_TIMINGS"

  # --- canonical dumps + the gate --------------------------------------------
  dump_normalized "$WORK/py.kg" py || { echo "G2 base: FAIL — kg_dump / normalization (python store)"; return 1; }
  dump_normalized "$WORK/rs.kg" rs || { echo "G2 base: FAIL — kg_dump / normalization (rust store)"; return 1; }
  "$PY" "$KG_DIFF" "$WORK/py.norm" "$WORK/rs.norm"
  local diff=$?
  echo ""
  if [ "$diff" -eq 0 ]; then
    echo "G2 base: PASS — python-built and rust-built dumps content-equal (corpus: $CORPUS)"
    return 0
  fi
  echo "G2 base: FAIL — canonical dumps differ (kg_diff exit $diff)"
  return 1
}

# --- legs rebuild / staging: rust-only, compared against a fresh rust build --
fresh_baseline() {
  [ -f "$WORK/fresh.norm" ] && return 0
  "$RS_CAIRN" build --workspace "$CORPUS" --db "$WORK/fresh.kg" \
    > "$WORK/fresh.build.log" 2>&1 \
    || { tail -20 "$WORK/fresh.build.log"; echo "G2 fresh baseline: FAIL — build failed"; return 1; }
  dump_normalized "$WORK/fresh.kg" fresh || { echo "G2 fresh baseline: FAIL — dump/normalization"; return 1; }
}

compare_to_fresh() {
  "$PY" "$KG_DIFF" "$WORK/fresh.norm" "$WORK/$1.norm"
}

leg_rebuild() {
  echo "== leg rebuild: rust cairn build twice into one store =="
  fresh_baseline || return 1
  local db="$WORK/rebuild.kg" pass
  for pass in 1 2; do
    "$RS_CAIRN" build --workspace "$CORPUS" --db "$db" \
      > "$WORK/rebuild.$pass.log" 2>&1 \
      || { tail -20 "$WORK/rebuild.$pass.log"; echo "G2 rebuild: FAIL — build pass $pass exited non-zero"; return 1; }
  done
  dump_normalized "$db" rebuild || { echo "G2 rebuild: FAIL — dump/normalization"; return 1; }
  if compare_to_fresh rebuild; then
    echo "G2 rebuild: PASS"
    return 0
  fi
  echo "G2 rebuild: FAIL — rebuilt store differs from a fresh build"
  return 1
}

leg_staging() {
  echo "== leg staging: rust cairn build --staging =="
  fresh_baseline || return 1
  local db="$WORK/staged.kg" problems=0
  "$RS_CAIRN" build --staging --workspace "$CORPUS" --db "$db" \
    > "$WORK/staged.build.log" 2>&1 \
    || { tail -20 "$WORK/staged.build.log"; echo "G2 staging: FAIL — build --staging exited non-zero"; return 1; }
  if [ -e "$db.tmp" ]; then
    echo "G2 staging: FAIL — staging file left behind: $db.tmp"
    problems=1
  fi
  if [ ! -f "$db" ]; then
    echo "G2 staging: FAIL — live db not written: $db"
    problems=1
  fi
  [ "$problems" -eq 0 ] || return 1
  dump_normalized "$db" staged || { echo "G2 staging: FAIL — dump/normalization"; return 1; }
  if compare_to_fresh staged; then
    echo "G2 staging: PASS"
    return 0
  fi
  echo "G2 staging: FAIL — live db differs from a fresh build"
  return 1
}

# --- run selected legs -------------------------------------------------------
status=0
if leg_on base; then
  leg_base || status=1
fi
if leg_on rebuild; then
  leg_rebuild || status=1
fi
if leg_on staging; then
  leg_staging || status=1
fi
exit "$status"
