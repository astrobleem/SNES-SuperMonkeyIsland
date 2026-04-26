#!/usr/bin/env bash
# Sync the local in-tree mesen_mcp/ copy with the Mesen2 fork's canonical
# python/mesen_mcp/. Run after editing either side. Default direction is
# from this repo (where iteration usually happens) → fork (the
# distribution copy). Pass `--from-fork` to pull the other way.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SMI="$HERE/mesen_mcp"
FORK="${MESEN_FORK_PATH:-$HERE/../../Mesen2}/python/mesen_mcp"

if [[ ! -d "$FORK" ]]; then
    echo "fork path not found: $FORK"
    echo "set MESEN_FORK_PATH to the Mesen2 fork's root if it lives elsewhere."
    exit 1
fi

if [[ "${1:-}" == "--from-fork" ]]; then
    src="$FORK"
    dst="$SMI"
    echo "pulling fork → SMI in-tree copy"
else
    src="$SMI"
    dst="$FORK"
    echo "pushing SMI in-tree copy → fork"
fi

# Mirror — delete files in dst that aren't in src so we don't leave
# stale artefacts behind. Excludes pycache / build artefacts.
rsync -av --delete \
    --exclude='__pycache__' \
    --exclude='*.egg-info' \
    --exclude='.pytest_cache' \
    "$src/" "$dst/"

echo "done. summarise the fork copy:"
ls -la "$FORK" | head -15
