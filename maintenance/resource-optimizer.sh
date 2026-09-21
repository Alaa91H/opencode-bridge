#!/usr/bin/env bash
# Safe, idempotent host-memory optimizer for small VPS deployments.
# It prefers ZRAM, adds a bounded disk swap fallback only when safe, and never
# overwrites an existing unknown swap file.
set -Eeuo pipefail

readonly BRIDGE_DIR="/home/ubuntu/opencode-bridge"
readonly RUNTIME_DIR="${BRIDGE_DIR}/runtime"
readonly REPORT_PATH="${RUNTIME_DIR}/resource-latest.md"
readonly LOCK_PATH="/run/lock/opencode-bridge-resource-optimizer.lock"
readonly SWAPFILE="${OPENCODE_SWAPFILE_PATH:-/swapfile}"
readonly ZRAM_PERCENT="${OPENCODE_ZRAM_PERCENT:-50}"
# 0 means no artificial cap. The default target is exactly half of physical RAM.
readonly ZRAM_MAX_MIB="${OPENCODE_ZRAM_MAX_MIB:-0}"
readonly SWAPFILE_GIB="${OPENCODE_SWAPFILE_GIB:-1}"
readonly MIN_FREE_GIB="${OPENCODE_SWAP_MIN_FREE_GIB:-4}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "This script must run as root." >&2
  exit 1
fi

mkdir -p "$RUNTIME_DIR"
exec 9>"$LOCK_PATH"
if ! flock -n 9; then
  echo "resource optimizer skipped: another run is active"
  exit 0
fi

log() { printf '[resource-optimizer] %s\n' "$*"; }
clamp_int() {
  local value="$1" min="$2" max="$3"
  [[ "$value" =~ ^[0-9]+$ ]] || value="$min"
  (( value < min )) && value="$min"
  (( value > max )) && value="$max"
  printf '%s' "$value"
}

zram_percent="$(clamp_int "$ZRAM_PERCENT" 25 100)"
if [[ "$ZRAM_MAX_MIB" =~ ^[0-9]+$ ]]; then
  zram_max_mib="$ZRAM_MAX_MIB"
else
  zram_max_mib=0
fi
swapfile_gib="$(clamp_int "$SWAPFILE_GIB" 0 8)"
min_free_gib="$(clamp_int "$MIN_FREE_GIB" 2 32)"

zram_status="not configured"
swap_status="unchanged"

active_swap_names() {
  swapon --show=NAME --noheadings 2>/dev/null | sed '/^[[:space:]]*$/d' || true
}

zram_target_mib() {
  local mem_kib target_mib
  mem_kib="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
  [[ "$mem_kib" =~ ^[0-9]+$ ]] || return 1
  target_mib="$(( mem_kib * zram_percent / 100 / 1024 ))"
  (( target_mib < 32 )) && target_mib=32
  if (( zram_max_mib > 0 && target_mib > zram_max_mib )); then
    target_mib="$zram_max_mib"
  fi
  printf '%s' "$target_mib"
}

configure_zram_device() {
  local target_mib="$1"
  if [[ -r /sys/block/zram0/comp_algorithm ]]; then
    algorithms="$(cat /sys/block/zram0/comp_algorithm 2>/dev/null || true)"
    if grep -qw zstd <<<"$algorithms"; then
      echo zstd > /sys/block/zram0/comp_algorithm 2>/dev/null || true
    elif grep -qw lz4 <<<"$algorithms"; then
      echo lz4 > /sys/block/zram0/comp_algorithm 2>/dev/null || true
    fi
  fi

  [[ -w /sys/block/zram0/disksize ]] || return 1
  echo "$(( target_mib * 1024 * 1024 ))" > /sys/block/zram0/disksize
  mkswap -f /dev/zram0 >/dev/null
  swapon -p 100 /dev/zram0
  zram_status="active (${target_mib} MiB = ${zram_percent}% RAM, priority 100)"
}

