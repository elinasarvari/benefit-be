#!/usr/bin/env bash
# Rerun a CI run's failed jobs once when one of them failed on the network, not on the code.
#
# Usage: retry-flaky.sh <owner/repo> <run-id> [--dry-run]
# Needs GH_TOKEN with actions:write (the retry-flaky workflow passes its GITHUB_TOKEN).
#
# It reruns only when all of these hold:
#   - this is the run's first attempt (so a run is retried at most once),
#   - no newer run of the same workflow exists for the branch (a run cancelled because a new push
#     superseded it is left alone),
#   - at least one failed or cancelled job's log shows a network error (DNS, a timed-out or reset
#     connection, a 5xx from a package server). A test that fails on its own is never retried.
set -euo pipefail

repo="${1:?usage: retry-flaky.sh <owner/repo> <run-id> [--dry-run]}"
run="${2:?usage: retry-flaky.sh <owner/repo> <run-id> [--dry-run]}"
dry="${3:-}"

# What a network failure on a runner looks like: apt, curl, pnpm/npm, uv/pip, git and GitHub itself.
# ECONNREFUSED is not here: in the e2e jobs it means our own stack is down, which is a real failure.
NETWORK='Temporary failure resolving|Could not resolve host|Could not resolve hostname|Name or service not known|getaddrinfo (EAI_AGAIN|ENOTFOUND)|EAI_AGAIN|ETIMEDOUT|ECONNRESET|ERR_SOCKET_TIMEOUT|socket hang up|Connection timed out|Connection reset by peer|Operation timed out|TLS handshake timeout|i/o timeout|Failed to connect to [^ ]+ port [0-9]+|Unable to connect to|(502 Bad Gateway|503 Service Unavailable|504 Gateway Time-?out)|Failed to fetch https?://|Some index files failed to download|error: RPC failed|The runner has received a shutdown signal|lost communication with the server|Failed to (save|restore|download).*(cache|artifact).*(timed out|ECONN|network)'

# A connection error to the runner itself is our own stack not running: a real failure, never retried.
OWN_STACK='localhost|127\.0\.0\.1|\[::1\]|0\.0\.0\.0'

api() { gh api -H "Accept: application/vnd.github+json" "$@"; }

attempt="$(api "repos/$repo/actions/runs/$run" --jq '.run_attempt')"
workflow_id="$(api "repos/$repo/actions/runs/$run" --jq '.workflow_id')"
branch="$(api "repos/$repo/actions/runs/$run" --jq '.head_branch')"
conclusion="$(api "repos/$repo/actions/runs/$run" --jq '.conclusion')"
echo "run $run: $conclusion, attempt $attempt, branch $branch"

case "$conclusion" in failure|cancelled|timed_out) ;; *) echo "nothing to retry"; exit 0 ;; esac
if [ "$attempt" != "1" ]; then
  echo "already retried once (attempt $attempt): leaving it failed"
  exit 0
fi
latest="$(api -X GET "repos/$repo/actions/workflows/$workflow_id/runs" -f branch="$branch" -f per_page=1 \
  --jq '.workflow_runs[0].id')"
if [ -n "$latest" ] && [ "$latest" != "$run" ]; then
  echo "superseded by run $latest on $branch: not retrying"
  exit 0
fi

jobs="$(api --paginate "repos/$repo/actions/runs/$run/jobs?per_page=100" \
  --jq '.jobs[] | select(.conclusion=="failure" or .conclusion=="cancelled" or .conclusion=="timed_out")
        | "\(.id)\t\(.name)"')"
[ -n "$jobs" ] || { echo "no failed jobs"; exit 0; }

flaky=""
while IFS=$'\t' read -r id name; do
  [ -n "$id" ] || continue
  # gh run view, not gh api: gh api refuses to print a log with colour codes in it
  if ! log="$(gh run view "$run" --repo "$repo" --log --job "$id" 2>&1)"; then
    echo "job '$name': couldn't read its log ($(printf '%s' "$log" | tail -1 | cut -c1-120))"
    continue
  fi
  hits="$(printf '%s\n' "$log" | grep -E "$NETWORK" | grep -v -E "$OWN_STACK" | head -3 | cut -f3- || true)"
  if [ -n "$hits" ]; then
    echo "job '$name' failed on the network:"
    printf '%s\n' "$hits" | cut -c1-200 | sed 's/^/    /'
    flaky=1
  else
    echo "job '$name' failed with no sign of a network error"
  fi
done <<< "$jobs"

if [ -z "$flaky" ]; then
  echo "no network failure: not retrying"
  exit 0
fi
if [ "$dry" = "--dry-run" ]; then
  echo "dry run: would rerun the failed jobs of run $run"
  exit 0
fi
gh run rerun "$run" --repo "$repo" --failed
echo "reran the failed jobs of run $run"
if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  echo "Reran the failed jobs of [run $run](https://github.com/$repo/actions/runs/$run) once: a job failed on the network." \
    >> "$GITHUB_STEP_SUMMARY"
fi
