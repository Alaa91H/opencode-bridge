#!/usr/bin/env python3
"""Verify encrypted OpenCode Bridge backups on a separate trusted computer.

Usage: python scripts/verify-encrypted-backup.py backup-YYYYMMDDTHHMMSS-abcdefgh.age \
       /path/to/offline-age-identity.txt

Do not put the age private key on the Bridge production server.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bridge.services.backup_recovery import verify_encrypted_backup  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path, help="Encrypted backup *.age (plus companion *.age.json)")
    parser.add_argument("identity", type=Path, help="Private age identity on the OFFLINE computer")
    args = parser.parse_args()
    try:
        result = verify_encrypted_backup(args.backup, args.identity)
    except (OSError, ValueError):
        print(json.dumps({"status": "failed", "reason": "verification rejected archive or identity"}))
        return 1
    print(json.dumps({"status": "verified", **asdict(result)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
