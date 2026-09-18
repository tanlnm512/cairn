#!/usr/bin/env bash
# Build a review-loop fixture workspace for one scenario.
#
# Usage: prepare.sh <scenario> [target-dir]
# Scenarios: pack, banner, guard, gate, quiet, types, capture
#
# Layout under the target dir:
#   ws/                       git repo; `work` HEAD carries the pending change
#                             against `main`
#   store/graph.kg            cairn graph DB for the workspace
#   store/.knowledge/         memory/compass/wiki concepts
#   home/                     CAIRN_HOME sandbox (workspace registry)
#
# The last stdout line is the target dir. Rebuilding an existing target
# starts from scratch (idempotent). Env overrides: CAIRN_BIN (default
# `cairn`), CAIRN_HOME (default <target>/home).
set -euo pipefail

SCENARIO="${1:-}"
TARGET="${2:-${TMPDIR:-/tmp}/cairn-review-loop-fixture/${SCENARIO:-default}}"
CAIRN="${CAIRN_BIN:-cairn}"

if ! git --version >/dev/null 2>&1 && [ -x /usr/bin/git ]; then
    export PATH="/usr/bin:$PATH"
fi

usage() {
    echo "usage: prepare.sh <scenario> [target-dir]; scenarios: pack, banner, guard, gate, quiet, types, capture" >&2
    exit 2
}

[ -n "$SCENARIO" ] || usage
[ -n "$TARGET" ] && [ "$TARGET" != "/" ] || usage

case "$SCENARIO" in
    pack | banner | capture | gate | guard | quiet | types) ;;
    *) usage ;;
esac

export CAIRN_HOME="${CAIRN_HOME:-$TARGET/home}"
# Hash embedder: deterministic, no model download, keeps the build in seconds.
export CAIRN_EMBED_BACKEND=hash

WS="$TARGET/ws"
DB="$TARGET/store/graph.kg"
KNOWLEDGE="$TARGET/store/.knowledge"

rm -rf "$TARGET"
mkdir -p "$CAIRN_HOME" "$KNOWLEDGE" "$WS"

GIT="git -C $WS -c user.name=fixture -c user.email=fixture@cairn.local -c commit.gpgsign=false"

git -C "$WS" init -q -b main

# Shared module layout: ledger is consumed by reporting and api; banner
# consumes and is consumed by nothing; changelog.md belongs to no module.
cat > "$WS/ledger.py" <<'EOF'
"""Ledger module: record storage and date parsing."""


def parse_date(raw):
    """Normalize a raw date string."""
    return raw.strip()
EOF
cat > "$WS/reporting.py" <<'EOF'
"""Reporting module: builds reports from the ledger."""

from ledger import parse_date


def render_report(rows):
    return [parse_date(row["date"]) for row in rows]
EOF
cat > "$WS/api.py" <<'EOF'
"""API module: serves ledger queries over HTTP."""

from ledger import parse_date


def handle_request(raw):
    return {"date": parse_date(raw)}
EOF
cat > "$WS/banner.py" <<'EOF'
"""Banner module: standalone rendering."""


def render_banner(title):
    return "== {} ==".format(title)
EOF
echo "# changelog" > "$WS/changelog.md"

git -C "$WS" add -A
$GIT commit -qm "base"

# The pending change: one module's function body reworked on `work`.
# guard/types share pack's ledger rework so keyed memories match the
# change; quiet reworks banner so the ledger memories key nothing.
# capture shares pack's ledger rework so the recorded comment targets
# changed code; it carries no pre-recorded memories (TC-010 greps absence).
case "$SCENARIO" in
    pack | capture | gate | guard | types)
        cat > "$WS/ledger.py" <<'EOF'
"""Ledger module: record storage and date parsing."""


def parse_date(raw):
    """Normalize a raw date string."""
    value = raw.strip()
    return value
EOF
        ;;
    banner | quiet)
        cat > "$WS/banner.py" <<'EOF'
"""Banner module: standalone rendering."""


def render_banner(title):
    rendered = "== {} ==".format(title)
    return rendered
EOF
        ;;
esac

git -C "$WS" checkout -qb work
git -C "$WS" add -A
$GIT commit -qm "pending change"

"$CAIRN" build --workspace "$WS" --db "$DB" > /dev/null

# Enrichment corpora per scenario: pack/guard/gate carry the full corpus;
# quiet carries only the ledger memories (nothing keys to the change);
# types carries only a decision memory (never warns); banner and capture
# carry none. gate is the guard workspace; gating is enabled by the --gate
# flag at invocation, not by fixture state.
if [ "$SCENARIO" = "pack" ] || [ "$SCENARIO" = "guard" ] || [ "$SCENARIO" = "gate" ]; then
    "$CAIRN" memory record mistake "Never parse dates with regex" \
        --body "In the ledger module, never parse dates with regex; use the dedicated date parser." \
        --resource ledger --confidence 0.8 \
        --db "$DB" --knowledge "$KNOWLEDGE" > /dev/null
    "$CAIRN" memory record pattern "Prefer exponential backoff for retries" \
        --body "Ledger operations that retry remote writes prefer exponential backoff for retries." \
        --resource ledger --confidence 0.8 \
        --db "$DB" --knowledge "$KNOWLEDGE" > /dev/null
    mkdir -p "$KNOWLEDGE/compass" "$KNOWLEDGE/wiki/pages/fixture"
    cat > "$KNOWLEDGE/compass/ledger.md" <<'EOF'
---
type: Compass
title: ledger navigation
description: Module guide for the ledger module.
resource: ledger
---

The ledger module owns record storage and date parsing. Ownership of entries
stays inside the ledger module; reporting and api only read parsed values.
EOF
    cat > "$KNOWLEDGE/wiki/pages/fixture/ledger-architecture.md" <<'EOF'
---
type: Wiki-Article
title: Ledger architecture
description: Architecture page for the ledger module.
resource: ledger
---

The ledger module preserves the invariant that entries are append-only, and
date normalization is the single parse point consumed by reporting and api.
EOF
elif [ "$SCENARIO" = "quiet" ]; then
    "$CAIRN" memory record mistake "Never parse dates with regex" \
        --body "In the ledger module, never parse dates with regex; use the dedicated date parser." \
        --resource ledger --confidence 0.8 \
        --db "$DB" --knowledge "$KNOWLEDGE" > /dev/null
    "$CAIRN" memory record pattern "Prefer exponential backoff for retries" \
        --body "Ledger operations that retry remote writes prefer exponential backoff for retries." \
        --resource ledger --confidence 0.8 \
        --db "$DB" --knowledge "$KNOWLEDGE" > /dev/null
elif [ "$SCENARIO" = "types" ]; then
    "$CAIRN" memory record decision "Cache-first reads" \
        --body "Ledger reads prefer the cache; hit the store only on a miss." \
        --resource ledger --confidence 0.8 \
        --db "$DB" --knowledge "$KNOWLEDGE" > /dev/null
fi

echo "$TARGET"
