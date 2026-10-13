"""DNS rebinding prevention at the real managed HTTP download dial boundary."""

from __future__ import annotations

import asyncio
import socket
import unittest
from unittest.mock import AsyncMock, patch

from bridge.services.pinned_public_transport import (
    PublicOnlyHTTPTransport,
    PublicOnlyNetworkBackend,
)


class _FakeNetwork:
    def __init__(self) -> None:
        self.attempts: list[tuple[str, int]] = []

    async def connect_tcp(self, host, port, *args):
        self.attempts.append((host, port))
        return object()


def _answer(*addresses: str) -> list[tuple]:
    return [
        (socket.AF_INET6 if ":" in address else socket.AF_INET,
         socket.SOCK_STREAM, 6, "", (address, 443))
        for address in addresses
    ]


class PublicDialSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_dials_validated_numeric_ip_not_original_hostname(self) -> None:
        fake = _FakeNetwork()
        backend = PublicOnlyNetworkBackend(fake)
        loop = asyncio.get_running_loop()
        with patch.object(loop, "getaddrinfo", new=AsyncMock(return_value=_answer(
            "93.184.216.34", "2606:4700:4700::1111",
        ))) as resolver:
            await backend.connect_tcp("www.example.test", 443, 5.0)
        resolver.assert_awaited_once_with(
            "www.example.test", 443, type=socket.SOCK_STREAM,
        )
        self.assertEqual(fake.attempts, [("93.184.216.34", 443)])

    async def test_private_or_metadata_dns_never_reaches_dial(self) -> None:
        for address in (
            "127.0.0.1", "::1", "10.0.0.2", "192.168.0.8",
            "169.254.169.254", "fe80::1", "0.0.0.0",
        ):
            with self.subTest(address=address):
                fake = _FakeNetwork()
                loop = asyncio.get_running_loop()
                with patch.object(loop, "getaddrinfo",
                                  new=AsyncMock(return_value=_answer(address))):
                    with self.assertRaisesRegex(OSError, "Public DNS validation failed"):
                        await PublicOnlyNetworkBackend(fake).connect_tcp("rebind.test", 443)
                self.assertFalse(fake.attempts)

    async def test_mixed_public_and_private_dns_answers_are_rejected(self) -> None:
        fake = _FakeNetwork()
        with patch.object(asyncio.get_running_loop(), "getaddrinfo",
                          new=AsyncMock(return_value=_answer(
                              "93.184.216.34", "169.254.169.254",
                          ))):
            with self.assertRaises(OSError):
                await PublicOnlyNetworkBackend(fake).connect_tcp("mixed.test", 80)
        self.assertFalse(fake.attempts)

    async def test_dns_failure_never_attempts_a_connection(self) -> None:
        fake = _FakeNetwork()
        with patch.object(asyncio.get_running_loop(), "getaddrinfo",
                          new=AsyncMock(side_effect=socket.gaierror("unavailable"))):
            with self.assertRaises(OSError):
                await PublicOnlyNetworkBackend(fake).connect_tcp("missing.test", 80)
        self.assertFalse(fake.attempts)

    async def test_unix_socket_bypass_is_denied(self) -> None:
        with self.assertRaises(OSError):
            await PublicOnlyNetworkBackend(_FakeNetwork()).connect_unix_socket(
                "/run/docker.sock",
            )

    async def test_http_transport_fail_closed_on_pinned_backend_contract(self) -> None:
        transport = PublicOnlyHTTPTransport()
        try:
            self.assertIsInstance(
                transport._pool._network_backend, PublicOnlyNetworkBackend,
            )
        finally:
            await transport.aclose()


if __name__ == "__main__":
    unittest.main()
