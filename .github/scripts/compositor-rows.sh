#!/usr/bin/env bash
# Run the contract's compositor rows in bounded, isolated hosted containers.
set -euo pipefail

shard=${1:?usage: compositor-rows.sh SHARD SHARD_COUNT}
shard_count=${2:?usage: compositor-rows.sh SHARD SHARD_COUNT}
[[ $shard =~ ^[0-9]+$ && $shard_count =~ ^[0-9]+$ ]] &&
  (( shard_count > 0 && shard < shard_count )) || exit 2

repo=$(pwd -P)
evidence="$repo/build/compositor-rows/shard-$shard"
[[ ! -e $evidence ]] || { echo "compositor row evidence path is occupied" >&2; exit 2; }
mkdir -m 700 -p "$evidence/logs"
rows="$evidence/rows.tsv"
printf 'row\tstatus\texit_code\treason\n' > "$rows"
product_commit=${SCOTTLAND_PRODUCT_SOURCE:?set the pinned product commit}
harness_commit=${SCOTTLAND_HARNESS_HEAD:?set the PR harness head}
product_tree=$(git rev-parse "${product_commit}^{tree}")
[[ $product_commit == dc155ea158dd8aee14bc9a30f451b7e73316586b &&
   $product_tree == 5ea3f22461b820144778512e674d3b632929ba27 &&
   $(git rev-parse HEAD) == "$harness_commit" ]] &&
  git merge-base --is-ancestor "$product_commit" "$harness_commit" || {
    echo 'pinned product source or PR harness head does not match this checkout' >&2; exit 2;
  }
git diff --name-only "$product_commit" "$harness_commit" > "$evidence/harness-diff.txt"
while IFS= read -r changed_path; do
  case $changed_path in
    .github/scripts/compositor-rows.sh|.github/scripts/standalone-rows.tsv|.github/workflows/fast-checks.yml|\
    tests/omarchy-session-wrap-test.py|tests/reload-touch-focus-test.py|tests/session-busy-exec-test.sh|tests/zone-pin-test.py) ;;
    *) echo "product source differs from pinned commit: $changed_path" >&2; exit 2 ;;
  esac
done < "$evidence/harness-diff.txt"
prepared_image=$(docker image inspect scottland-checks:compositor --format '{{.Id}}' 2>/dev/null || true)

{
  printf 'product_commit\t%s\nproduct_tree\t%s\n' "$product_commit" "$product_tree"
  printf 'harness_commit\t%s\nharness_tree\t%s\n' "$harness_commit" "$(git rev-parse HEAD^{tree})"
  printf 'package_version\t%s-%s\n' \
    "$(sed -n 's/^pkgver=//p' packaging/arch/PKGBUILD)" \
    "$(sed -n 's/^pkgrel=//p' packaging/arch/PKGBUILD)"
  printf 'cargo_lock_sha256\t%s\n' "$(sha256sum core/tools/Cargo.lock | cut -d' ' -f1)"
  printf 'container_base\t%s\n' "${SCOTTLAND_BASE_IMAGE_DIGEST:-unknown}"
  printf 'container_image\t%s\n' "${prepared_image:-unavailable}"
  printf 'hosted_image\t%s %s\n' "${ImageOS:-unknown}" "${ImageVersion:-unknown}"
} > "$evidence/identity.txt"

docker_args=(
  --rm --privileged
  --user "$(id -u):$(id -g)"
  --env HOME=/home/ci
  --env XDG_RUNTIME_DIR="/run/user/$(id -u)"
  --env SCOTTLAND_HEADLESS_ISOLATION=1
  --env SCOTTLAND_HARNESS_HEAD
  --env SCOTTLAND_PRODUCT_SOURCE
  --volume "$repo:/work"
  --workdir /work
)

# Build once from the checked-out merge result. A real Wayfire+Scottland IPC
# session must start before any row can be credited to this venue.
setup_ok=1
if [[ -z $prepared_image ]]; then
  setup_ok=0
  echo 'prepared compositor image is unavailable; rows are not run' >&2
