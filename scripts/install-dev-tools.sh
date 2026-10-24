#!/usr/bin/env bash
# One-time provisioning of repository editing and GitHub CI observation tools.
# Intentionally no build toolchains, compiler installs, node/npm, Docker, or Gradle.
set -Eeuo pipefail
[[ "$EUID" == "0" ]] || { echo "Root required" >&2; exit 1; }
[[ -f /etc/debian_version ]] || { echo "Unsupported OS: Debian/Ubuntu required" >&2; exit 1; }
BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
readonly BRIDGE_DIR
readonly TOOL_POLICY="${BRIDGE_DIR}/runtime/tool-policy.json"
packages=(git gh jq ripgrep curl ca-certificates python3 python3-venv yt-dlp aria2 ffmpeg age)

# A package the owner removed from the tool manager must stay removed when the
# daily maintenance run refreshes root assets.  The policy is owned by the
# bridge user, but it only contains short tool keys; the map remains here.
if [[ -f "$TOOL_POLICY" && ! -L "$TOOL_POLICY" ]] && command -v python3 >/dev/null 2>&1; then
  mapfile -t removed_packages < <(
    python3 - "$TOOL_POLICY" <<'PY'
import json
import sys

packages = {
    "git": "git", "gh": "gh", "curl": "curl", "jq": "jq", "rg": "ripgrep",
    "yt-dlp": "yt-dlp", "aria2": "aria2", "ffmpeg": "ffmpeg", "age": "age",
    "poppler": "poppler-utils", "tesseract": "tesseract-ocr",
}
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
    removed = data.get("removed", []) if isinstance(data, dict) else []
except (OSError, ValueError):
    removed = []
for key in removed:
    package = packages.get(str(key))
    if package:
        print(package)
PY
  )
  if (( ${#removed_packages[@]} > 0 )); then
    kept=()
    for package in "${packages[@]}"; do
      skip=0
      for removed in "${removed_packages[@]}"; do
        [[ "$package" == "$removed" ]] && skip=1
      done
      (( skip )) || kept+=("$package")
    done
    packages=("${kept[@]}")
  fi
fi

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
