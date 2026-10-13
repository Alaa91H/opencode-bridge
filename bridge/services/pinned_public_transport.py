"""Pin validated public DNS answers to the TCP dial of managed HTTP downloads.

Validating DNS first and letting a separate connector resolve again permits a
DNS-rebinding gap. This backend resolves once per actual TCP connection, rejects
the whole answer if any address is non-public, and dials an approved numeric IP.

The original HTTP host and TLS SNI remain owned by httpcore; this module never
replaces the URL host or disables TLS certificate verification. It depends on
the exact httpx 0.28.1 / httpcore 1.0.9 versions pinned in requirements.lock.
It does not sandbox external subprocess tools such as yt-dlp.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from collections.abc import Iterable

import httpcore
import httpx


class PublicOnlyNetworkBackend(httpcore.AsyncNetworkBackend):
    """Network backend that refuses any private or ambiguous DNS answer."""

    def __init__(self, backend: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._backend = backend if backend is not None else httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[tuple[int, int, int | bytes]] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM,
            )
            if not infos or len(infos) > 64:
                raise OSError("No bounded public DNS response")
            destinations: list[str] = []
            for info in infos:
                address = ipaddress.ip_address(info[4][0])
                # A mixed public/private answer is unsafe, not a fallback.
                if not address.is_global:
                    raise OSError("Destination is not publicly routable")
                ip_text = str(address)
                if ip_text not in destinations:
                    destinations.append(ip_text)
        except (OSError, ValueError) as exc:
            raise OSError("Public DNS validation failed") from None

        last_failure: Exception | None = None
        for address in destinations[:4]:
            try:
                # Numeric IP: the socket backend cannot re-resolve the original
                # hostname to loopback, metadata or a private network.
                return await self._backend.connect_tcp(
                    address, port, timeout, local_address, socket_options,
                )
            except (OSError, httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                last_failure = exc
        raise OSError("Connection to validated public addresses failed") from last_failure

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None,
        socket_options: Iterable[tuple[int, int, int | bytes]] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise OSError("Unix sockets are not valid public download destinations")


class PublicOnlyHTTPTransport(httpx.AsyncHTTPTransport):
    """HTTPX transport whose actual TCP dial enforces public-IP pinning."""

    def __init__(self) -> None:
        super().__init__(trust_env=False)
        pool = getattr(self, "_pool", None)
        if not isinstance(pool, httpcore.AsyncConnectionPool) or not hasattr(
            pool, "_network_backend",
        ):
            # Never silently fall back to an unprotected HTTP connector after
            # a future httpx/httpcore compatibility change.
            raise RuntimeError("Pinned public network backend is not supported")
        pool._network_backend = PublicOnlyNetworkBackend()
