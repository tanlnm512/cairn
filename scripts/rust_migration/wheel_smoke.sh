#!/usr/bin/env bash
# Wheel smoke: build (or take) the bin wheel, install it into a fresh venv, and
# probe the INSTALLED product.
#
# Usage: wheel_smoke.sh [WHEEL]
#   WHEEL   probe an already-built wheel (the release legs pass maturin's output);
#   no arg  first build the host wheel through the PEP 517 path a pip user hits
#           (`python -m build --wheel`: isolated build env, maturin backend).
#
# Probes (from the fresh venv, never the repo checkout):
#   1. the wheel is a platform-tagged bin wheel: py3-none-<platform>, no .py
#      entry, no Python package, the engine as the only script;
#   2. `cairn --version` exits 0 and reports the wheel's version;
#   3. `cairn config --json` is stdout-pure JSON with the C-4 store keys;
#   4. the installed `cairn` is the native engine (ELF/Mach-O/PE), not a #! shim;
#   5. `uv tool install` and `pipx install` of the wheel work when the tool is
#      on PATH (skipped otherwise).
#
# Exit 0 = all pass, 1 = fail.
set -uo pipefail

export PATH="$HOME/.cargo/bin:$PATH"

fail() { echo "wheel-smoke: FAIL — $*"; exit 1; }
skip() { echo "wheel-smoke: SKIP — $*"; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/cairn-wheel-smoke.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

# Driver interpreter: the repo venv when present, else the runner's python.
if [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PY="$REPO_ROOT/.venv/bin/python"
else
  PY="$(command -v python3 || command -v python)" || fail "no python interpreter on PATH"
fi

# --- the wheel: given, or built through PEP 517 ------------------------------
if [ $# -gt 0 ]; then
  [ $# -eq 1 ] || fail "expected exactly one wheel argument, got $#: $*"
  [ -f "$1" ] || fail "wheel not found: $1"
  WHEEL="$(cd "$(dirname "$1")" && pwd)/$(basename "$1")"
else
  "$PY" -m build --wheel --outdir "$WORK/dist" "$REPO_ROOT" || fail "wheel build (python -m build)"
  WHEEL="$(ls -t "$WORK"/dist/cairn_intel-*.whl 2>/dev/null | head -n1)"
  [ -n "$WHEEL" ] || fail "no wheel built into $WORK/dist"
fi
VERSION="$(basename "$WHEEL" | cut -d- -f2)"

"$PY" - "$WHEEL" <<'EOF' || fail "wheel is not a bin wheel"
import sys, zipfile
from pathlib import Path
path = Path(sys.argv[1])
dist, version, tags = path.name.split("-", 2)
assert dist == "cairn_intel", path.name
assert tags.startswith("py3-none-") and not path.name.endswith("-any.whl"), path.name
names = zipfile.ZipFile(path).namelist()
assert not [n for n in names if n.endswith(".py")], [n for n in names if n.endswith(".py")]
assert not [n for n in names if n.startswith("cairn/")], "python package inside the wheel"
exe = ".exe" if "win" in tags else ""
scripts = sorted(n for n in names if "/scripts/" in n)
assert scripts == [f"cairn_intel-{version}.data/scripts/cairn{exe}"], scripts
print("bin wheel OK:", path.name)
EOF

# --- fresh venv + install ------------------------------------------------------
"$PY" -m venv "$WORK/venv" || fail "fresh venv"
if [ -x "$WORK/venv/bin/python" ]; then VENV_BIN="$WORK/venv/bin"; else VENV_BIN="$WORK/venv/Scripts"; fi
VENV_PY="$VENV_BIN/python"
"$VENV_PY" -m pip install --quiet --disable-pip-version-check "$WHEEL" || fail "wheel install"
CAIRN="$(ls "$VENV_BIN"/cairn "$VENV_BIN"/cairn.exe 2>/dev/null | head -n1)"
[ -n "$CAIRN" ] && [ -x "$CAIRN" ] || fail "cairn executable missing after install in $VENV_BIN"

export CAIRN_HOME="$WORK/home"
cd "$WORK"

# --- probe the INSTALLED product ----------------------------------------------
out="$("$CAIRN" --version)" || fail "cairn --version"
case "$out" in
  *"$VERSION"*) echo "version OK: $out" ;;
  *) fail "cairn --version reported '$out', wheel is $VERSION" ;;
esac

"$VENV_PY" - "$CAIRN" <<'EOF' || fail "cairn config --json machine contract (C-4)"
import json, subprocess, sys
r = subprocess.run([sys.argv[1], "config", "--json"], capture_output=True, text=True)
assert r.returncode == 0, r.stderr
data = json.loads(r.stdout)  # stdout must be pure JSON
assert set(data) == {"cairn_home", "workspace", "db", "knowledge", "scip"}, sorted(data)
print("config --json OK:", r.stdout.strip())
EOF

"$PY" - "$CAIRN" <<'EOF' || fail "installed cairn is not the native engine"
import sys
head = open(sys.argv[1], "rb").read(4)
assert not head.startswith(b"#!"), "a #! launcher script, not the engine"
macho = {b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe"}
assert head == b"\x7fELF" or head in macho or head[:2] == b"MZ", head
print("native executable OK:", sys.argv[1])
EOF

# --- tool installers ------------------------------------------------------------
if command -v uv >/dev/null 2>&1; then
  UV_TOOL_DIR="$WORK/uv-tools" UV_TOOL_BIN_DIR="$WORK/uv-bin" \
    uv tool install --python "$VENV_PY" "$WHEEL" || fail "uv tool install"
  "$WORK/uv-bin/cairn" --version >/dev/null || fail "uv tool cairn --version"
  echo "uv tool install OK"
else
  skip "uv not on PATH, uv tool install leg"
fi

if command -v pipx >/dev/null 2>&1; then
  PIPX_HOME="$WORK/pipx" PIPX_BIN_DIR="$WORK/pipx-bin" \
    pipx install --python "$VENV_PY" "$WHEEL" >/dev/null || fail "pipx install"
  "$WORK/pipx-bin/cairn" --version >/dev/null || fail "pipx cairn --version"
  echo "pipx install OK"
else
  skip "pipx not on PATH, pipx install leg"
fi

echo "wheel-smoke: PASS ($WHEEL)"
