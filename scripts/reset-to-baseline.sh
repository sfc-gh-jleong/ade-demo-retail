#!/usr/bin/env bash
# Resets the demo to its healthy baseline:
#   1. closes open ADE pull requests and deletes their ade/* branches;
#   2. resets the source data in Snowflake (RAW, REF, LOAD_ORDERS) without
#      running the pipeline, and empties the tables this repo manages;
#   3. restores objects/ from the `baseline` tag in one commit on main, pushed
#      with your admin override of branch protection;
#   4. waits for the Deploy workflow, which deploys the objects and runs the
#      pipeline once.
#
# The order matters: the baseline code expects the baseline source data (for
# example RAW.ORDERS.CHANNEL), and every deploy runs the pipeline. Resetting the
# data first means that run uses matching code and data.
#
# Usage: scripts/reset-to-baseline.sh [-c <snow connection>] [-y]
#   -c  Snowflake CLI connection to use (default: $SNOWFLAKE_CONNECTION or osm_deploy).
#       Its role must be able to call ADE_DB.CHAOS.RESET_DEMO (SYSADMIN can).
#   -y  Don't ask for confirmation.
set -euo pipefail

connection="${SNOWFLAKE_CONNECTION:-osm_deploy}"
assume_yes=false
while getopts "c:y" opt; do
  case "$opt" in
    c) connection="$OPTARG" ;;
    y) assume_yes=true ;;
    *) echo "Usage: $0 [-c <snow connection>] [-y]" >&2; exit 2 ;;
  esac
done

root="$(git rev-parse --show-toplevel)"
cd "$root"

for tool in git gh snow; do
  command -v "$tool" > /dev/null || { echo "$tool is not installed." >&2; exit 1; }
done
if [ -n "$(git status --porcelain)" ]; then
  echo "The working tree has uncommitted changes. Commit or stash them first." >&2
  exit 1
fi

repo="$(gh repo view --json nameWithOwner --jq .nameWithOwner)"
git fetch --quiet --prune --tags origin
git rev-parse --verify --quiet "refs/tags/baseline" > /dev/null || {
  echo "The baseline tag is missing." >&2
  exit 1
}

open_prs="$(gh pr list --repo "$repo" --label ade --state open --json number --jq '.[].number')"
ade_branches="$(git ls-remote --heads origin 'ade/*' | awk '{print $2}' | sed 's#^refs/heads/##')"

echo "Reset $repo and the demo source data to baseline:"
echo "  open ADE pull requests to close: ${open_prs:-none}" | tr '\n' ' '; echo
echo "  ade/* branches to delete:        $(echo "${ade_branches:-none}" | wc -w | tr -d ' ')"
echo "  Snowflake connection:            $connection"
if ! $assume_yes; then
  read -r -p "Continue? [y/N] " answer
  [[ "$answer" =~ ^[Yy]$ ]] || { echo "Cancelled."; exit 1; }
fi

for pr in $open_prs; do
  gh pr close "$pr" --repo "$repo" --comment "Closed by the demo reset (scripts/reset-to-baseline.sh)." > /dev/null
  echo "Closed PR #$pr"
done
for branch in $(git ls-remote --heads origin 'ade/*' | awk '{print $2}' | sed 's#^refs/heads/##'); do
  git push --quiet origin --delete "$branch"
  echo "Deleted branch $branch"
done

echo "Resetting the source data (no pipeline run)..."
snow sql -c "$connection" -q "CALL ADE_DB.CHAOS.RESET_DEMO(FALSE)" > /dev/null
echo "Source data reset."

git checkout --quiet main
git pull --quiet --ff-only origin main
git rm -r --quiet objects
git checkout baseline -- objects
git add -A objects

if git diff --cached --quiet; then
  echo "objects/ already matches baseline; starting the Deploy workflow to run the pipeline."
  before="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  gh workflow run deploy.yml --repo "$repo" --ref main
  head="$(git rev-parse HEAD)"
  event="workflow_dispatch"
else
  git commit --quiet -m "Reset demo objects to baseline" \
    -m "Restores objects/ from the baseline tag (scripts/reset-to-baseline.sh)."
  before="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  git push --quiet origin main
  head="$(git rev-parse HEAD)"
  event="push"
  echo "Pushed $(git rev-parse --short HEAD) to main."
fi

echo "Waiting for the Deploy run..."
run_id=""
for _ in $(seq 1 30); do
  run_id="$(gh run list --repo "$repo" --workflow deploy.yml --branch main --event "$event" \
              --json databaseId,headSha,createdAt \
              --jq "[.[] | select(.headSha == \"$head\" and .createdAt >= \"$before\")][0].databaseId // empty")"
  [ -n "$run_id" ] && break
  sleep 4
done
if [ -z "$run_id" ]; then
  echo "No Deploy run started for $head. Check the Actions tab." >&2
  exit 1
fi
if gh run watch "$run_id" --repo "$repo" --exit-status > /dev/null; then
  echo "Deploy succeeded. The demo is at baseline."
else
  echo "Deploy failed: $(gh run view "$run_id" --repo "$repo" --json url --jq .url)" >&2
  exit 1
fi
