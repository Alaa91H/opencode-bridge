"""Lightweight GitHub Actions status monitor for development workspaces.

This module deliberately performs no builds and installs no dependencies. It
uses GitHub's REST API to identify workflow runs and failed steps so the
Telegram/OpenCode development agent can treat remote CI as the source of truth.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any

import httpx

GITHUB_API = "https://api.github.com"
TERMINAL_CONCLUSIONS = {
    "success",
    "failure",
    "cancelled",
    "timed_out",
    "action_required",
    "neutral",
    "skipped",
    "stale",
    "startup_failure",
}


class GitHubCIError(RuntimeError):
    pass


@dataclass(frozen=True)
class CIStatus:
    state: str
    repository: str
    run_id: int | None = None
    workflow: str | None = None
    head_sha: str | None = None
    branch: str | None = None
    status: str | None = None
    conclusion: str | None = None
    html_url: str | None = None
    failed_steps: tuple[str, ...] = ()


def _validate_repo_slug(value: str) -> str:
    slug = value.strip().removesuffix(".git").strip("/")
    parts = slug.split("/")
    if len(parts) != 2 or not all(parts):
        raise GitHubCIError("repository must use owner/repo format")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")
    if any(any(char not in allowed for char in part) for part in parts):
        raise GitHubCIError("repository contains unsupported characters")
    return slug


def summarize_run(repository: str, run: dict[str, Any] | None) -> CIStatus:
    slug = _validate_repo_slug(repository)
    if not run:
        return CIStatus(state="not_found", repository=slug)
    status = str(run.get("status") or "unknown")
    conclusion_value = run.get("conclusion")
    conclusion = str(conclusion_value) if conclusion_value else None
    if status != "completed":
        state = "pending"
    elif conclusion == "success":
        state = "success"
    elif conclusion in TERMINAL_CONCLUSIONS:
        state = "failure"
    else:
        state = "unknown"
    run_id = run.get("id")
    return CIStatus(
        state=state,
        repository=slug,
        run_id=int(run_id) if isinstance(run_id, int) else None,
        workflow=str(run.get("name")) if run.get("name") else None,
        head_sha=str(run.get("head_sha")) if run.get("head_sha") else None,
        branch=str(run.get("head_branch")) if run.get("head_branch") else None,
        status=status,
        conclusion=conclusion,
        html_url=str(run.get("html_url")) if run.get("html_url") else None,
    )


def failed_steps_from_jobs(payload: dict[str, Any]) -> tuple[str, ...]:
    failures: list[str] = []
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        return ()
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_name = str(job.get("name") or "job")
        steps = job.get("steps")
        if isinstance(steps, list):
            failed = [
                str(step.get("name") or "step")
                for step in steps
                if isinstance(step, dict) and step.get("conclusion") == "failure"
            ]
            failures.extend(f"{job_name}: {step}" for step in failed)
        elif job.get("conclusion") == "failure":
            failures.append(job_name)
    return tuple(dict.fromkeys(failures))


class GitHubCIClient:
    def __init__(self, token: str | None = None, timeout: float = 20.0) -> None:
        resolved_token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "opencode-bridge-ci-monitor",
        }
        if resolved_token.strip():
            headers["Authorization"] = f"Bearer {resolved_token.strip()}"
        self._client = httpx.AsyncClient(base_url=GITHUB_API, headers=headers, timeout=timeout, follow_redirects=False)

    async def close(self) -> None:
        await self._client.aclose()

    async def latest_run(
        self,
        repository: str,
        *,
        branch: str | None = None,
        head_sha: str | None = None,
        workflow: str | None = None,
    ) -> CIStatus:
        slug = _validate_repo_slug(repository)
        params: dict[str, str | int] = {"per_page": 30}
        if branch:
            params["branch"] = branch
        if head_sha:
            params["head_sha"] = head_sha
        response = await self._client.get(f"/repos/{slug}/actions/runs", params=params)
        if response.status_code == 403 and "rate limit" in response.text.casefold():
            raise GitHubCIError("GitHub API rate limit reached; configure GITHUB_TOKEN for authenticated monitoring")
        if response.status_code == 404:
            raise GitHubCIError("repository or Actions endpoint was not found")
        response.raise_for_status()
        payload = response.json()
        runs = payload.get("workflow_runs", []) if isinstance(payload, dict) else []
        if workflow:
            wanted = workflow.casefold()
            runs = [run for run in runs if isinstance(run, dict) and str(run.get("name", "")).casefold() == wanted]
        run = runs[0] if runs else None
        status = summarize_run(slug, run)
        if status.run_id and status.state == "failure":
            failed_steps = await self.failed_steps(slug, status.run_id)
            status = CIStatus(**{**asdict(status), "failed_steps": failed_steps})
        return status

    async def failed_steps(self, repository: str, run_id: int) -> tuple[str, ...]:
        slug = _validate_repo_slug(repository)
        response = await self._client.get(f"/repos/{slug}/actions/runs/{int(run_id)}/jobs", params={"per_page": 100})
        response.raise_for_status()
        payload = response.json()
        return failed_steps_from_jobs(payload if isinstance(payload, dict) else {})

    async def wait_for_run(
        self,
        repository: str,
        *,
        head_sha: str,
        workflow: str | None = None,
        timeout: float = 900.0,
        poll_seconds: float = 8.0,
    ) -> CIStatus:
        deadline = time.monotonic() + max(1.0, timeout)
        interval = max(2.0, poll_seconds)
        last = CIStatus(state="not_found", repository=_validate_repo_slug(repository), head_sha=head_sha)
        while time.monotonic() < deadline:
            last = await self.latest_run(repository, head_sha=head_sha, workflow=workflow)
            if last.state in {"success", "failure"}:
                return last
            await asyncio.sleep(interval)
        return CIStatus(**{**asdict(last), "state": "timeout"})


async def _async_main(args: argparse.Namespace) -> int:
    client = GitHubCIClient()
    try:
        if args.command == "wait":
            status = await client.wait_for_run(
                args.repository,
                head_sha=args.sha,
                workflow=args.workflow,
                timeout=args.timeout,
                poll_seconds=args.poll,
            )
        else:
            status = await client.latest_run(
                args.repository,
                branch=args.branch,
                head_sha=args.sha,
                workflow=args.workflow,
            )
        print(json.dumps(asdict(status), ensure_ascii=False, separators=(",", ":")))
        return 0 if status.state in {"success", "pending", "not_found"} else 1
    except (GitHubCIError, httpx.HTTPError) as exc:
        print(json.dumps({"state": "error", "error": str(exc)}, ensure_ascii=False, separators=(",", ":")))
        return 2
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect GitHub Actions without building on the control server.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status_parser = subparsers.add_parser("status")
    status_parser.add_argument("repository")
    status_parser.add_argument("--branch")
    status_parser.add_argument("--sha")
    status_parser.add_argument("--workflow")

    wait_parser = subparsers.add_parser("wait")
    wait_parser.add_argument("repository")
    wait_parser.add_argument("--sha", required=True)
    wait_parser.add_argument("--workflow")
    wait_parser.add_argument("--timeout", type=float, default=900.0)
    wait_parser.add_argument("--poll", type=float, default=8.0)

    return asyncio.run(_async_main(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
