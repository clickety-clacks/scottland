#!/usr/bin/bash
# pool {"memory_mib":5120,"cpus":3,"seconds":7200}
set -Eeuo pipefail
umask 077

diagnostic_mode=${SCOTTLAND_DISPLAY_GUEST_DIAGNOSTIC:-0}
[[ $diagnostic_mode == 0 || $diagnostic_mode == 1 ]] || {
  printf 'display-v5 guest launcher: diagnostic mode must be 0 or 1\n' >&2
  exit 2
}
launcher_budget=6600
qemu_budget=5400
if [[ $diagnostic_mode == 1 ]]; then
  launcher_budget=240
  qemu_budget=120
fi
stage_name=launcher-start

stage() {
  stage_name=$1
  printf 'SCOTTLAND_STAGE=%s elapsed_seconds=%s\n' "$stage_name" "$SECONDS"
}

trap 'status=$?; printf "SCOTTLAND_STAGE_FAILED=%s status=%s line=%s\\n" "$stage_name" "$status" "$LINENO" >&2' ERR

fail() {
  printf 'display-v5 guest launcher: %s\n' "$*" >&2
  exit 1
}

if [[ ${1:-} == --bounded-run ]]; then
  shift
  (($# == 0)) || fail 'unexpected argument to bounded launcher'
else
  (($# == 0)) || fail 'launcher does not accept arguments'
  command -v timeout >/dev/null 2>&1 || fail 'GNU timeout is unavailable'
  printf 'SCOTTLAND_LAUNCHER_BUDGET_SECONDS=%s (pool limit: 7200) diagnostic=%s\n' \
    "$launcher_budget" "$diagnostic_mode"
  exec timeout --foreground --signal=USR1 --kill-after=30s "${launcher_budget}s" \
    "$BASH" "$0" --bounded-run
fi

[[ ${POOL_RUN_ID:-} =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] || fail 'missing or invalid pool run id'
pool_root=$(pwd -P)
for parent in "$pool_root/disks" "$pool_root/scratch"; do
  [[ -d $parent && ! -L $parent ]] || fail "required pool directory is unavailable: $parent"
done

disk_dir="$pool_root/disks/$POOL_RUN_ID"
scratch_dir="$pool_root/scratch/$POOL_RUN_ID"
disk_owned=0
scratch_owned=0
qemu_pid=
serial_printed=0
qemu_log_printed=0

print_serial() {
  ((serial_printed == 0)) || return 0
  serial_printed=1
  if [[ -s ${serial_log:-} ]]; then
    printf '\n--- guest serial output ---\n'
    cat -- "$serial_log"
  fi
}

print_qemu_log() {
  ((qemu_log_printed == 0)) || return 0
  if [[ -s ${qemu_log:-} ]]; then
    printf '\n--- QEMU output ---\n'
    cat -- "$qemu_log"
    printf '\n--- end QEMU output ---\n'
  fi
  qemu_log_printed=1
}

cleanup() {
  result=$?
  cleanup_failed=0
  trap - EXIT INT TERM HUP
  trap - USR1
  if [[ -n $qemu_pid ]] && kill -0 "$qemu_pid" 2>/dev/null; then
    kill -TERM "$qemu_pid" 2>/dev/null || true
    wait "$qemu_pid" 2>/dev/null || true
  fi
  print_qemu_log || cleanup_failed=1
  print_serial || cleanup_failed=1
  if ((disk_owned)); then
    if [[ -d $disk_dir && ! -L $disk_dir && $(realpath -e -- "$disk_dir") == "$disk_dir" ]]; then
      rm -rf -- "$disk_dir" || cleanup_failed=1
      [[ ! -e $disk_dir && ! -L $disk_dir ]] || cleanup_failed=1
    else
      printf 'preserving changed run-owned disk path: %s\n' "$disk_dir" >&2
      cleanup_failed=1
    fi
  fi
  if ((scratch_owned)); then
    if [[ -d $scratch_dir && ! -L $scratch_dir && $(realpath -e -- "$scratch_dir") == "$scratch_dir" ]]; then
      rm -rf -- "$scratch_dir" || cleanup_failed=1
      [[ ! -e $scratch_dir && ! -L $scratch_dir ]] || cleanup_failed=1
    else
      printf 'preserving changed run-owned scratch path: %s\n' "$scratch_dir" >&2
      cleanup_failed=1
    fi
  fi
  if ((cleanup_failed)); then
    printf 'SCOTTLAND_LAUNCHER_CLEANUP=FAIL\n' >&2
    result=1
  else
    printf 'SCOTTLAND_LAUNCHER_CLEANUP=PASS\n'
  fi
  exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
trap 'exit 129' HUP
trap 'printf "launcher deadline reached; stopping the run-owned guest\n" >&2; exit 124' USR1

[[ ! -e $disk_dir && ! -L $disk_dir ]] || fail 'run disk directory already exists'
mkdir -m 0700 -- "$disk_dir"
disk_owned=1
[[ ! -e $scratch_dir && ! -L $scratch_dir ]] || fail 'run scratch directory already exists'
mkdir -m 0700 -- "$scratch_dir"
scratch_owned=1

for tool in curl python3 sha256sum qemu-img qemu-system-x86_64; do
  command -v "$tool" >/dev/null 2>&1 || fail "required launcher command is unavailable: $tool"
done

stage 'pool-preflight'
available_kib=$(df -Pk -- "$pool_root" 2>/dev/null | awk 'NR == 2 {print $4}' || true)
if [[ $available_kib =~ ^[0-9]+$ ]] && ((available_kib < 6 * 1024 * 1024)); then
  printf 'warning: less than 6 GiB is free for the run-owned guest image\n' >&2
elif [[ ! $available_kib =~ ^[0-9]+$ ]]; then
  printf 'warning: could not read free space for the run-owned guest image\n' >&2
fi

source_ref_json="$scratch_dir/source-ref.json"
stage 'resolve-current-0.4'
curl --fail --location --retry 3 --retry-all-errors --silent --show-error \
  --connect-timeout 15 --max-time 120 \
  -H 'Accept: application/vnd.github+json' \
  -H 'X-GitHub-Api-Version: 2022-11-28' \
  'https://api.github.com/repos/clickety-clacks/scottland/commits/0.4' \
  --output "$source_ref_json"
read -r source_sha source_tree < <(python3 - "$source_ref_json" <<'PY'
import json
import re
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    commit = json.load(source)
sha = commit["sha"]
tree = commit["commit"]["tree"]["sha"]
if not re.fullmatch(r"[0-9a-f]{40}", sha) or not re.fullmatch(r"[0-9a-f]{40}", tree):
    raise SystemExit("GitHub returned an invalid source or tree SHA")
print(sha, tree)
PY
)
[[ $source_sha =~ ^[0-9a-f]{40}$ && $source_tree =~ ^[0-9a-f]{40}$ ]] || fail 'could not resolve the current 0.4 source'
printf 'Scottland 0.4 source commit: %s\nScottland source tree: %s\n' "$source_sha" "$source_tree"

recipe_url="https://raw.githubusercontent.com/clickety-clacks/scottland/$source_sha/tests/display-v5-guest-image.yaml"
stage 'fetch-guest-recipe'
if [[ -n ${SCOTTLAND_EMBEDDED_RECIPE_B64:-} ]]; then
  python3 - "$scratch_dir/image.yaml.in" <<'PY'
import base64
import os
import sys
from pathlib import Path

Path(sys.argv[1]).write_bytes(base64.b64decode(os.environ['SCOTTLAND_EMBEDDED_RECIPE_B64'], validate=True))
PY
else
  curl --fail --location --retry 3 --retry-all-errors --silent --show-error \
    --connect-timeout 15 --max-time 180 "$recipe_url" --output "$scratch_dir/image.yaml.in"
fi
source_archive="$scratch_dir/scottland-source.tar.gz"
stage 'fetch-source-archive'
curl --fail --location --retry 3 --retry-all-errors --silent --show-error \
  --connect-timeout 15 --max-time 300 \
  "https://codeload.github.com/clickety-clacks/scottland/tar.gz/$source_sha" \
  --output "$source_archive"
source_archive_sha256=$(sha256sum -- "$source_archive" | awk '{print $1}')
[[ $source_archive_sha256 =~ ^[0-9a-f]{64}$ ]] || fail 'could not hash the pinned Scottland source archive'

python3 - "$scratch_dir/image.yaml.in" "$scratch_dir/user-data" \
  "$source_sha" "$source_tree" "$source_archive_sha256" <<'PY'
from pathlib import Path
import os
import re
import sys

template = Path(sys.argv[1]).read_text(encoding="utf-8")
replacements = {
    "@@SCOTTLAND_SOURCE_SHA@@": sys.argv[3],
    "@@SCOTTLAND_SOURCE_TREE@@": sys.argv[4],
    "@@SCOTTLAND_SOURCE_ARCHIVE_SHA256@@": sys.argv[5],
    "@@SCOTTLAND_DIAGNOSTIC_ONLY@@": os.environ.get("SCOTTLAND_DISPLAY_GUEST_DIAGNOSTIC", "0"),
}
for marker, value in replacements.items():
    if template.count(marker) != 1:
        raise SystemExit(f"expected exactly one {marker} marker in the image recipe")
    template = template.replace(marker, value)
if re.search(r"@@[A-Z0-9_]+@@", template):
    raise SystemExit("image recipe contains an unresolved template marker")
Path(sys.argv[2]).write_text(template, encoding="utf-8")
PY

printf 'instance-id: scottland-display-%s\n' "$POOL_RUN_ID" >"$scratch_dir/meta-data"
mkdir -m 0700 -- "$scratch_dir/seed" "$scratch_dir/source"
install -m 0600 -- "$scratch_dir/user-data" "$scratch_dir/seed/user-data"
install -m 0600 -- "$scratch_dir/meta-data" "$scratch_dir/seed/meta-data"
install -m 0444 -- "$source_archive" "$scratch_dir/source/scottland-source.tar.gz"

base_name=Arch-Linux-x86_64-cloudimg.qcow2
base_url=https://geo.mirror.pkgbuild.com/images/latest
base_image="$scratch_dir/$base_name"
base_checksum_file="$scratch_dir/$base_name.SHA256"
stage 'fetch-base-cloud-image'
curl --fail --location --retry 3 --retry-all-errors --silent --show-error \
  --connect-timeout 15 --max-time 300 "$base_url/$base_name" --output "$base_image"
stage 'fetch-base-checksum'
curl --fail --location --retry 3 --retry-all-errors --silent --show-error \
  --connect-timeout 15 --max-time 60 "$base_url/$base_name.SHA256" --output "$base_checksum_file"
base_expected_sha256=$(awk 'NF {print $1; exit}' "$base_checksum_file")
[[ $base_expected_sha256 =~ ^[0-9a-fA-F]{64}$ ]] || fail 'official cloud image checksum is malformed'
base_actual_sha256=$(sha256sum -- "$base_image" | awk '{print $1}')
[[ ${base_actual_sha256,,} == ${base_expected_sha256,,} ]] || fail 'official cloud image checksum did not match'
printf 'Base cloud image SHA-256: %s\n' "$base_actual_sha256"

overlay="$disk_dir/guest.qcow2"
printf 'QEMU version: %s\n' "$(qemu-system-x86_64 --version | head -n 1)"
printf 'qemu-img version: %s\n' "$(qemu-img --version | head -n 1)"
stage 'create-run-owned-qcow2'
timeout --foreground --signal=TERM --kill-after=10s 180s \
  qemu-img create -f qcow2 -F qcow2 -b "$base_image" "$overlay" 24G
serial_log="$scratch_dir/guest-serial.log"
qemu_log="$scratch_dir/qemu.log"
monitor_socket="$scratch_dir/qemu-monitor.sock"
monitor_log="$scratch_dir/qemu-monitor.log"
screen_dump="$scratch_dir/qemu-screen.ppm"
monitor_backend=none
if [[ $diagnostic_mode == 1 ]]; then
  monitor_backend="unix:$monitor_socket,server=on,wait=off"
fi
stage 'start-qemu-guest'
timeout --foreground --signal=TERM --kill-after=20s "${qemu_budget}s" qemu-system-x86_64 \
  -machine type=q35,accel=kvm \
  -cpu host \
  -smp 2 \
  -m 4096 \
  -display none \
  -monitor "$monitor_backend" \
  -serial "file:$serial_log" \
  -drive "file=$overlay,format=qcow2,if=none,id=systemdisk" \
  -device virtio-blk-pci,drive=systemdisk,bootindex=1 \
  -blockdev "driver=vvfat,node-name=seed,dir=$scratch_dir/seed,label=cidata,rw=off,read-only=on" \
  -device virtio-blk-pci,drive=seed \
  -blockdev "driver=vvfat,node-name=source,dir=$scratch_dir/source,label=SCOTT_SRC,rw=off,read-only=on" \
  -device virtio-blk-pci,drive=source \
  -nic user,model=e1000 \
  -no-reboot >"$qemu_log" 2>&1 &
qemu_pid=$!
if [[ $diagnostic_mode == 1 ]]; then
  sleep 30
  if python3 - "$monitor_socket" "$screen_dump" >"$monitor_log" 2>&1 <<'PY'
import socket
import sys
import time

socket_path, screen_path = sys.argv[1:]
deadline = time.monotonic() + 10
connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
connection.settimeout(3)
while True:
    try:
        connection.connect(socket_path)
        break
    except OSError:
        if time.monotonic() >= deadline:
            raise
        time.sleep(0.2)

def prompt():
    data = bytearray()
    deadline = time.monotonic() + 5
    while b"(qemu)" not in data:
        if time.monotonic() >= deadline:
            raise TimeoutError("QEMU monitor prompt timed out")
        data.extend(connection.recv(65536))
    return bytes(data)

print(prompt().decode(errors="replace"), end="")
for command in ("info status", "info block", "info cpus", f"screendump {screen_path}"):
    connection.sendall(command.encode() + b"\n")
    print(prompt().decode(errors="replace"), end="")
connection.close()
PY
  then
    cat -- "$monitor_log"
    if [[ -s $screen_dump ]]; then
      printf 'SCOTTLAND_QEMU_SCREEN_SHA256=%s\n' "$(sha256sum -- "$screen_dump" | awk '{print $1}')"
      printf 'SCOTTLAND_QEMU_SCREEN_GZIP_B64='
      gzip -c -- "$screen_dump" | base64 -w 0
      printf '\nSCOTTLAND_QEMU_SCREEN_GZIP_B64_END\n'
    else
      printf 'SCOTTLAND_QEMU_SCREEN_DUMP=MISSING\n'
    fi
  else
    printf 'SCOTTLAND_QEMU_MONITOR_CAPTURE=FAILED\n'
    cat -- "$monitor_log"
  fi
fi
qemu_status=0
wait "$qemu_pid" || qemu_status=$?
qemu_pid=
print_qemu_log
print_serial
printf 'SCOTTLAND_STAGE=qemu-exit status=%s elapsed_seconds=%s\n' "$qemu_status" "$SECONDS"
if ((qemu_status == 124)); then
  fail "QEMU guest stage exceeded ${qemu_budget}s diagnostic=$diagnostic_mode"
fi
((qemu_status == 0)) || fail "QEMU exited with status $qemu_status"

guest_status=$(sed -n 's/^SCOTTLAND_VENUE_PREFLIGHT_EXIT=//p' "$serial_log" | tail -n 1)
[[ $guest_status =~ ^[0-9]{1,3}$ ]] || fail 'guest did not report a completion status on the serial console'
((guest_status <= 255)) || fail 'guest reported an invalid completion status'
if ((guest_status != 0)); then
  fail "guest property preflight exited with status $guest_status"
fi
printf 'Run-owned guest completed with status 0.\n'
