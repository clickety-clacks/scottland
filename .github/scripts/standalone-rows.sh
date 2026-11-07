#!/usr/bin/env bash
set -euo pipefail

evidence="$PWD/build/standalone-rows"
mkdir -p "$evidence"
{
  printf 'source_commit\t%s\n' "$(git rev-parse HEAD)"
  printf 'source_tree\t%s\n' "$(git rev-parse HEAD^{tree})"
  printf 'source_branch\t%s\n' "${GITHUB_REF:-unknown}"
  printf 'container_os\t'
  . /etc/os-release
  printf '%s %s\n' "$NAME" "$VERSION_ID"
  printf 'cargo_lock_sha256\t'
  sha256sum core/tools/Cargo.lock | cut -d' ' -f1
  printf 'base_image_digest\t%s\n' "${SCOTTLAND_BASE_IMAGE_DIGEST:-unknown}"
  printf 'prepared_container_id\t%s\n' "${SCOTTLAND_CONTAINER_ID:-unknown}"
  printf '\ninstalled_packages\n'
  pacman -Q
  printf '\npython_version\t'
  python3 --version
  printf 'compiler_version\t'
  gcc --version | sed -n '1p'
} > "$evidence/identity.txt"
cat "$evidence/identity.txt"

printf 'row\tstatus\texit_code\treason\n' > "$evidence/rows.tsv"
failed=0
while IFS=$'\t' read -r row venue; do
  [[ -z "$row" || "$row" == \#* ]] && continue
  log="$evidence/${row##*/}.log"
  if [[ "$venue" == standalone ]]; then
    if timeout --signal=TERM --kill-after=10s 4m python3 "$row" > "$log" 2>&1; then
      status=PASS
      code=0
    else
      code=$?
      status=FAIL
      failed=1
    fi
    printf '%s\t%s\t%s\t%s\n' "$row" "$status" "$code" '-' >> "$evidence/rows.tsv"
    printf '%s %s (exit %s)\n' "$status" "$row" "$code"
    cat "$log"
  elif [[ "$venue" == headless ]]; then
    printf 'Requires a compositor session; reserved for the headless route.\n' > "$log"
    printf '%s\tNOT_RUN\t-\theadless_compositor\n' "$row" >> "$evidence/rows.tsv"
    printf 'NOT_RUN %s (headless compositor)\n' "$row"
  else
    printf 'Unknown venue for %s: %s\n' "$row" "$venue" >&2
    exit 2
  fi
done < .github/scripts/standalone-rows.tsv

cat "$evidence/rows.tsv"
exit "$failed"
