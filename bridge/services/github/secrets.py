"""GitHub Secrets and Deploy Keys management for OpenCode Bridge.

Handles encrypted secrets, repository secrets, organization secrets,
and deploy keys via the GitHub REST API.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from bridge.services.github.api import (
    GitHubAPIClient,
)

log = logging.getLogger("opencode_bridge.github.secrets")


@dataclass
class GitHubSecret:
    """Information about a GitHub repository secret."""

    name: str
    created_at: str
    updated_at: str
    visibility: str  # all, private, selected


@dataclass
class GitHubDeployKey:
    """Information about a GitHub deploy key."""

    id: int
    key: str
    title: str
    verified: bool
    created_at: str
    read_only: bool
    url: str


class GitHubSecretManager:
    """Manages GitHub repository secrets and deploy keys for OpenCode Bridge.

    Provides encrypted secret management using GitHub's public key
    encryption (LibSodium) for secure secret storage.
    """

    def __init__(self, client: GitHubAPIClient) -> None:
        self.client = client

    async def get_public_key(self, owner: str, repo: str) -> tuple[str, str]:
        """Get the public key for encrypting repository secrets.

        Returns:
            Tuple of (key_id, public_key_base64).
        """
        data = await self.client.get(f"/repos/{owner}/{repo}/actions/secrets/public-key")
        return data.get("key_id", ""), data.get("key", "")

    async def encrypt_secret(self, public_key_b64: str, secret_value: str) -> str:
        """Encrypt a secret value using GitHub's public key.

        Args:
            public_key_b64: Base64-encoded public key from GitHub.
            secret_value: The secret value to encrypt.

        Returns:
            Base64-encoded encrypted secret value.
        """
        public_key_bytes = base64.b64decode(public_key_b64)
        public_key = serialization.load_pem_public_key(public_key_bytes)

        if not isinstance(public_key, RSAPublicKey):
            raise ValueError("المفتاح العام ليس من نوع RSA")

        encrypted = public_key.encrypt(
            secret_value.encode("utf-8"),
            padding.PKCS1v15(),
        )
        return base64.b64encode(encrypted).decode("ascii")

    async def create_or_update_secret(
        self,
        owner: str,
        repo: str,
        secret_name: str,
        secret_value: str,
    ) -> bool:
        """Create or update an encrypted repository secret.

        Args:
            owner: Repository owner.
            repo: Repository name.
            secret_name: Name of the secret.
            secret_value: Secret value (will be encrypted automatically).

        Returns:
            True if successful.
        """
        key_id, public_key_b64 = await self.get_public_key(owner, repo)
        encrypted_value = self.encrypt_secret(public_key_b64, secret_value)

        await self.client.put(
            f"/repos/{owner}/{repo}/actions/secrets/{secret_name}",
            json_body={
                "encrypted_value": encrypted_value,
                "key_id": key_id,
            },
        )
        return True

    async def delete_secret(self, owner: str, repo: str, secret_name: str) -> bool:
        """Delete a repository secret."""
        await self.client.delete(f"/repos/{owner}/{repo}/actions/secrets/{secret_name}")
        return True

    async def list_secrets(self, owner: str, repo: str) -> list[GitHubSecret]:
        """List repository secrets (names only, values are never returned)."""
        data = await self.client.get(f"/repos/{owner}/{repo}/actions/secrets")
        secrets = data.get("secrets", []) if isinstance(data, dict) else []
        return [
            GitHubSecret(
                name=s.get("name", ""),
                created_at=s.get("created_at", ""),
                updated_at=s.get("updated_at", ""),
                visibility=s.get("visibility", "all"),
            )
            for s in secrets
        ]

    async def get_deploy_keys(self, owner: str, repo: str) -> list[GitHubDeployKey]:
        """List deploy keys for a repository."""
        data = await self.client.get(f"/repos/{owner}/{repo}/keys")
        keys = data if isinstance(data, list) else []
        return [
            GitHubDeployKey(
                id=k.get("id", 0),
                key=k.get("key", ""),
                title=k.get("title", ""),
                verified=k.get("verified", False),
                created_at=k.get("created_at", ""),
                read_only=k.get("read_only", False),
                url=k.get("url", ""),
            )
            for k in keys
        ]

    async def create_deploy_key(
        self,
        owner: str,
        repo: str,
        title: str,
        public_key: str,
        read_only: bool = True,
    ) -> GitHubDeployKey:
        """Create a new deploy key for a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            title: Descriptive title for the key.
            public_key: SSH public key string.
            read_only: Whether the key has read-only access.

        Returns:
            GitHubDeployKey with details of the created key.
        """
        data = await self.client.post(
            f"/repos/{owner}/{repo}/keys",
            json_body={
                "title": title,
                "key": public_key,
                "read_only": read_only,
            },
        )
        return self._parse_deploy_key(data)

    async def delete_deploy_key(self, owner: str, repo: str, key_id: int) -> bool:
        """Delete a deploy key from a repository."""
        await self.client.delete(f"/repos/{owner}/{repo}/keys/{key_id}")
        return True

    async def generate_ssh_key_pair(self) -> tuple[str, str]:
        """Generate a new SSH key pair for deploy key usage.

        Returns:
            Tuple of (private_key_pem, public_key_ssh_format).
        """
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )

        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")

        public_bytes = private_key.public_key().public_bytes(
            encoding=serialization.Encoding.OpenSSH,
            format=serialization.PublicFormat.OpenSSH,
        ).decode("ascii")

        return private_pem, public_bytes

    def _parse_deploy_key(self, data: dict[str, Any]) -> GitHubDeployKey:
        """Convert raw API response to GitHubDeployKey dataclass."""
        return GitHubDeployKey(
            id=data.get("id", 0),
            key=data.get("key", ""),
            title=data.get("title", ""),
            verified=data.get("verified", False),
            created_at=data.get("created_at", ""),
            read_only=data.get("read_only", False),
            url=data.get("url", ""),
        )
