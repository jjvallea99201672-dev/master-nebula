#!/usr/bin/env bash
# Start the local Grid Memory web UI (127.0.0.1 only). Ctrl-C to stop.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$HERE/bin/gridmem" serve --port "${1:-8792}"
