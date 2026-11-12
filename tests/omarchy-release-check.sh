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
  if ! docker run --rm --user "$(id -u):$(id -g)" \
    --env HOME=/tmp --env OMARCHY_PATH=/work/build/omarchy-release-check/upstream \
    --env OMARCHY_AUDIT_VERSION="$tag" --env OMARCHY_AUDIT_COMMIT="$commit" \
    --volume "$PWD:/work" --workdir /work scottland-omarchy-audit \
    python3 tests/omarchy-binding-audit-test.py \
      --audit-output "build/omarchy-release-check/$tag.tsv"; then
    title="Omarchy $tag adapter binding audit failed"
    if ! gh issue list --repo "$repo" --state all --search "$title in:title" --json title --jq '.[].title' | grep -Fxq "$title"; then
      printf 'The automated adapter audit failed for Omarchy %s at %s.\n\nRun: %s\nAudit artifact: omarchy-release-audit-%s\n' \
        "$tag" "$commit" "$GITHUB_SERVER_URL/$repo/actions/runs/$GITHUB_RUN_ID" "$GITHUB_RUN_ID" \
        >build/omarchy-release-check/issue.md
      gh issue create --repo "$repo" --title "$title" --body-file build/omarchy-release-check/issue.md
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
branch="bot/omarchy-tested-${last#v}-$GITHUB_RUN_ID"
git switch -c "$branch"
printf '%s\n' "$last" >omarchy/tested-release
if ! grep -Fxq "$series" omarchy/supported-series; then
  printf '%s\n' "$series" >>omarchy/supported-series
  sort -Vu -o omarchy/supported-series omarchy/supported-series
fi
git add omarchy/tested-release omarchy/supported-series
git -c user.name='Scottland release audit' -c user.email='github-actions[bot]@users.noreply.github.com' \
  commit -m "adapter: verify Omarchy through $last"
git push origin "$branch"
printf 'Omarchy releases through %s passed the real-config binding audit.\n\nThe newly tested series is %s.x; previously tested series remain supported. Audit artifacts are attached to run %s. Merge through the normal reviewed release branch route before publishing an adapter package.\n' \
  "$last" "$series" "$GITHUB_SERVER_URL/$repo/actions/runs/$GITHUB_RUN_ID" \
  >build/omarchy-release-check/pr.md
gh pr create --repo "$repo" --base 0.4 --head "$branch" \
  --title "Adapter: verify Omarchy through $last" --body-file build/omarchy-release-check/pr.md
