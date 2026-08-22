#!/usr/bin/env bash
# Install root-owned maintenance and deferred reboot assets from the checked-out release.
set -Eeuo pipefail

readonly BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly MAINTENANCE_DIR="${BRIDGE_DIR}/maintenance"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

install -m 0750 -o root -g root "${MAINTENANCE_DIR}/daily-maintenance.sh" /usr/local/sbin/opencode-bridge-maintenance
install -m 0750 -o root -g root "${MAINTENANCE_DIR}/reboot-guard.sh" /usr/local/sbin/opencode-bridge-reboot-guard
install -m 0644 -o root -g root "${MAINTENANCE_DIR}/opencode-bridge-maintenance.service" /etc/systemd/system/opencode-bridge-maintenance.service
install -m 0644 -o root -g root "${MAINTENANCE_DIR}/opencode-bridge-maintenance.timer" /etc/systemd/system/opencode-bridge-maintenance.timer
install -m 0644 -o root -g root "${MAINTENANCE_DIR}/opencode-bridge-reboot-guard.service" /etc/systemd/system/opencode-bridge-reboot-guard.service

systemctl daemon-reload
systemctl enable opencode-bridge-maintenance.timer >/dev/null

echo "root_maintenance_assets=installed"