ensure_zram() {
  local target_mib target_bytes current_bytes used_bytes
  target_mib="$(zram_target_mib)" || return 1
  target_bytes="$(( target_mib * 1024 * 1024 ))"

  if [[ ! -b /dev/zram0 ]]; then
    modprobe zram num_devices=1 >/dev/null 2>&1 || true
  fi
  [[ -b /dev/zram0 ]] || return 1

  current_bytes="$(cat /sys/block/zram0/disksize 2>/dev/null || echo 0)"
  if active_swap_names | grep -Fxq '/dev/zram0'; then
    if [[ "$current_bytes" == "$target_bytes" ]]; then
      zram_status="active (${target_mib} MiB = ${zram_percent}% RAM, priority 100)"
      return 0
    fi

    # Resize immediately only when no swap pages are in use. Otherwise keep the
    # active device untouched and apply the new target safely on the next boot.
    used_bytes="$(swapon --show=NAME,USED --bytes --noheadings 2>/dev/null | awk '$1=="/dev/zram0" {print $2; exit}')"
    used_bytes="${used_bytes:-0}"
    if [[ "$used_bytes" =~ ^[0-9]+$ ]] && (( used_bytes == 0 )); then
      swapoff /dev/zram0
      if [[ -w /sys/block/zram0/reset ]]; then
        echo 1 > /sys/block/zram0/reset
      else
        zramctl --reset /dev/zram0 >/dev/null 2>&1 || true
      fi
      configure_zram_device "$target_mib"
      zram_status="resized and active (${target_mib} MiB = ${zram_percent}% RAM, priority 100)"
      return 0
    fi

    current_mib="$(( current_bytes / 1024 / 1024 ))"
    zram_status="active (${current_mib} MiB); target ${target_mib} MiB deferred until reboot because ZRAM is in use"
    return 0
  fi

  if (( current_bytes > 0 )); then
    if [[ -w /sys/block/zram0/reset ]]; then
      echo 1 > /sys/block/zram0/reset
    else
      zramctl --reset /dev/zram0 >/dev/null 2>&1 || true
    fi
  fi
  configure_zram_device "$target_mib"
}

ensure_swapfile() {
  (( swapfile_gib > 0 )) || { swap_status="disabled by configuration"; return 0; }
  if active_swap_names | grep -Fxq "$SWAPFILE"; then
    swap_status="already active"
    return 0
  fi

  if [[ -e "$SWAPFILE" ]]; then
    if swapon -p 10 "$SWAPFILE" >/dev/null 2>&1; then
      swap_status="existing swap file activated"
    else
      swap_status="existing file left untouched because it is not valid swap"
    fi
    return 0
  fi

  free_kib="$(df -Pk / | awk 'NR==2 {print $4}')"
  required_kib="$(( min_free_gib * 1024 * 1024 ))"
  if (( free_kib < required_kib )); then
    swap_status="not created: free disk below ${min_free_gib} GiB safety threshold"
    return 0
  fi

  if command -v fallocate >/dev/null 2>&1; then
    fallocate -l "${swapfile_gib}G" "$SWAPFILE"
  else
    dd if=/dev/zero of="$SWAPFILE" bs=1M count="$(( swapfile_gib * 1024 ))" status=none
  fi
  chmod 600 "$SWAPFILE"
  chown root:root "$SWAPFILE"
  mkswap "$SWAPFILE" >/dev/null
  swapon -p 10 "$SWAPFILE"
  swap_status="created and active (${swapfile_gib} GiB, priority 10)"
}

apply_vm_tuning() {
  sysctl -w vm.swappiness=100 >/dev/null 2>&1 || true
  sysctl -w vm.vfs_cache_pressure=100 >/dev/null 2>&1 || true
  sysctl -w vm.page-cluster=0 >/dev/null 2>&1 || true
}

if ensure_zram; then
  log "zram: $zram_status"
else
  zram_status="unavailable on this kernel"
  log "zram unavailable; continuing safely"
fi
ensure_swapfile
apply_vm_tuning

mem_summary="$(free -h | awk '/^Mem:/ {print $3 " used / " $2 " total; " $7 " available"}')"
swap_summary="$(free -h | awk '/^Swap:/ {print $3 " used / " $2 " total"}')"
pressure="unknown"
if [[ -r /proc/pressure/memory ]]; then
  pressure="$(tr '\n' ';' < /proc/pressure/memory | sed 's/;$/ /')"
fi

report_tmp="$(mktemp "${RUNTIME_DIR}/.resource.XXXXXX")"
{
  printf '# Host resource status\n\n'
  printf '| Item | Status |\n|---|---|\n'
  printf '| ZRAM | %s |\n' "$zram_status"
  printf '| Disk swap | %s |\n' "$swap_status"
  printf '| Memory | %s |\n' "$mem_summary"
  printf '| Swap | %s |\n' "$swap_summary"
  printf '| vm.swappiness | %s |\n' "$(sysctl -n vm.swappiness 2>/dev/null || echo unknown)"
  printf '| Memory PSI | %s |\n' "$pressure"
  printf '\nUpdated: %s\n' "$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
} > "$report_tmp"
install -m 0640 -o ubuntu -g ubuntu "$report_tmp" "$REPORT_PATH"
rm -f "$report_tmp"

log "zram=$zram_status; disk_swap=$swap_status; $swap_summary"
