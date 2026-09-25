#!/usr/bin/env bash
cd "$(dirname "$0")"
[ -f .venv/bin/activate ] && source .venv/bin/activate
exec python -m autotube "$@"
