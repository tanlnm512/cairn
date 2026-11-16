#!/usr/bin/env bash
# Wheel smoke: build (or take) the bin wheel, install it into a fresh venv, and
# probe the installed `cairn` binary.
#
# Usage: wheel-smoke.sh [WHEEL]
#   WHEEL   probe an already-built wheel;
#   no arg  build the host wheel via `python -m build --wheel`.
set -uo pipefail

export PATH="$HOME/.cargo/bin:$PATH"

fail() { echo "wheel-smoke: FAIL — $*"; exit 1; }
skip() { echo "wheel-smoke: SKIP — $*"; }

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/cairn-wheel-smoke.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
  PY="$VIRTUAL_ENV/bin/python"
elif [ -x "$REPO_ROOT/.venv/bin/python" ]; then
  PY="$REPO_ROOT/.venv/bin/python"
else
  PY="$(command -v python3 || command -v python)" || fail "no python interpreter on PATH"
fi

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

wheel_names() {
  if command -v unzip >/dev/null 2>&1; then
    unzip -Z1 "$1"
  elif command -v 7z >/dev/null 2>&1; then
    7z l -ba -slt "$1" | sed -n 's/^Path = //p' | tr '\\' /
  else
    fail "neither unzip nor 7z on PATH to list the wheel"
  fi
}

WHEEL_NAME="$(basename "$WHEEL")"
TAGS="${WHEEL_NAME#cairn_intel-"$VERSION"-}"
[ "${WHEEL_NAME%%-*}" = cairn_intel ] || fail "wheel is not cairn_intel: $WHEEL_NAME"
case "$TAGS" in
  py3-none-any.whl) fail "wheel is pure-python, not a bin wheel: $WHEEL_NAME" ;;
  py3-none-*) ;;
  *) fail "wheel is not tagged py3-none-<platform>: $WHEEL_NAME" ;;
esac
NAMES="$(wheel_names "$WHEEL" | tr -d '\r')" || fail "cannot list $WHEEL_NAME"
PY_ENTRIES="$(printf '%s\n' "$NAMES" | grep -E '\.py$|^cairn/' || true)"
[ -z "$PY_ENTRIES" ] || fail "python entries inside the wheel: $PY_ENTRIES"
case "$TAGS" in *win*) EXE=.exe ;; *) EXE= ;; esac
SCRIPTS="$(printf '%s\n' "$NAMES" | grep '/scripts/' || true)"
[ "$SCRIPTS" = "cairn_intel-$VERSION.data/scripts/cairn$EXE" ] \
  || fail "the engine must be the only script, got: $SCRIPTS"
echo "bin wheel OK: $WHEEL_NAME"

"$PY" -m venv "$WORK/venv" || fail "fresh venv"
if [ -x "$WORK/venv/bin/python" ]; then VENV_BIN="$WORK/venv/bin"; else VENV_BIN="$WORK/venv/Scripts"; fi
if [ -e "$VENV_BIN/python.exe" ]; then
  VENV_PY="$VENV_BIN/python.exe"
else
  VENV_PY="$VENV_BIN/python"
fi
[ -x "$VENV_PY" ] || fail "venv python missing in $VENV_BIN"
"$VENV_PY" -m pip install --quiet --disable-pip-version-check "$WHEEL" || fail "wheel install"
if [ -e "$VENV_BIN/cairn.exe" ]; then
  CAIRN="$VENV_BIN/cairn.exe"
else
  CAIRN="$VENV_BIN/cairn"
fi
[ -n "$CAIRN" ] && [ -x "$CAIRN" ] || fail "cairn executable missing after install in $VENV_BIN"

export CAIRN_HOME="$WORK/home"
cd "$WORK"

out="$("$CAIRN" --version)" || fail "cairn --version"
case "$out" in
  *"$VERSION"*) echo "version OK: $out" ;;
  *) fail "cairn --version reported '$out', wheel is $VERSION" ;;
esac

config="$("$CAIRN" config --json)" || fail "cairn config --json"
printf '%s' "$config" \
  | jq -es 'length == 1 and (.[0] | keys) == ["cairn_home", "db", "knowledge", "scip", "workspace"]' >/dev/null \
  || fail "cairn config --json machine contract (C-4): $config"
echo "config --json OK: $config"

MAGIC="$(head -c 4 "$CAIRN" | od -An -tx1 | tr -d ' \n')"
case "$MAGIC" in
  2321*) fail "installed cairn is a #! launcher script, not the engine" ;;
  7f454c46 | cffaedfe | feedfacf | cefaedfe | feedface | cafebabe | 4d5a*) ;;
  *) fail "installed cairn is not a native executable (magic $MAGIC)" ;;
esac
echo "native executable OK: $CAIRN"

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
