#!/usr/bin/env bash
# Render a digest with the repository's current Python path, independent of cwd.
set -Eeuo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly BRIDGE_DIR

PYTHONPATH="$BRIDGE_DIR" exec python3 "$BRIDGE_DIR/scripts/render_telegraph_digest.py" "$@"
