#!/usr/bin/env bash
# Install the burnstop hook into settings.json (macOS/Linux).
#   scripts/install.sh            # user scope (~/.claude/settings.json)
#   scripts/install.sh project    # project scope (./.claude/settings.json)
set -euo pipefail
scope="${1:-user}"
repo="$(cd "$(dirname "$0")/.." && pwd)"
py="$(command -v python3 || command -v python || true)"
if [ -z "$py" ]; then
  echo "Python 3.8+ not found on PATH." >&2
  exit 1
fi
exec "$py" "$repo/cli.py" install --scope "$scope"
