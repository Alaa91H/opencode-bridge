#!/usr/bin/env bash
# One-time provisioning of repository editing and GitHub CI observation tools.
# Intentionally no build toolchains, compiler installs, node/npm, Docker, or Gradle.
set -Eeuo pipefail
[[ "$EUID" == "0" ]] || { echo "Root required" >&2; exit 1; }
[[ -f /etc/debian_version ]] || { echo "Unsupported OS: Debian/Ubuntu required" >&2; exit 1; }
packages=(git gh jq ripgrep curl ca-certificates python3 python3-venv yt-dlp aria2 ffmpeg age)
missing=()
for package in "${packages[@]}"; do
  dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -Fxq 'install ok installed' || missing+=("$package")
done
if (( ${#missing[@]} > 0 )); then
  export DEBIAN_FRONTEND=noninteractive
  apt-get -o Dpkg::Lock::Timeout=300 update
  apt-get -o Dpkg::Lock::Timeout=300 install -y --no-install-recommends "${missing[@]}"
fi
echo "development_tools=ready (no local build toolchain)"
