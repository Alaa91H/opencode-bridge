"""End-to-end tests for managed link downloads.

Every test drives the real service against a real HTTP server on a real socket,
with real files on a real filesystem. Only the Telegram send path is absent,
because that belongs to the command adapter.
"""

import json
import tempfile
import threading
import unittest
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from bridge.services.download_service import (
    DIRECT_TELEGRAM_LIMIT,
    DownloadError,
    DownloadRecord,
    DownloadService,
    DownloadTooLarge,
    validate_url,
)

UTC = UTC
OWNER = "4242"
OTHER = "9999"


def _payload(size: int) -> bytes:
    return bytes((index * 7 + 11) % 251 for index in range(size))


class _FileServer(BaseHTTPRequestHandler):
    """Serves a real body, plus error, redirect, and slow routes."""

    body = b""
    status = 200
    content_type = "application/octet-stream"
    slow = False
    redirect_to: str | None = None
    requests: list[str] = []

    def log_message(self, *args: object) -> None:
        return

    def do_GET(self) -> None:
        type(self).requests.append(self.path)
        if self.redirect_to and self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", self.redirect_to)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/notfound":
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.slow:
            import time

            time.sleep(0.05)
        payload = type(self).body
        self.send_response(type(self).status)
        self.send_header("Content-Type", type(self).content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class DownloadServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "downloads"
        _FileServer.body = b""
        _FileServer.status = 200
        _FileServer.content_type = "application/octet-stream"
        _FileServer.slow = False
        _FileServer.redirect_to = None
        _FileServer.requests = []
        self.server = HTTPServer(("127.0.0.1", 0), _FileServer)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    async def asyncTearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def service(self, **kwargs) -> DownloadService:
        # The loopback server is only reachable because the tests opt in. Production
        # never sets this, and a dedicated test below proves it.
        kwargs.setdefault("allow_private_hosts", True)
        return DownloadService(self.root, **kwargs)

    # ------------------------------------------------------------ happy path

    async def test_downloads_a_real_file_and_stores_it(self) -> None:
        payload = _payload(64 * 1024)
        _FileServer.body = payload
        service = self.service()
        record = await service.fetch(OWNER, f"{self.base}/report.pdf")

        self.assertTrue(Path(record.path).is_file())
        self.assertEqual(Path(record.path).read_bytes(), payload)
        self.assertEqual(record.size_bytes, len(payload))
        self.assertEqual(record.owner_id, OWNER)
        self.assertTrue(record.filename.endswith(".pdf"))
        self.assertTrue(record.sendable_inline)

    async def test_index_survives_a_new_service_instance(self) -> None:
        _FileServer.body = _payload(1024)
        first = self.service()
        record = await first.fetch(OWNER, f"{self.base}/a.bin")
        second = self.service()
        self.assertEqual([item.id for item in second.list(OWNER)], [record.id])

    async def test_records_are_owner_scoped(self) -> None:
        _FileServer.body = _payload(512)
        service = self.service()
        record = await service.fetch(OWNER, f"{self.base}/mine.bin")
        self.assertEqual(len(service.list(OWNER)), 1)
        self.assertEqual(service.list(OTHER), [])
        self.assertIsNone(service.get(OTHER, record.id))
        self.assertFalse(service.delete(OTHER, record.id))
        self.assertTrue(Path(record.path).is_file())

    # ----------------------------------------------------------------- SSRF

    async def test_refuses_non_http_schemes(self) -> None:
        service = self.service()
        for url in ("file:///etc/passwd", "ftp://host/f", "gopher://host/", "data:text/plain,x", "//host/f"):
            with self.subTest(url=url), self.assertRaises(DownloadError):
                await service.fetch(OWNER, url)

    async def test_refuses_internal_and_metadata_addresses(self) -> None:
        # No escape hatch here: this is the production policy.
        service = DownloadService(self.root)
        for url in (
            "http://127.0.0.1:22/",
            "http://localhost/etc/passwd",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.119/",
            "http://192.168.1.1/",
            "http://[::1]/",
            "http://0.0.0.0/",
            "http://router.local/",
        ):
            with self.subTest(url=url), self.assertRaises(DownloadError):
                await service.fetch(OWNER, url)

    def test_validate_url_rejects_empty_and_oversized(self) -> None:
        with self.assertRaises(DownloadError):
            validate_url("")
        with self.assertRaises(DownloadError):
            validate_url("https://example.com/" + "a" * 4000)

    def test_the_strict_default_is_what_production_gets(self) -> None:
        """The test-only escape hatch must never be the default."""
        service = DownloadService(self.root)
        self.assertFalse(service.allow_private_hosts)
        for url in ("http://127.0.0.1/x", "http://169.254.169.254/", "http://10.0.0.119/"):
            with self.subTest(url=url), self.assertRaises(DownloadError):
                validate_url(url)
        # And the escape hatch does not relax the scheme rule.
        with self.assertRaises(DownloadError):
            validate_url("file:///etc/passwd", allow_private_hosts=True)
        with self.assertRaises(DownloadError):
            validate_url("", allow_private_hosts=True)

    # ---------------------------------------------------------------- limits

    async def test_refuses_a_file_larger_than_the_cap(self) -> None:
        _FileServer.body = _payload(64 * 1024)
        service = self.service(max_file_bytes=1024)
        with self.assertRaises(DownloadTooLarge):
            await service.fetch(OWNER, f"{self.base}/big.bin")
        self.assertFalse(any(self.root.rglob("*.part")))

    async def test_refuses_when_the_total_budget_is_spent(self) -> None:
        _FileServer.body = _payload(4096)
        service = self.service(max_file_bytes=4096, max_total_bytes=4096)
        await service.fetch(OWNER, f"{self.base}/one.bin")
        with self.assertRaises(DownloadTooLarge):
            await service.fetch(OWNER, f"{self.base}/two.bin")

    async def test_a_failing_status_is_reported(self) -> None:
        service = self.service()
        with self.assertRaises(DownloadError):
            await service.fetch(OWNER, f"{self.base}/notfound")

    async def test_a_partial_file_is_removed_on_failure(self) -> None:
        _FileServer.body = _payload(32 * 1024)
        service = self.service(max_file_bytes=2048)
        with self.assertRaises(DownloadTooLarge):
            await service.fetch(OWNER, f"{self.base}/overflow.bin")
        leftovers = list(self.root.rglob("*")) if self.root.exists() else []
        self.assertEqual([p for p in leftovers if p.suffix == ".part"], [])
        self.assertEqual(service.list(OWNER), [])

    # ------------------------------------------------------------- filenames

    async def test_filename_is_taken_from_the_server_and_sanitized(self) -> None:
        _FileServer.body = b"x"
        service = self.service()
        record = await service.fetch(OWNER, f"{self.base}/%2e%2e%2f%2e%2e%2fetc%2fpasswd")
        name = Path(record.filename).name
        self.assertNotIn("/", name)
        self.assertNotIn("..", name)
        self.assertTrue(Path(record.path).is_relative_to(service.root))

    # --------------------------------------------------------------- cleanup

    async def test_expired_files_are_removed_and_freed(self) -> None:
        _FileServer.body = _payload(2048)
        service = self.service(ttl_hours=1)
        record = await service.fetch(OWNER, f"{self.base}/temp.bin")
        self.assertTrue(Path(record.path).is_file())

        past = datetime.now(UTC) - timedelta(hours=2)
        self.assertEqual(service.cleanup_expired()["removed"], 0)
        later = datetime.now(UTC) + timedelta(hours=2)
        result = service.cleanup_expired(now=later)
        self.assertEqual(result["removed"], 1)
        self.assertEqual(result["freed_bytes"], record.size_bytes)
        self.assertFalse(Path(record.path).exists())
        self.assertEqual(service.list(OWNER), [])
        self.assertEqual(service.used_bytes(), 0)
        self.assertEqual(past.tzinfo, UTC)

    async def test_cleanup_leaves_unexpired_and_foreign_files_alone(self) -> None:
        _FileServer.body = _payload(1024)
        service = self.service(ttl_hours=24)
        keep = await service.fetch(OWNER, f"{self.base}/keep.bin")
        other = await service.fetch(OTHER, f"{self.base}/theirs.bin")
        # Expire only the other owner's record.
        index = json.loads(service.index_path.read_text(encoding="utf-8"))
        index[other.id]["expires_at"] = (
            datetime.now(UTC) - timedelta(hours=1)
        ).isoformat()
        service._write_index(index)

        result = service.cleanup_expired()
        self.assertEqual(result["removed"], 1)
        self.assertTrue(Path(keep.path).is_file())
        self.assertFalse(Path(other.path).exists())

    async def test_explicit_delete_removes_file_and_index_entry(self) -> None:
        _FileServer.body = _payload(1024)
        service = self.service()
        record = await service.fetch(OWNER, f"{self.base}/gone.bin")
        self.assertTrue(service.delete(OWNER, record.id))
        self.assertFalse(Path(record.path).exists())
        self.assertEqual(service.list(OWNER, include_expired=True), [])

    async def test_corrupt_index_entries_are_discarded_not_fatal(self) -> None:
        service = self.service()
        service.ensure_directories()
        service.index_path.write_text("{not json", encoding="utf-8")
        self.assertEqual(service.list(OWNER), [])
        self.assertEqual(service.used_bytes(), 0)
        _FileServer.body = b"y"
        record = await service.fetch(OWNER, f"{self.base}/after.bin")
        self.assertTrue(Path(record.path).is_file())

    # ---------------------------------------------------------- capabilities

    async def test_capabilities_reflect_the_host(self) -> None:
        caps = self.service().capabilities()
        self.assertEqual(caps["direct_send_limit"], DIRECT_TELEGRAM_LIMIT)
        self.assertFalse(caps["transcoding"])
        self.assertIn("ytdlp_available", caps)
        self.assertGreater(caps["free_bytes"], 0)

    def test_record_round_trips_through_dict(self) -> None:
        record = DownloadRecord(
            id="1-2",
            owner_id=OWNER,
            filename="a.bin",
            path=str(self.root / "a.bin"),
            size_bytes=10,
            mime="application/octet-stream",
            source_url="https://example.com/a.bin",
            created_at="2026-01-01T00:00:00+00:00",
            expires_at="2026-01-02T00:00:00+00:00",
            direct_send_limit=50,
        )
        self.assertEqual(DownloadRecord.from_dict(record.to_dict()), record)