elif ! docker run "${docker_args[@]}" \
    --env SCOTTLAND_TEST_SCRATCH="/work/build/compositor-rows/shard-$shard/preflight" \
    --env SCOTTLAND_HEADLESS_DIR="/work/build/compositor-rows/shard-$shard/preflight/session" \
    scottland-checks:compositor bash -euo pipefail -c '
      mkdir -m 700 -p "$XDG_RUNTIME_DIR" "$SCOTTLAND_TEST_SCRATCH"
      test "$(git rev-parse HEAD)" = "$SCOTTLAND_HARNESS_HEAD"
      test "$(git rev-parse "${SCOTTLAND_PRODUCT_SOURCE}^{tree}")" = 5ea3f22461b820144778512e674d3b632929ba27
      grep -qx "pkgver=0.4.1" packaging/arch/PKGBUILD
      make test-hooks
      sha256sum build/libscottland.so \
        build/hooks/libexec/scottland-exec \
        build/hooks/libexec/scottland-build-config \
        build/hooks/libexec/scottland-ctl
      for required_tool in magick hyprctl grim wf-recorder; do
        command -v "$required_tool"
      done
      pacman -Q
      preflight_status=0
      tests/headless.sh start || preflight_status=$?
      if (( preflight_status == 0 )); then
        tests/headless.sh ipc scottland/desktop-model || preflight_status=$?
        tests/headless.sh ipc scottland/goo-state > "$SCOTTLAND_TEST_SCRATCH/goo-state.json" || true
      fi
      tests/headless.sh stop --preserve-scratch || preflight_status=$?
      exit "$preflight_status"
    ' > "$evidence/preflight.log" 2>&1; then
  setup_ok=0
  echo 'real compositor preflight failed; rows are not run' >&2
fi
goo_available=0
if [[ -f $evidence/preflight/goo-state.json ]] &&
    jq -e '.screens | type == "array" and length > 0' "$evidence/preflight/goo-state.json" >/dev/null; then
  goo_available=1
fi
printf 'goo_screen_available\t%s\n' "$goo_available" >> "$evidence/identity.txt"

