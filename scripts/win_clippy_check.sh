#!/usr/bin/env bash
# win_clippy_check.sh -- lint the x86_64-pc-windows-msvc cfg arms from macOS/Linux.
#
# The Windows-only #[cfg] code is invisible to host clippy, so only the
# windows-latest CI leg catches lints there. This runs cargo clippy (and
# cargo check) for the Windows target using stub cross C tools: clippy never
# links, so the sqlite/tree-sitter C sources only need to "compile" into
# empty objects for the build scripts to succeed.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET=x86_64-pc-windows-msvc
STUBS="$REPO_ROOT/.cache/win-cross-stubs"
mkdir -p "$STUBS"

cat > "$STUBS/fake-cc" <<'STUB'
#!/bin/sh
out=""; prev=""
for a in "$@"; do [ "$prev" = "-o" ] && out="$a"; prev="$a"; done
[ -n "$out" ] && : > "$out" 2>/dev/null
exit 0
STUB
cat > "$STUBS/fake-lib" <<'STUB'
#!/bin/sh
out=""
case "$1" in rcs|rc) out="$2" ;; esac
for a in "$@"; do
  case "$a" in /OUT:*|/out:*|-out:*) out="${a#*:}" ;; esac
done
[ -n "$out" ] && : > "$out" 2>/dev/null
exit 0
STUB
chmod +x "$STUBS/fake-cc" "$STUBS/fake-lib"

export PATH="$HOME/.cargo/bin:$PATH"
rustup target list --installed | grep -qx "$TARGET" || rustup target add "$TARGET"

cd "$REPO_ROOT"
env "CC_x86_64_pc_windows_msvc=$STUBS/fake-cc" \
    "AR_x86_64_pc_windows_msvc=$STUBS/fake-lib" \
    cargo clippy --target "$TARGET" --workspace --all-targets -- -D warnings
echo "windows-target clippy clean"
