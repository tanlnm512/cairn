#!/bin/sh
# Provision the scip-indexing fixtures: one git repo per workspace
# (the scanner discovers repos by the .git marker). Idempotent: re-init is a
# no-op and the initial commit is only created when HEAD is absent.
#
# The committed index.scip binaries were generated once from SCIP proto
# values whose occurrence positions match the fixture sources; regenerate
# the same way, never by hand. The generator is not in this tree: recover
# it from git history.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

for ws in covered covered-off; do
    [ -d "$root/$ws" ] || continue
    cd "$root/$ws"
    git init -q
    if ! git rev-parse -q --verify HEAD >/dev/null 2>&1; then
        git add -A
        git --no-pager -c user.name=cairn-fixtures -c user.email=fixtures@cairn.local \
            commit -q --no-gpg-sign -m "fixture: $ws workspace"
    fi
done
