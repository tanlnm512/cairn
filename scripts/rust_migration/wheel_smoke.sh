#!/usr/bin/env bash
# Wheel smoke (3c): build the release binary, inject it as package data, build
# the wheel, install into a fresh venv, and probe the INSTALLED product.
#
# Probes (from the fresh venv, never the repo checkout):
#   1. `cairn --version` exits 0;
#   2. `cairn config --json` is stdout-pure JSON with the C-4 store keys;
#   3. the bundled binary is discoverable: daemon_impl() defaults to rust and
#      rust_bin() resolves to <site-packages>/cairn/_bin/cairn, which also runs.
#   4. the console script execs the resolved binary: CAIRN_RUST_BIN pinned to
#      a marker script, the original argv and exit code come back through it.
#
# Exit 0 = all pass, 1 = fail. Removes the injected binary on exit so the
# source tree returns to a source-checkout state.
set -uo pipefail

export PATH="$HOME/.cargo/bin:$PATH"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PY="$REPO_ROOT/.venv/bin/python"
BIN_DIR="$REPO_ROOT/src/cairn/_bin"
BIN_DST="$BIN_DIR/cairn"
RS_BIN="$REPO_ROOT/rust/target/release/cairn"
WORK="$(mktemp -d /tmp/cairn-wheel-smoke.XXXXXX)"
trap 'rm -rf "$WORK"; rm -f "$BIN_DST"' EXIT

fail() { echo "wheel-smoke: FAIL — $*"; exit 1; }

[ -x "$PY" ] || fail "repo venv python not found at $PY"

# --- rust release binary ------------------------------------------------------
cargo build --release --manifest-path "$REPO_ROOT/rust/Cargo.toml" -p cairn-cli \
  || fail "cargo build -p cairn-cli"
[ -f "$RS_BIN" ] || fail "release binary missing: $RS_BIN"

# --- inject as package data + build the wheel ---------------------------------
mkdir -p "$BIN_DIR"
cp "$RS_BIN" "$BIN_DST"
"$PY" -m build --wheel --outdir "$WORK/dist" "$REPO_ROOT" \
  || fail "wheel build"
WHEEL="$(ls -t "$WORK"/dist/cairn_intel-*.whl 2>/dev/null | head -n1)"
[ -n "$WHEEL" ] || fail "no wheel built into $WORK/dist"

"$PY" - "$WHEEL" <<'EOF' || fail "wheel does not bundle cairn/_bin/cairn"
import sys, zipfile
names = zipfile.ZipFile(sys.argv[1]).namelist()
assert "cairn/_bin/cairn" in names, [n for n in names if "_bin" in n or n.endswith("cairn")]
EOF

# --- fresh venv + install -----------------------------------------------------
"$PY" -m venv "$WORK/venv" || fail "fresh venv"
VENV_PY="$WORK/venv/bin/python"
"$VENV_PY" -m pip install --quiet "$WHEEL" || fail "wheel install"
CAIRN="$WORK/venv/bin/cairn"
[ -x "$CAIRN" ] || fail "console script missing after install: $CAIRN"

export CAIRN_HOME="$WORK/home"
cd "$WORK"

# --- probe the INSTALLED product ----------------------------------------------
"$CAIRN" --version || fail "cairn --version"

"$VENV_PY" - "$CAIRN" <<'EOF' || fail "cairn config --json machine contract (C-4)"
import json, subprocess, sys
r = subprocess.run([sys.argv[1], "config", "--json"], capture_output=True, text=True)
assert r.returncode == 0, r.stderr
data = json.loads(r.stdout)  # stdout must be pure JSON
assert set(data) == {"cairn_home", "workspace", "db", "knowledge", "scip"}, sorted(data)
print("config --json OK:", r.stdout.strip())
EOF

"$VENV_PY" - <<'EOF' || fail "bundled-binary discovery probe"
import subprocess
from cairn import paths
from cairn.mcp_server import lifecycle

impl = lifecycle.daemon_impl()
assert impl == "rust", f"daemon_impl()={impl!r}, expected rust"
bundled = lifecycle.rust_bin()
expected = str(paths.BUNDLED_BIN_DIR / "cairn")
assert bundled == expected, f"rust_bin()={bundled!r}, expected {expected!r}"
r = subprocess.run([bundled, "--version"], capture_output=True, text=True)
assert r.returncode == 0, r.stderr
print(f"discovery OK: impl=rust bundled binary runs ({r.stdout.strip() or bundled})")
EOF

# --- console-script exec shim (4d-2) -------------------------------------------
FAKE="$WORK/fake-engine"
printf '#!/bin/sh\necho EXEC-SHIM "$@"\nexit 7\n' > "$FAKE"
chmod +x "$FAKE"
for args in "--version" "config --json"; do
  out="$(CAIRN_RUST_BIN="$FAKE" "$CAIR" $args 2>/dev/null)"
  code=$?
  [ "$out" = "EXEC-SHIM $args" ] \
    || fail "console script did not exec the resolved binary for '$args' (got: $out)"
  [ "$code" -eq 7 ] \
    || fail "console script lost the exec'd binary exit code for '$args' (got $code)"
done

echo "wheel-smoke: PASS ($WHEEL)"
