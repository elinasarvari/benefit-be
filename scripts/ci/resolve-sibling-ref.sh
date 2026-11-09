#!/usr/bin/env bash
# Print the ref of the sibling repo to check out: the same-named branch if it exists, else main
# (FR-000-25). Usage: resolve-sibling-ref.sh <sibling-repo-url>
set -euo pipefail

url="${1:?usage: resolve-sibling-ref.sh <sibling-repo-url>}"
branch="${GITHUB_HEAD_REF:-${GITHUB_REF_NAME:-main}}"

if [ -n "$branch" ] && git ls-remote --exit-code --heads "$url" "refs/heads/$branch" > /dev/null 2>&1; then
  ref="$branch"
else
  ref="main"
fi

if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
  echo "Sibling ref: $ref" >> "$GITHUB_STEP_SUMMARY"
fi
echo "$ref"
