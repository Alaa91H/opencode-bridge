#!/usr/bin/env python3
"""Create and verify immutable GitHub release artifact manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

_SHA = re.compile(r"[0-9a-f]{40}\Z")
_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:-rc\.[0-9]+)?\Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_manifest(artifact: Path, version: str, source_sha: str) -> dict[str, str]:
    if not _SHA.fullmatch(source_sha):
        raise ValueError("source SHA must be a full lowercase Git commit SHA")
    if not _VERSION.fullmatch(version):
        raise ValueError("version must be a stable or supported preview version")
    if not artifact.is_file():
        raise ValueError("artifact file does not exist")
    return {
        "artifact": artifact.name,
        "sha256": sha256_file(artifact),
        "source_sha": source_sha,
        "version": version,
    }


def verify_manifest(artifact: Path, manifest: dict[str, Any], expected_source_sha: str) -> None:
    if not _SHA.fullmatch(expected_source_sha):
        raise ValueError("expected source SHA must be a full lowercase Git commit SHA")
    if manifest.get("source_sha") != expected_source_sha:
        raise ValueError("artifact source SHA does not match the requested commit")
    if manifest.get("artifact") != artifact.name:
        raise ValueError("artifact filename does not match its manifest")
    if manifest.get("sha256") != sha256_file(artifact):
        raise ValueError("artifact checksum does not match its manifest")
    if not isinstance(manifest.get("version"), str) or not _VERSION.fullmatch(manifest["version"]):
        raise ValueError("artifact version is invalid")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("artifact", type=Path)
    create.add_argument("version")
    create.add_argument("source_sha")
    create.add_argument("manifest", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("artifact", type=Path)
    verify.add_argument("manifest", type=Path)
    verify.add_argument("expected_source_sha")
    args = parser.parse_args()

    if args.command == "create":
        result = create_manifest(args.artifact, args.version, args.source_sha)
        args.manifest.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    else:
        data = json.loads(args.manifest.read_text(encoding="utf-8"))
        verify_manifest(args.artifact, data, args.expected_source_sha)
        print("artifact_identity=verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
