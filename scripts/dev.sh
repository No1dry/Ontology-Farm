#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
exec "$root/.venv/bin/python" "$root/scripts/dev.py" "${1:-status}"
