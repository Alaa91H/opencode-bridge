#!/usr/bin/env bash
# تشغيل محلي يدوي فقط؛ يفضّل استخدام systemd في بيئة الإنتاج.
set -euo pipefail

BRIDGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$BRIDGE_DIR"

if [[ ! -x "$BRIDGE_DIR/venv/bin/python" ]]; then
  echo "بيئة Python الجاهزة غير موجودة. لا تثبّت أو تبنِ على الخادم؛ انشر إصدارًا جاهزًا يتضمن البيئة المطلوبة."
  exit 1
fi

if [[ ! -f "$BRIDGE_DIR/.env" ]]; then
  echo "ملف الإعدادات .env غير موجود. انسخ .env.example واضبط القيم السرية محليًا."
  exit 1
fi

set -a
# shellcheck disable=SC1091
source "$BRIDGE_DIR/.env"
set +a

if ! curl --fail --silent --show-error --max-time 10 \
  --user "${OPENCODE_SERVER_USERNAME:-opencode}:${OPENCODE_SERVER_PASSWORD:?OPENCODE_SERVER_PASSWORD مطلوب}" \
  "http://${OPENCODE_HOST:-127.0.0.1}:${OPENCODE_PORT:-4096}/global/health" >/dev/null; then
  echo "وكيل OpenCode غير متاح أو أن مصادقته غير صحيحة. شغّل خدمة opencode-serve ثم أعد المحاولة."
  exit 1
fi

exec "$BRIDGE_DIR/venv/bin/python" -m bot
