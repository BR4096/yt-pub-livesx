#!/usr/bin/env bash
# setup.sh — shortcut for scripts/setup-system
# (Part 1 of setup: prepares machine + starts master-dashboard)
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$DIR/scripts/setup-system" "$@"