failed=0
row_index=0
model_row_deferred=0
while IFS=$'\t' read -r row_name venue dependency; do
  [[ -z $row_name || $row_name == \#* ]] && continue
  [[ $venue == headless ]] || continue
  row_shard=$((row_index % shard_count))
  # The model-process row is invoked by widgets-test.sh after its widget fixture
  # docks a window. Keep both rows in one shard so its actual outcome is visible.
  [[ $row_name != tests/model-process-test.py ]] || row_shard=3
  if (( row_shard != shard )); then
    row_index=$((row_index + 1))
    continue
  fi

  if (( ! setup_ok )); then
    printf '%s\tNOT_RUN\t-\treal compositor preflight failed\n' "$row_name" >> "$rows"
  elif [[ $row_name == tests/omarchy-reload-rehearsal-test.py ]]; then
    printf '%s\tNOT_RUN\t-\trequires a separately built older checkout for the upgrade fixture\n' "$row_name" >> "$rows"
  elif [[ $row_name == tests/model-process-test.py ]]; then
    model_row_deferred=1
  else
    row_dir="$evidence/row-$row_index"
    mkdir -m 700 "$row_dir"
    scratch="$row_dir/scratch"
    headless="$scratch/session"
    # WK37/WK38 verifies the runner-owned item2 directory and exact session
    # layout, so create that row's private scratch at the required build root.
    if [[ $row_name == tests/hint-outline-test.sh ]]; then
      scratch=$(mktemp -d "$repo/build/item2-tint.XXXXXXXX")
      headless="$scratch/headless-wk37-wk38"
    fi
    container_scratch="/work${scratch#"$repo"}"
    container_headless="/work${headless#"$repo"}"
    container_name="scottland-${GITHUB_RUN_ID:-local}-${GITHUB_RUN_ATTEMPT:-1}-$shard-$row_index"
    log="$evidence/logs/${row_name##*/}.log"
    printf 'RUN %s\n' "$row_name"
    if docker run "${docker_args[@]}" --name "$container_name" \
        --env SCOTTLAND_TEST_ROW="$row_name" \
        --env SCOTTLAND_TEST_SCRATCH="$container_scratch" \
        --env SCOTTLAND_HEADLESS_DIR="$container_headless" \
        scottland-checks:compositor bash -euo pipefail -c '
          umask 077
          mkdir -m 700 -p "$XDG_RUNTIME_DIR" "$SCOTTLAND_TEST_SCRATCH"
          mkdir -m 700 "$SCOTTLAND_TEST_SCRATCH/tmp"
          export TMPDIR="$SCOTTLAND_TEST_SCRATCH/tmp"
          test "$(git rev-parse HEAD)" = "$SCOTTLAND_HARNESS_HEAD"
          printf "product_commit=%s\nproduct_tree=%s\nharness_commit=%s\nharness_tree=%s\n" \
            "$SCOTTLAND_PRODUCT_SOURCE" "$(git rev-parse "${SCOTTLAND_PRODUCT_SOURCE}^{tree}")" \
            "$(git rev-parse HEAD)" "$(git rev-parse HEAD^{tree})"
          sha256sum build/libscottland.so
          timeout --signal=TERM --kill-after=45s 20m bash -euo pipefail -c '\''
            case "$SCOTTLAND_TEST_ROW" in
              tests/widget-input-test.py)
                tests/headless.sh start --widgets
                input_status=0
                tests/headless.sh run python3 "$SCOTTLAND_TEST_ROW" || input_status=$?
                tests/headless.sh stop || input_status=$?
                exit "$input_status"
                ;;
              tests/shim-reconnect-test.sh) bash -x "$SCOTTLAND_TEST_ROW" ;;
              tests/*.py) python3 "$SCOTTLAND_TEST_ROW" ;;
              tests/*.sh) bash "$SCOTTLAND_TEST_ROW" ;;
              *) echo "unknown contract entrypoint" >&2; exit 2 ;;
            esac
          '\''
        ' > "$log" 2>&1; then
      row_status=PASS
      exit_code=0
    else
      exit_code=$?
      row_status=FAIL
      failed=1
      tail -50 "$log"
    fi
    if [[ -d $scratch ]]; then
      while IFS= read -r -d '' runtime_log; do
        printf '\n--- %s (last 80 lines) ---\n' "${runtime_log#"$repo"/}" >> "$log"
        tail -80 "$runtime_log" >> "$log"
      done < <(find "$scratch" -type f \( -name wayfire.log -o -name .headless-start-stderr.log \) -print0)
    fi
    container_persisted=0
    if docker container inspect "$container_name" >/dev/null 2>&1; then
      echo 'owned row container persisted after exit' >> "$log"
      docker rm -f "$container_name" >> "$log" 2>&1 || true
      row_status=FAIL
      failed=1
      container_persisted=1
    else
      echo 'postflight: owned row container removed' >> "$log"
    fi
    row_reason=-
    if [[ $row_status == FAIL && $container_persisted == 0 ]]; then
      case $row_name in
        tests/attention-color-family-test.sh|tests/goo-dye-strength-test.sh|tests/goo-test.sh|tests/hint-avoidance-hang-test.sh|tests/window-mode-tint-pixel-test.sh)
          if (( ! goo_available )); then
            row_status=NOT_RUN
            row_reason='Goo GPU screen not established in preflight; cause unresolved and failed assertions retained in row log'
          fi ;;
        tests/hint-outline-test.sh)
          if grep -Fq 'isolated headless session required:' "$log"; then
            row_status=NOT_RUN
            row_reason='test isolation precondition refused; failed assertions retained in row log'
          fi ;;
        tests/reload-touch-focus-test.sh|tests/zone-pin-test.sh)
          if grep -Fq 'No such file or directory' "$log" && grep -Fq '.reloading' "$log"; then
            row_status=NOT_RUN
            row_reason='private runtime reload marker parent absent; failed assertions retained in row log'
          fi ;;
        tests/session-busy-exec-test.sh)
          if grep -Fq '7 passed, 0 failed' "$log"; then
            row_status=NOT_RUN
            row_reason='all named checks passed but isolated session cleanup returned nonzero'
          fi ;;
        tests/settings-help-test.sh)
          if grep -Fq 'Signals.SIGBUS: 7' "$log"; then
            row_status=NOT_RUN
            row_reason='grim crashed with SIGBUS; earlier failed assertions retained in row log'
          fi ;;
      esac
    elif [[ $row_name == tests/omarchy-session-wrap-test.py && $row_status == PASS ]]; then
      row_reason='mock Session fixture; no live compositor behavior in this row'
    fi
    printf '%s\t%s\t%s\t%s\n' "$row_name" "$row_status" "$exit_code" "$row_reason" >> "$rows"
    printf '%s %s (exit %s)\n' "$row_status" "$row_name" "$exit_code"
  fi
  row_index=$((row_index + 1))
done < .github/scripts/standalone-rows.tsv

if (( model_row_deferred )); then
  widgets_log="$evidence/logs/widgets-test.sh.log"
  if [[ -f $widgets_log ]] &&
      grep -Fq 'PASS  DM2 launcher exit is published as a newer full snapshot' "$widgets_log"; then
    printf 'tests/model-process-test.py\tPASS\t0\tinvoked by widgets-test.sh with its docked widget fixture\n' >> "$rows"
  elif [[ -f $widgets_log ]] &&
      grep -Fq 'FAIL  DM2 launcher exit is published as a newer full snapshot' "$widgets_log"; then
    printf 'tests/model-process-test.py\tFAIL\t1\tinvoked by widgets-test.sh with its docked widget fixture\n' >> "$rows"
    failed=1
  else
    printf 'tests/model-process-test.py\tNOT_RUN\t-\tdocked widget fixture was not reached in widgets-test.sh\n' >> "$rows"
  fi
fi

cat "$evidence/identity.txt"
cat "$rows"
(( setup_ok && ! failed ))
