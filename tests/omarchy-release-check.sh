#!/bin/bash
# Scheduled upstream release audit. Runs inside a disposable hosted CI checkout.
set -euo pipefail

repo=${GITHUB_REPOSITORY:?}
last=$(cat omarchy/tested-release)
[[ $last =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "invalid tested release: $last" >&2; exit 2; }
mkdir -p build/omarchy-release-check
if [[ -n $(gh api "repos/$repo/pulls?state=open&per_page=100" \
  --jq '[.[] | select(.head.ref | startswith("bot/omarchy-tested-"))] | .[0].number // empty') ]]; then
  echo 'A tested-series update is awaiting review; checking resumes after it lands'
  exit 0
fi

mapfile -t releases < <(gh api --paginate repos/omacom/omarchy/releases --jq '.[].tag_name' |
  grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -Vu)

seen=0
for tag in "${releases[@]}"; do
  if [[ $tag == "$last" ]]; then seen=1; continue; fi
  [[ $seen == 1 ]] || continue
  echo "Auditing Omarchy $tag"
  git clone --quiet --depth 1 --branch "$tag" https://github.com/omacom/omarchy.git build/omarchy-release-check/upstream
  commit=$(git -C build/omarchy-release-check/upstream rev-parse HEAD)
  audit_file="build/omarchy-release-check/$tag.tsv"
  if ! docker run --rm --user "$(id -u):$(id -g)" \
    --env HOME=/tmp --env OMARCHY_PATH=/work/build/omarchy-release-check/upstream \
    --env OMARCHY_AUDIT_VERSION="$tag" --env OMARCHY_AUDIT_COMMIT="$commit" \
    --volume "$PWD:/work" --workdir /work scottland-omarchy-audit \
    python3 tests/omarchy-binding-audit-test.py \
      --audit-output "$audit_file"; then
    title="Omarchy $tag adapter binding audit failed"
    issue_body=build/omarchy-release-check/issue.md
    printf 'Scottland compatibility failure for Omarchy %s at %s.\n\nFailing bindings:\n' \
      "$tag" "$commit" >"$issue_body"
    python3 - "$audit_file" >>"$issue_body" <<'PY'
import csv
import sys
from pathlib import Path

report = Path(sys.argv[1])
if not report.is_file():
    print("- The audit stopped before it produced per-binding results; see the run log.")
else:
    lines = report.read_text(errors="replace").splitlines()
    failed = [row for row in csv.DictReader(
        (line for line in lines if not line.startswith("#")), delimiter="\t")
        if row["status"] == "broken"]
    for row in failed:
        print(f'- {row["keys"]}: {row["classification"]}')
    if not failed:
        print("- No binding was classified as broken; see audit diagnostics in the run log.")
    for line in lines:
        if line.startswith("# ERROR:"):
            print(f"- Audit diagnostic: {line[8:].strip()}")
PY
    printf '\nRun: %s\nAudit artifact: omarchy-release-audit-%s\n' \
      "$GITHUB_SERVER_URL/$repo/actions/runs/$GITHUB_RUN_ID" "$GITHUB_RUN_ID" >>"$issue_body"
    issue_record=$(gh issue list --repo "$repo" --state all --limit 100 \
      --search "$title in:title" --json number,title,state |
      ISSUE_TITLE="$title" python3 -c 'import json, os, sys; print(next((str(i["number"]) + " " + i["state"] for i in json.load(sys.stdin) if i["title"] == os.environ["ISSUE_TITLE"]), ""))')
    if [[ -n $issue_record ]]; then
      read -r issue_number issue_state <<<"$issue_record"
      gh issue edit "$issue_number" --repo "$repo" --body-file "$issue_body"
      if [[ $issue_state == CLOSED ]]; then gh issue reopen "$issue_number" --repo "$repo"; fi
    else
      gh issue create --repo "$repo" --title "$title" --body-file "$issue_body"
    fi
    exit 1
  fi
  rm -rf build/omarchy-release-check/upstream
  last=$tag
done
[[ $seen == 1 ]] || { echo "tested release $last is absent from upstream releases" >&2; exit 2; }

if [[ $last == "$(cat omarchy/tested-release)" ]]; then
  echo 'No new Omarchy release to audit'
  exit 0
fi

series=${last#v}
series=${series%.*}
branch="bot/omarchy-tested-${last#v}"
if git ls-remote --exit-code --heads origin "$branch" >/dev/null; then
  echo "Reusing existing proposal branch $branch"
else
  git switch -c "$branch"
  printf '%s\n' "$last" >omarchy/tested-release
  if ! grep -Fxq "$series" omarchy/supported-series; then
    printf '%s\n' "$series" >>omarchy/supported-series
    sort -Vu -o omarchy/supported-series omarchy/supported-series
  fi
  pkgrel=$(sed -n 's/^pkgrel=//p' packaging/arch/PKGBUILD)
  [[ $pkgrel =~ ^[0-9]+$ ]] || { echo "invalid package release: $pkgrel" >&2; exit 2; }
  sed -i "s/^pkgrel=$pkgrel$/pkgrel=$((pkgrel + 1))/" packaging/arch/PKGBUILD
  git add omarchy/tested-release omarchy/supported-series packaging/arch/PKGBUILD
  git -c user.name='Scottland release audit' -c user.email='github-actions[bot]@users.noreply.github.com' \
    commit -m "adapter: verify Omarchy through $last"
  git push origin "$branch"
fi
printf 'Omarchy releases through %s passed the real-config binding audit.\n\nThe newly tested series is %s.x; previously tested series remain supported. This PR also increments pkgrel for the adapter package cut. Audit artifacts are attached to run %s. Continue through checks, independent review, the merge queue and exact-cut release review before publication.\n' \
  "$last" "$series" "$GITHUB_SERVER_URL/$repo/actions/runs/$GITHUB_RUN_ID" \
  >build/omarchy-release-check/pr.md
gh pr create --repo "$repo" --base 0.4 --head "$branch" \
  --title "Adapter: verify Omarchy through $last" --body-file build/omarchy-release-check/pr.md
