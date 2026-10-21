# Test Architecture

Suites are classified as:

- unit: pure domain/service behavior.
- integration: SQLite/storage/process integration.
- contract: Bridge/OpenCode and adapter contracts.
- e2e: Telegram abstraction → queue/service → OpenCode mock → output.
- load: deterministic synthetic workload harnesses.
- fault: injected dependency/process/storage failures.
- security: traversal/secrets/sandbox/hardening.
- migration: schema and upgrade/rollback compatibility.

Existing `test_v2_*` and `test_v3_*` remain discoverable for backward compatibility while new tests should declare the closest suite through naming/folder placement.
