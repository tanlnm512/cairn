#!/usr/bin/env bash
# G2 gate (rust-migration 2c): canonical dump diff of a fresh Rust store
# against the frozen Python-recorded reference (the Rust goldens are the
# spec of record; no Python engine is driven).
#
# base: builds the pinned parity corpus (rust/golden/stores/self/corpus) with
# the Rust `cairn build-graph`, runs kg_dump.py, normalizes volatile content
# at dump time (plan.md 2c: "id determinism handled at dump time"), and
# requires kg_diff.py against rust/golden/dump/self-corpus.norm.txt to be
# empty. Prints the build timings.
#
# Normalization is symmetric and content-derived, applied to BOTH sides
# (kg_normalize.py):
#   - dropped sections: build_runs / events / tool_metrics (run telemetry) and
#     dataflow (CLI post-build derived index, outside the Phase 2 build path);
#   - uuid/counter row ids rewritten to content keys (files/symbols/edges/
#     imports/rationale/parse_errors/skipped_files);
#   - volatile stamps blanked: indexed_at / recorded_at / timestamp -> <TS>,
#     parse_errors.stack_trace -> <TRACE>.
#
# G2_LEGS selects comma-separated legs (default "base"):
#   base     - the rust-vs-frozen-reference comparison above
#   rebuild  - rust `cairn build` twice into one store; dump must equal a fresh build
#   staging  - rust `cairn build --staging`; live db must exist, no .tmp left, dump must equal a fresh build
# CORPUS (arg 1, default: the repo root) feeds only the rebuild/staging legs.
set -uo pipefail

export PATH="$HOME/.cargo/bin:$PATH"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CORPUS="${1:-$REPO_ROOT}"
WORKERS="${CAIRN_WORKERS:-$(sysctl -n hw.ncpu 2>/dev/null || echo 8)}"
# Repo venv when present, else the active/PATH interpreter (containers).
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PY="$REPO_ROOT/.venv/bin/python"
elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
  PY="$VIRTUAL_ENV/bin/python"
else
  PY="$(command -v python3 || command -v python)" || { echo "G2: no python interpreter" >&2; exit 1; }
fi
KG_DUMP="$REPO_ROOT/scripts/rust_migration/kg_dump.py"
KG_NORMALIZE="$REPO_ROOT/scripts/rust_migration/kg_normalize.py"
KG_DIFF="$REPO_ROOT/scripts/rust_migration/kg_diff.py"
FROZEN_NORM="$REPO_ROOT/rust/golden/dump/self-corpus.norm.txt"
PINNED_CORPUS="$REPO_ROOT/rust/golden/stores/self/corpus"
RS_CAIRN="${CAIRN_RUST_BIN:-$REPO_ROOT/rust/target/release/cairn}"
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
  cargo build --release --manifest-path "$REPO_ROOT/Cargo.toml" -p cairn-cli \
    || fail "cargo build -p cairn-cli"
fi
[ -x "$RS_CAIRN" ] || fail "rust binary missing at $RS_CAIRN"

# --- dump-time normalization (plan.md 2c: id determinism at dump time) -------
normalize() {
  "$PY" "$KG_NORMALIZE" "$1" "$2"
}

dump_normalized() {
  # $3 (optional workspace) maps the staged corpus prefix to <WORKSPACE> so the
  # dump never bakes in this machine's scratch path. Branch, not an empty-array
  # expansion: bash 3.2 treats that as unbound under set -u.
  if [ -n "${3:-}" ]; then
    "$PY" "$KG_DUMP" "$1" --map-path "$3=<WORKSPACE>" -o "$WORK/$2.dump" || return 1
  else
    "$PY" "$KG_DUMP" "$1" -o "$WORK/$2.dump" || return 1
  fi
  normalize "$WORK/$2.dump" "$WORK/$2.norm"
}

stage_pinned_corpus() {
  # git cannot track a `.git` path, so the committed corpus ships without the
  # repo marker the scanners require; stage a copy with the marker restored.
  local dst="$WORK/corpus"
  [ -d "$dst" ] && return 0
  cp -R "$PINNED_CORPUS" "$dst" || return 1
  printf 'gitdir: pinned-parity-corpus\n' > "$dst/.git"
}

# --- leg base: rust fresh store vs the frozen python-recorded reference ------
leg_base() {
  [ -f "$FROZEN_NORM" ] || { echo "G2 base: FAIL — frozen reference missing: $FROZEN_NORM"; return 1; }
  [ -d "$PINNED_CORPUS" ] || { echo "G2 base: FAIL — pinned corpus missing: $PINNED_CORPUS"; return 1; }
  stage_pinned_corpus || { echo "G2 base: FAIL — corpus staging"; return 1; }
  echo "== rust cairn build-graph over the pinned self corpus (workers=$WORKERS) =="
  local t0 t1 RS_WALL RS_TIMINGS RS_SUMMARY
  t0=$(date +%s.%N)
  CAIRN_WORKERS="$WORKERS" "$RS_CAIRN" build-graph \
    --workspace "$WORK/corpus" --db "$WORK/rs.kg" \
    > "$WORK/rs.build.log" 2>&1 \
    || { tail -20 "$WORK/rs.build.log"; echo "G2 base: FAIL — rust build-graph failed"; return 1; }
  t1=$(date +%s.%N)
  RS_WALL=$("$PY" -c "print(f'{$t1 - $t0:.3f}')")
  RS_TIMINGS=$(grep "^timings:" "$WORK/rs.build.log" | tail -1)
  RS_SUMMARY=$(grep '^{"repos"' "$WORK/rs.build.log" | tail -1)
  echo "rust build wall: ${RS_WALL}s  $RS_SUMMARY"
  echo "  $RS_TIMINGS"

  dump_normalized "$WORK/rs.kg" rs "$WORK/corpus" || { echo "G2 base: FAIL — kg_dump / normalization (rust store)"; return 1; }
  "$PY" "$KG_DIFF" "$FROZEN_NORM" "$WORK/rs.norm"
  local diff=$?
  echo ""
  if [ "$diff" -eq 0 ]; then
    echo "G2 base: PASS — rust-built dump content-equal to the frozen python-recorded reference"
    return 0
  fi
  echo "G2 base: FAIL — canonical dump differs from the frozen reference (kg_diff exit $diff)"
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
