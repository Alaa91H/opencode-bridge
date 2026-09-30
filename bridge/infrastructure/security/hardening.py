"""Security hardening policies for local services, files and outputs."""

from __future__ import annotations

import ipaddress
from pathlib import Path

SYSTEMD_HARDENING = (
    "NoNewPrivileges=true",
    "PrivateTmp=true",
    "ProtectSystem=strict",
    "ProtectHome=true",
    "CapabilityBoundingSet=",
    "RestrictSUIDSGID=true",
)


def require_loopback_opencode(url: str) -> None:
    from urllib.parse import urlparse
    host = urlparse(url).hostname
    if host not in {"localhost", "127.0.0.1", "::1"}:
        try:
            if host is None or not ipaddress.ip_address(host).is_loopback:
                raise ValueError("OpenCode must bind to loopback")
        except ValueError as exc:
            raise ValueError("OpenCode must bind to loopback") from exc


def safe_output_path(root: Path, relative: str) -> Path:
    root = root.resolve()
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("output escapes jail")
    if candidate.is_symlink():
        raise ValueError("symlink output denied")
    return candidate


def inspect_magic(header: bytes) -> str:
    signatures = {
        b"%PDF-": "application/pdf",
        b"PK\x03\x04": "application/zip",
        b"\x89PNG\r\n\x1a\n": "image/png",
        b"\xff\xd8\xff": "image/jpeg",
    }
    for signature, mime in signatures.items():
        if header.startswith(signature):
            return mime
    return "application/octet-stream"


def subprocess_timeout(requested: float, *, maximum: float = 3600.0) -> float:
    if requested <= 0 or maximum <= 0:
        raise ValueError("runtime must be positive")
    return min(requested, maximum)
