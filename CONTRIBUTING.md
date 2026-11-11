# Contributing to OpenCode Bridge

Thanks for considering a contribution.

## Before you start
- Search existing issues and pull requests.
- Discuss substantial behavior or architecture changes in an issue first.
- Report vulnerabilities privately through [SECURITY.md](SECURITY.md).
- Never commit credentials, tokens, signing keys, private logs, or personal data.

## Workflow
1. Create a focused branch.
2. Keep the change scoped to one coherent problem.
3. Follow the existing architecture, formatting, and naming.
4. Update tests and documentation when behavior changes.
5. Run the checks documented by the repository and CI.
6. Open a pull request explaining the problem, implementation, verification, and compatibility impact.

Avoid unrelated refactors and generated-file churn. Keep operational and configuration changes reviewable and reversible where practical.

## Verification

On Linux/CI, run the full suite and the strict quality gate:

```bash
python -m unittest discover -s tests -p "test_*.py" -v
bash scripts/quality.sh
```

On a Windows checkout, use `.venv/Scripts/python` because system Python lacks the runtime packages. The PTY suite is Unix-only and is skipped locally; Linux GitHub Actions is the release gate.

## Documentation

Update `CHANGELOG.md`, `docs/PROJECT_STATUS.md` and `docs/SESSION_LOG.md` for material changes. Keep `docs/ARCHITECTURE.md` and `docs/DECISIONS.md` aligned with the code, and remove obsolete documentation instead of leaving contradictory pages. Start at [docs/README.md](docs/README.md); agent-specific rules are in [AGENTS.md](AGENTS.md).
