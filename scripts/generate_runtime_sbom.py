#!/usr/bin/env python3
"""Generate a validated SPDX 2.3 inventory of locked Python runtime packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path

_LOCKED_LINE = re.compile(
    r"(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]*)=="
    r"(?P<version>[A-Za-z0-9][A-Za-z0-9._+!-]*)"
    r"(?:\s*;\s*(?P<marker>.+))?\Z",
)
_CANONICAL = re.compile(r"[-_.]+")


def locked_packages(text: str, *, python_version: tuple[int, int] | None = None) -> dict[str, str]:
    """Fail closed on unknown or unpinned requirements and duplicate packages."""
    version = python_version or sys.version_info[:2]
    found: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LOCKED_LINE.fullmatch(line)
        if match is None:
            raise ValueError("Unpinned or invalid runtime requirement")
        marker = match.group("marker")
        if marker is not None:
            if marker != 'python_version < "3.15"':
                raise ValueError("Unknown environment marker in runtime lock")
            if version >= (3, 15):
                continue
        name = _CANONICAL.sub("-", match.group("name")).lower()
        if name in found:
            raise ValueError("Duplicate package in runtime lock")
        found[name] = match.group("version")
    if not found:
        raise ValueError("Runtime lock contains no packages")
    return dict(sorted(found.items()))


def generate(lock: Path, *, revision: str = "") -> dict:
    data = lock.read_bytes()
    locked = locked_packages(data.decode("utf-8"))
    packages = []
    relationships = []
    for name, expected_version in locked.items():
        actual_version = metadata.version(name)
        if actual_version != expected_version:
            raise ValueError(f"Installed package mismatch: {name}")
        spdx_id = "SPDXRef-Package-" + name
        packages.append({
            "SPDXID": spdx_id,
            "name": name,
            "versionInfo": actual_version,
            "downloadLocation": "NOASSERTION",
            "filesAnalyzed": False,
            "externalRefs": [{
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": f"pkg:pypi/{name}@{actual_version}",
            }],
        })
        relationships.append({
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": spdx_id,
        })
    lock_sha = hashlib.sha256(data).hexdigest()
    if revision and re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise ValueError("Invalid source commit identity")
    identity = revision or lock_sha
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "opencode-bridge-python-runtime",
        "documentNamespace": f"https://github.com/Alaa91H/opencode-bridge/sbom/{identity}",
        "documentComment": f"requirements.lock sha256={lock_sha}",
        "creationInfo": {
            "creators": ["Tool: opencode-bridge-runtime-sbom/1"],
            "created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "packages": packages,
        "relationships": relationships,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path("requirements.lock"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--revision", type=str, default="")
    args = parser.parse_args()
    result = generate(args.lock, revision=args.revision)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"SBOM validated: {len(result['packages'])} packages")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
