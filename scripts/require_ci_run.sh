#!/usr/bin/env bash
# Fail unless a run of the manually-dispatched CI workflow SUCCEEDED for one
# exact commit.
#
# CI is on `workflow_dispatch`. Nothing runs it for you. That removes the cost
# of a four-OS matrix on every push, and it introduces one failure mode in its
# place: a pull request nobody dispatched shows an empty checks list, and an
# empty checks list looks exactly like a clean one. "Nobody ran it" and "it
# passed" have to be different colours at merge time, so this exits non-zero
# for every state that is not a recorded success.
#
# It keys on the head SHA, not on the branch. A green run on the commit before
# the one under review is stale evidence about code that no longer exists, and
# the query below cannot see it: `head_sha` is an exact match, so a new commit
# starts red again.
#
# Usage: require_ci_run.sh <owner/repo> <sha> [workflow-file]
set -euo pipefail

REPO="${1:-}"
SHA="${2:-}"
WORKFLOW="${3:-ci.yml}"

if [ -z "$REPO" ] || [ -z "$SHA" ]; then
  echo "usage: require_ci_run.sh <owner/repo> <sha> [workflow-file]" >&2
  exit 2
fi

# A truncated or empty SHA would silently widen the query into "any run".
if ! printf '%s' "$SHA" | grep -Eq '^[0-9a-f]{40}$'; then
  echo "::error::Refusing to check a commit that is not a full 40-character SHA: '$SHA'" >&2
  exit 1
fi

dispatch_hint() {
  echo "Dispatch it, wait for it to finish, then re-run this check:"
  echo "    gh workflow run CI --ref <branch>"
  echo "    gh run watch \"\$(gh run list --workflow=CI --branch <branch> --limit 1 --json databaseId --jq '.[0].databaseId')\""
}

if ! runs=$(gh api "/repos/$REPO/actions/workflows/$WORKFLOW/runs?head_sha=$SHA&per_page=100" \
  --jq '.workflow_runs[] | [.status, (.conclusion // "pending"), .event, .html_url] | @tsv'); then
  # A query that failed is not an absence. Say which it was.
  echo "::error::Could not query $WORKFLOW runs for $SHA. This check makes no claim about that commit."
  exit 1
fi

if [ -z "$runs" ]; then
  echo "::error::No $WORKFLOW run exists for commit $SHA."
  echo "No $WORKFLOW run exists for commit $SHA."
  dispatch_hint
  exit 1
fi

echo "Runs of $WORKFLOW recorded against $SHA:"
printf '%s\n' "$runs" | while IFS=$'\t' read -r status conclusion event url; do
  printf '  %-12s %-10s %-18s %s\n' "$status" "$conclusion" "$event" "$url"
done

# Matched with a case statement rather than a pipe into grep -q, for the reason
# check-release-prep.yml already records: grep -q closes the pipe on its first
# match and the writer takes SIGPIPE, which pipefail reports as a failed match.
while IFS=$'\t' read -r status conclusion event url; do
  if [ "$status" = "completed" ] && [ "$conclusion" = "success" ]; then
    echo "CI passed for this exact commit: $url"
    exit 0
  fi
done <<<"$runs"

if printf '%s\n' "$runs" | grep -Eq $'^(queued|in_progress|waiting|requested|pending)\t'; then
  echo "::error::A $WORKFLOW run for $SHA is still running. Re-run this check once it finishes."
  echo "A $WORKFLOW run for $SHA is still running. Re-run this check once it finishes."
  exit 1
fi

echo "::error::No $WORKFLOW run for $SHA succeeded. Every run against this commit is listed above."
echo "No $WORKFLOW run for $SHA succeeded."
dispatch_hint
exit 1
