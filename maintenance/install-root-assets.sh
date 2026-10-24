#!/usr/bin/env bash
# Install root-owned maintenance and deferred reboot assets from the checked-out release.
set -Eeuo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly BRIDGE_DIR
readonly MAINTENANCE_DIR="${BRIDGE_DIR}/maintenance"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

# Install developer CLI once; never install project dependencies or compilers.
if [[ "${OPENCODE_INSTALL_DEV_TOOLS:-1}" == "1" ]]; then
  bash "${BRIDGE_DIR}/scripts/install-dev-tools.sh"
fi

install -m 0750 -o root -g root "${MAINTENANCE_DIR}/daily-maintenance.sh" /usr/local/sbin/opencode-bridge-maintenance
install -m 0750 -o root -g root "${MAINTENANCE_DIR}/reboot-guard.sh" /usr/local/sbin/opencode-bridge-reboot-guard
install -m 0750 -o root -g root "${MAINTENANCE_DIR}/resource-optimizer.sh" /usr/local/sbin/opencode-bridge-resource-optimizer
install -m 0750 -o root -g root "${MAINTENANCE_DIR}/opencode-bridge-tools.py" /usr/local/sbin/opencode-bridge-tools
python3 -c 'import pathlib; compile(pathlib.Path("/usr/local/sbin/opencode-bridge-tools").read_text(), "opencode-bridge-tools", "exec")'
install -m 0644 -o root -g root "${MAINTENANCE_DIR}/opencode-bridge-resource-optimizer.service" /etc/systemd/system/opencode-bridge-resource-optimizer.service
install -m 0644 -o root -g root "${MAINTENANCE_DIR}/opencode-bridge-resource-optimizer.timer" /etc/systemd/system/opencode-bridge-resource-optimizer.timer
install -m 0644 -o root -g root "${MAINTENANCE_DIR}/opencode-bridge-maintenance.service" /etc/systemd/system/opencode-bridge-maintenance.service
install -m 0644 -o root -g root "${MAINTENANCE_DIR}/opencode-bridge-maintenance.timer" /etc/systemd/system/opencode-bridge-maintenance.timer
install -m 0644 -o root -g root "${MAINTENANCE_DIR}/opencode-bridge-reboot-guard.service" /etc/systemd/system/opencode-bridge-reboot-guard.service

install -m 0440 -o root -g root "${MAINTENANCE_DIR}/opencode-bridge-admin.sudoers" /etc/sudoers.d/opencode-bridge-admin
visudo -cf /etc/sudoers.d/opencode-bridge-admin

# Owner-initiated reboot from the Telegram panel is opt-in and idempotent:
#   OPENCODE_ENABLE_POWER_CONTROL=1  install the single-command sudoers rule
#   OPENCODE_ENABLE_POWER_CONTROL=0  remove it
#   (unset)                          leave the current state alone, so the daily
#                                    maintenance run never silently reverts the choice
readonly POWER_SUDOERS="/etc/sudoers.d/opencode-bridge-power"
case "${OPENCODE_ENABLE_POWER_CONTROL:-}" in
  1)
    # Validate the source first: a broken file left in /etc/sudoers.d would disable sudo.
    visudo -cf "${MAINTENANCE_DIR}/opencode-bridge-power.sudoers"
    install -m 0440 -o root -g root "${MAINTENANCE_DIR}/opencode-bridge-power.sudoers" "$POWER_SUDOERS"
    echo "power_control=enabled"
    ;;
  0)
    rm -f "$POWER_SUDOERS"
    echo "power_control=disabled"
    ;;
esac

systemctl daemon-reload
systemctl enable opencode-bridge-resource-optimizer.service >/dev/null
systemctl enable --now opencode-bridge-resource-optimizer.timer >/dev/null
systemctl restart opencode-bridge-resource-optimizer.service
systemctl enable --now opencode-bridge-maintenance.timer >/dev/null

echo "root_maintenance_assets=installed"
