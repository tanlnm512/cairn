#!/bin/sh
# Regenerate the on-demand-paths fixture workspaces in place (idempotent).
set -e
cd "$(dirname "$0")"

cat > chain/chain.py <<'EOF'
"""Fixture: linear call chain for shortest-path ordering."""


def chain_a():
    return chain_b()


def chain_b():
    return chain_c()


def chain_c():
    return chain_d()


def chain_d():
    return None
EOF

cat > islands/islands.py <<'EOF'
"""Fixture: two disconnected components for no-path checks."""


def isl_a1():
    return isl_a2()


def isl_a2():
    return None


def isl_b1():
    return isl_b2()


def isl_b2():
    return None
EOF

cat > fork/fork.py <<'EOF'
"""Fixture: two equal-length routes through distinct middle hops."""


def hub_top():
    return mid_left() + mid_right()


def mid_left():
    return hub_sink()


def mid_right():
    return hub_sink()


def hub_sink():
    return 0
EOF

{
  echo '"""Fixture: 60 same-depth pairs for the printed-path cap."""'
  i=0
  while [ "$i" -lt 60 ]; do
    printf '\n\ndef fan_%s():\n    return fan_sink()\n' "$i"
    i=$((i + 1))
  done
  printf '\n\ndef fan_sink():\n    return 0\n'
} > fanout/fanout.py

cat > fuzzy/fuzzy.py <<'EOF'
"""Fixture: ambiguous name hop bridged only under fuzzy."""


def fz_start():
    return fz_shared()


def fz_end():
    return None
EOF

cat > fuzzy/fuzzy_main.py <<'EOF'
"""First definition of the ambiguous fz_shared hop."""


def fz_shared():
    return fz_end()
EOF

cat > fuzzy/fuzzy_alt.py <<'EOF'
"""Second definition of the ambiguous fz_shared hop."""


def fz_shared():
    return fz_end()
EOF

cat > update/update.py <<'EOF'
"""Fixture: mutable two-symbol chain for update-cycle tests."""


def upd_a():
    return upd_b()


def upd_b():
    return None
EOF

cat > depth/depth.py <<'EOF'
"""Fixture: entry-to-target diamond for depth-bound checks."""


def dep_entry():
    return dep_left() + dep_right()


def dep_left():
    return dep_target()


def dep_right():
    return dep_target()


def dep_target():
    return 0
EOF

rm -f update/upd.py   # TC-run mutation state; provision always starts pristine
for ws in chain islands fork fanout fuzzy update depth; do
  mkdir -p "$ws/.git"
done

for f in \
  chain/chain.py \
  islands/islands.py \
  fork/fork.py \
  fanout/fanout.py \
  fuzzy/fuzzy.py \
  fuzzy/fuzzy_main.py \
  fuzzy/fuzzy_alt.py \
  update/update.py \
  depth/depth.py
do
  [ -s "$f" ] || { echo "missing fixture: $f" >&2; exit 1; }
done
