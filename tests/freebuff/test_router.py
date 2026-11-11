from __future__ import annotations

import unittest
from types import SimpleNamespace

from bridge.services.backend_router import BackendRouter


class FakeSessions:
    def __init__(self) -> None:
        self.preferences: dict[str, str] = {}

    async def get_preference(self, owner_id: str) -> str | None:
        return self.preferences.get(owner_id)

    async def set_preference(self, owner_id: str, backend: str) -> None:
        self.preferences[owner_id] = backend


class FakeBackend:
    capabilities = SimpleNamespace(sharing=False, backend_id="freebuff")

    def __init__(self, *, healthy: bool) -> None:
        self.healthy = healthy
        self.probes = 0

    async def health(self) -> dict[str, object]:
        self.probes += 1
        return {
            "healthy": self.healthy,
            "supported": self.healthy,
            "logged_in": self.healthy,
            "version": "0.0.200",
            "reason": "ok" if self.healthy else "not_installed",
            "models": ["model-a"],
        }


def _router(*, mode: str, healthy: bool) -> tuple[BackendRouter, FakeBackend, FakeSessions]:
    sessions = FakeSessions()
    backend = FakeBackend(healthy=healthy)
    router = BackendRouter(
        default_mode=mode,
        opencode=object(),
        freebuff=backend,
        sessions=sessions,
    )
    return router, backend, sessions


class BackendRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_opencode_keeps_legacy_path(self) -> None:
        router, _, _ = _router(mode="opencode", healthy=True)
        self.assertIsNone(await router.resolve("1"))

    async def test_owner_preference_overrides_default(self) -> None:
        router, backend, sessions = _router(mode="opencode", healthy=True)
        await router.set_mode("1", "freebuff")
        self.assertEqual(sessions.preferences["1"], "freebuff")
        self.assertIs(await router.resolve("1"), backend)
        self.assertIsNone(await router.resolve("2"))

    async def test_auto_uses_freebuff_only_when_healthy(self) -> None:
        healthy, _, _ = _router(mode="auto", healthy=True)
        self.assertIsNotNone(await healthy.resolve("1"))
        unhealthy, _, _ = _router(mode="auto", healthy=False)
        self.assertIsNone(await unhealthy.resolve("1"))

    async def test_status_reports_effective_backend(self) -> None:
        router, _, _ = _router(mode="freebuff", healthy=True)
        status = await router.status("1")
        self.assertEqual(status["mode"], "freebuff")
        self.assertEqual(status["effective"], "freebuff")
        self.assertTrue(status["freebuff"]["healthy"])

    async def test_invalid_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _router(mode="nope", healthy=True)


if __name__ == "__main__":
    unittest.main()
