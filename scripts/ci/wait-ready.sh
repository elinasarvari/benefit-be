#!/usr/bin/env bash
# Wait until `make health` passes, or fail after the timeout (default 180 s).
set -uo pipefail

deadline=$(( $(date +%s) + ${1:-180} ))
until make health > /dev/null 2>&1; do
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "stack did not become ready in time" >&2
    make health || true
    exit 1
  fi
  sleep 3
done
echo "stack is ready"
