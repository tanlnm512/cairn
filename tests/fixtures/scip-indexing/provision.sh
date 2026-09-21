#!/bin/sh
# Provision the scip-indexing fixture family: one git repo per workspace
# (the scanner discovers repos by the .git marker; TC-009/010/015/016 and the
# covered/opaque builds all need it). Idempotent: re-init is a no-op and the
# initial commit is only created when HEAD is absent.
#
# The committed index.scip binaries are generated once with the vendored stub
# (cairn.parsers._scip_pb2): build scip.Index / scip.Document /
# scip.Occurrence values whose occurrence positions match the fixture
# sources, then SerializeToString() -- regenerate the same way, never by hand.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

for ws in covered covered-off opaque opaque-off drifted drifted-off autogen seven-langs update heavy heavy-off; do
    [ -d "$root/$ws" ] || continue
    cd "$root/$ws"
    git init -q
    if ! git rev-parse -q --verify HEAD >/dev/null 2>&1; then
        git add -A
        git --no-pager -c user.name=cairn-fixtures -c user.email=fixtures@cairn.local \
            commit -q --no-gpg-sign -m "fixture: $ws workspace"
    fi
done

# multirepo/ is a multi-repo workspace: the workspace root carries no .git;
# each subdirectory is its own repository (the scanner discovers them by marker).
for repo in "$root"/multirepo/*/; do
    [ -d "$repo" ] || continue
    cd "$repo"
    git init -q
    if ! git rev-parse -q --verify HEAD >/dev/null 2>&1; then
        git add -A
        git --no-pager -c user.name=cairn-fixtures -c user.email=fixtures@cairn.local \
            commit -q --no-gpg-sign -m "fixture: multirepo/$(basename "$repo")"
    fi
done
