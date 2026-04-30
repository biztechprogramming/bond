"""Git credential resolution for agent repo clones (Design Doc 113).

Given an agent_repos row + URL, returns the credential the agent should use to
authenticate. Resolution order:

1. Per-repo override: if `agent_repos.credentialId` is set, use that credential.
2. User-level fallback: pick the most-specific `git_credentials.hostPattern`
   matching the URL's host, breaking ties by `isDefault`.
3. None: caller attempts unauthenticated clone (works for public repos).
"""

from __future__ import annotations

import fnmatch
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

logger = logging.getLogger("bond.git_credentials")


@dataclass(frozen=True)
class ResolvedCredential:
    """A credential ready for use by the entrypoint."""

    id: str
    name: str
    auth_type: str  # 'https_pat' | 'ssh_key'
    secret: str  # the actual PAT or SSH private key, fetched from vault
    username: str  # for HTTPS PATs; empty for SSH


# ── URL parsing ────────────────────────────────────────────────


_SSH_URL_RE = re.compile(r"^(?:ssh://)?(?:[^@]+@)?([^:/]+)[:/]")


def parse_repo_host(url: str) -> str | None:
    """Extract the host from a git URL (HTTPS or SSH).

    Returns None if the URL is malformed.
    """
    url = url.strip()
    if not url:
        return None

    # SSH form: git@github.com:foo/bar.git
    if "://" not in url:
        m = _SSH_URL_RE.match(url)
        return m.group(1) if m else None

    parsed = urlparse(url)
    return parsed.hostname


# ── Resolver ────────────────────────────────────────────────────


def _pattern_specificity(pattern: str) -> int:
    """Higher = more specific. Wildcard patterns score lower."""
    if pattern == "*":
        return 0
    if pattern.startswith("*."):
        return 1
    if "*" in pattern:
        return 2
    return 3  # exact match


def _pattern_matches(pattern: str, host: str) -> bool:
    return fnmatch.fnmatchcase(host, pattern)


def resolve_credential(
    repo_url: str,
    repo_credential_id: str,
    all_credentials: list[dict],
) -> dict | None:
    """Pick the credential to use for cloning a repo.

    Args:
        repo_url: the clone URL.
        repo_credential_id: value of agent_repos.credential_id; empty for no override.
        all_credentials: list of git_credentials rows from STDB (snake_case keys).

    Returns:
        The matching credential dict, or None if no match.
    """
    # 1. Per-repo override
    if repo_credential_id:
        for cred in all_credentials:
            if cred["id"] == repo_credential_id:
                return cred
        # Configured override missing — log and fall through to host match
        logger.warning(
            "agent_repos.credential_id=%s not found in git_credentials; "
            "falling back to host pattern match",
            repo_credential_id,
        )

    # 2. Host pattern fallback
    host = parse_repo_host(repo_url)
    if not host:
        return None

    matches = [c for c in all_credentials if _pattern_matches(c["host_pattern"], host)]
    if not matches:
        return None

    # Sort: most-specific pattern first, then is_default, then by name for determinism
    matches.sort(
        key=lambda c: (
            -_pattern_specificity(c["host_pattern"]),
            not bool(c.get("is_default")),
            c["name"],
        )
    )
    return matches[0]


# ── Vault integration ──────────────────────────────────────────


def fetch_credential_secret(secret_ref: str) -> str | None:
    """Read a credential's secret from the vault."""
    from backend.app.core.vault import Vault

    vault = Vault()
    return vault.get(secret_ref)


def vault_key_for_credential(credential_id: str) -> str:
    """Convention for storing a credential's secret in the vault."""
    return f"git.credentials.{credential_id}"
