# Sourced by real-input wrappers that need private, owner-checked scratch.

scottland_runner_fail() {
  printf 'shared-runner setup: %s\n' "$*" >&2
  return 1
}

scottland_runner_resolve_build_child() {
  local requested=$1 build=$2 resolved
  [[ $requested == /* ]] || requested="$PWD/$requested"
  [[ ! -e $requested && ! -L $requested ]] || {
    scottland_runner_fail "path already exists: $requested"
    return 1
  }
  resolved=$(realpath -m -- "$requested") || return 1
  [[ $(dirname -- "$resolved") == "$build" ]] || {
    scottland_runner_fail "keep runner paths as direct children of $build"
    return 1
  }
  printf '%s\n' "$resolved"
}

scottland_runner_owned_dir() {
  local path=$1 marker=$2
  [[ -d $path && ! -L $path && -f $path/$marker && ! -L $path/$marker ]] || return 1
  if [[ $marker == .scottland-headless-owner ]]; then
    [[ $(sed -n '1p' "$path/$marker") == "$(id -u)" &&
       $(sed -n '2p' "$path/$marker") == "$SCOTTLAND_RUNNER_RUNTIME" &&
       $(sed -n '3p' "$path/$marker") == "$SCOTTLAND_HEADLESS_OWNER" ]]
  else
    [[ $(cat -- "$path/$marker") == "$SCOTTLAND_HEADLESS_OWNER" ]]
  fi
}

scottland_runner_prepare() {
  local requested_artifacts=$1 build
  : "${SCOTTLAND_HEADLESS_DIR:?set a fresh headless directory under this checkout's build/}"

  local repo
  repo=$(realpath -e -- "$PWD") || return 1
  [[ ! -L $repo/build ]] || { scottland_runner_fail 'checkout build directory must not be a symlink'; return 1; }
  mkdir -p -- "$repo/build"
  build=$(realpath -e -- "$repo/build") || return 1
  [[ $build == "$repo/build" ]] || { scottland_runner_fail 'checkout build directory resolves outside the checkout'; return 1; }
  SCOTTLAND_HEADLESS_DIR=$(scottland_runner_resolve_build_child "$SCOTTLAND_HEADLESS_DIR" "$build") || return 1
  SCOTTLAND_RUNNER_ARTIFACTS=$(scottland_runner_resolve_build_child "$requested_artifacts" "$build") || return 1
  SCOTTLAND_RUNNER_TMPDIR="$SCOTTLAND_HEADLESS_DIR.tmp"

  local paths=("$SCOTTLAND_HEADLESS_DIR" "$SCOTTLAND_RUNNER_TMPDIR" "$SCOTTLAND_RUNNER_ARTIFACTS")
  local i j path
  for ((i=0; i<${#paths[@]}; i++)); do
    path=${paths[i]}
    for ((j=0; j<i; j++)); do
      [[ $path != "${paths[j]}" ]] || { scottland_runner_fail 'headless, TMPDIR, and evidence paths must be distinct'; return 1; }
    done
    [[ ! -e $path && ! -L $path ]] || { scottland_runner_fail "path already exists: $path"; return 1; }
  done

  SCOTTLAND_HEADLESS_OWNER=$(python3 -c 'import uuid; print(uuid.uuid4())') || return 1
  SCOTTLAND_RUNNER_RUNTIME=$(realpath -e -- "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}") || return 1
  SCOTTLAND_RUNNER_XDG_WAS_SET=${XDG_RUNTIME_DIR+x}
  SCOTTLAND_RUNNER_XDG_VALUE=${XDG_RUNTIME_DIR-}

  local old_umask
  old_umask=$(umask)
  umask 077
  if ! mkdir -- "$SCOTTLAND_HEADLESS_DIR"; then umask "$old_umask"; return 1; fi
  mkdir -m 700 -- "$SCOTTLAND_HEADLESS_DIR/runtime" "$SCOTTLAND_HEADLESS_DIR/tmp"
  printf '%s\n%s\n%s\n' "$(id -u)" "$SCOTTLAND_RUNNER_RUNTIME" \
    "$SCOTTLAND_HEADLESS_OWNER" >"$SCOTTLAND_HEADLESS_DIR/.scottland-headless-owner"
  chmod 600 -- "$SCOTTLAND_HEADLESS_DIR/.scottland-headless-owner"
  if ! mkdir -- "$SCOTTLAND_RUNNER_TMPDIR"; then umask "$old_umask"; return 1; fi
  printf '%s\n' "$SCOTTLAND_HEADLESS_OWNER" >"$SCOTTLAND_RUNNER_TMPDIR/.scottland-runner-owner"
  if ! mkdir -- "$SCOTTLAND_RUNNER_ARTIFACTS"; then umask "$old_umask"; return 1; fi
  printf '%s\n' "$SCOTTLAND_HEADLESS_OWNER" >"$SCOTTLAND_RUNNER_ARTIFACTS/.scottland-runner-owner"
  umask "$old_umask"

  export SCOTTLAND_HEADLESS_DIR SCOTTLAND_HEADLESS_OWNER
  export SCOTTLAND_RUNNER_ARTIFACTS
  export TMPDIR="$SCOTTLAND_RUNNER_TMPDIR"
}

scottland_runner_runtime_unchanged() {
  [[ ${XDG_RUNTIME_DIR+x} == "$SCOTTLAND_RUNNER_XDG_WAS_SET" && ${XDG_RUNTIME_DIR-} == "$SCOTTLAND_RUNNER_XDG_VALUE" ]] || {
    scottland_runner_fail 'XDG_RUNTIME_DIR changed during the test; preserve scratch for inspection'
    return 1
  }
}

scottland_runner_cleanup() {
  local status=$? stop_failed=0
  trap - EXIT
  [[ -n ${SCOTTLAND_HEADLESS_OWNER:-} ]] || exit "$status"

  if ! scottland_runner_runtime_unchanged; then
    printf 'preserving runner scratch at %s and %s\n' "$SCOTTLAND_HEADLESS_DIR" "${SCOTTLAND_RUNNER_TMPDIR:-unset}" >&2
    exit 1
  fi

  if [[ -e $SCOTTLAND_HEADLESS_DIR || -L $SCOTTLAND_HEADLESS_DIR ]]; then
    if ! scottland_runner_owned_dir "$SCOTTLAND_HEADLESS_DIR" .scottland-headless-owner; then
      printf 'refusing to stop or remove unowned headless scratch: %s\n' "$SCOTTLAND_HEADLESS_DIR" >&2
      exit 1
    fi
    if [[ -e $SCOTTLAND_HEADLESS_DIR/wayfire.log || -L $SCOTTLAND_HEADLESS_DIR/wayfire.log ]]; then
      if [[ ! -f $SCOTTLAND_HEADLESS_DIR/wayfire.log || -L $SCOTTLAND_HEADLESS_DIR/wayfire.log ]] ||
         ! scottland_runner_owned_dir "$SCOTTLAND_RUNNER_ARTIFACTS" .scottland-runner-owner ||
         ! cp -- "$SCOTTLAND_HEADLESS_DIR/wayfire.log" "$SCOTTLAND_RUNNER_ARTIFACTS/wayfire.log"; then
        printf 'could not preserve a regular headless log; scratch remains at %s\n' "$SCOTTLAND_HEADLESS_DIR" >&2
        exit 1
      fi
    fi
    if ! tests/headless.sh stop >/dev/null; then
      stop_failed=1
    fi
  fi

  if ((stop_failed)); then
    printf 'headless stop failed; preserving session, TMPDIR, and evidence at %s\n' "$SCOTTLAND_HEADLESS_DIR" >&2
    exit 1
  fi

  if [[ -e $SCOTTLAND_HEADLESS_DIR || -L $SCOTTLAND_HEADLESS_DIR ]]; then
    printf 'headless stop did not release its verified scratch: %s\n' "$SCOTTLAND_HEADLESS_DIR" >&2
    exit 1
  fi

  if [[ -n ${SCOTTLAND_RUNNER_TMPDIR:-} && ( -e $SCOTTLAND_RUNNER_TMPDIR || -L $SCOTTLAND_RUNNER_TMPDIR ) ]]; then
    if ! scottland_runner_owned_dir "$SCOTTLAND_RUNNER_TMPDIR" .scottland-runner-owner; then
      printf 'refusing to remove unowned TMPDIR: %s\n' "$SCOTTLAND_RUNNER_TMPDIR" >&2
      exit 1
    fi
    rm -rf -- "$SCOTTLAND_RUNNER_TMPDIR"
  fi

  if scottland_runner_owned_dir "$SCOTTLAND_RUNNER_ARTIFACTS" .scottland-runner-owner; then
    rm -f -- "$SCOTTLAND_RUNNER_ARTIFACTS/.scottland-runner-owner"
  fi
  exit "$status"
}
