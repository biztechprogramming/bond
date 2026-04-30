"""Git credentials API (Design Doc 113).

User-level git auth used to clone repos into agent containers. Secrets are
stored in the vault; only `secretRef` lives in SpacetimeDB.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from ulid import ULID

from backend.app.core.spacetimedb import get_stdb
from backend.app.core.vault import Vault
from backend.app.sandbox.git_credentials import vault_key_for_credential

logger = logging.getLogger("bond.api.git_credentials")

router = APIRouter(prefix="/git-credentials", tags=["git-credentials"])


# ── Pydantic models ──────────────────────────────────────────


_VALID_AUTH_TYPES = {"https_pat", "ssh_key"}


class CredentialCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    auth_type: str
    host_pattern: str = Field(..., min_length=1)
    username: str = ""
    is_default: bool = False
    secret: str = Field(..., min_length=1)


class CredentialUpdate(BaseModel):
    name: str | None = None
    auth_type: str | None = None
    host_pattern: str | None = None
    username: str | None = None
    is_default: bool | None = None
    secret: str | None = None  # if set, replaces the vault entry


# ── Helpers ───────────────────────────────────────────────────


def _escape_sql(value):
    if value is None:
        return ""
    return str(value).replace("'", "''")


def _row_to_dict(row: dict) -> dict:
    """Format a git_credentials row for API response. Never returns the secret."""
    secret = Vault().get(row["secretRef"]) or ""
    return {
        "id": row["id"],
        "name": row["name"],
        "auth_type": row["authType"],
        "host_pattern": row["hostPattern"],
        "username": row["username"] or "",
        "is_default": bool(row["isDefault"]),
        "created_at": int(row["createdAt"]),
        "updated_at": int(row.get("updatedAt") or row["createdAt"]),
        # Reveal only the last 4 chars so the UI can confirm "yes, this is the right one"
        "secret_hint": secret[-4:] if len(secret) >= 4 else "",
        "secret_set": bool(secret),
    }


# ── Endpoints ─────────────────────────────────────────────────


@router.get("")
async def list_credentials():
    stdb = get_stdb()
    rows = await stdb.query(
        "SELECT id, name, authType, secretRef, hostPattern, username, isDefault, createdAt "
        "FROM git_credentials"
    )
    rows.sort(key=lambda r: (not bool(r["isDefault"]), r["name"].lower()))
    return [_row_to_dict(r) for r in rows]


@router.post("")
async def create_credential(body: CredentialCreate):
    if body.auth_type not in _VALID_AUTH_TYPES:
        raise HTTPException(400, f"auth_type must be one of {sorted(_VALID_AUTH_TYPES)}")

    stdb = get_stdb()
    cred_id = str(ULID())
    secret_ref = vault_key_for_credential(cred_id)
    created_at = int(time.time() * 1000)

    # Store secret in vault first; if the STDB insert fails, we have a dangling
    # vault entry (acceptable — `delete_credential` would orphan-clean later).
    Vault().set(secret_ref, body.secret)

    # If this is being set as default, clear any other defaults.
    if body.is_default:
        await stdb.query("UPDATE git_credentials SET isDefault = false WHERE isDefault = true")

    await stdb.query(f"""
        INSERT INTO git_credentials (
            id, name, authType, secretRef, hostPattern, username, isDefault, createdAt, updatedAt
        ) VALUES (
            '{cred_id}',
            '{_escape_sql(body.name)}',
            '{body.auth_type}',
            '{secret_ref}',
            '{_escape_sql(body.host_pattern)}',
            '{_escape_sql(body.username)}',
            {str(body.is_default).lower()},
            {created_at},
            {created_at}
        )
    """)

    rows = await stdb.query(f"SELECT * FROM git_credentials WHERE id = '{cred_id}'")
    return _row_to_dict(rows[0])


@router.put("/{credential_id}")
async def update_credential(credential_id: str, body: CredentialUpdate):
    stdb = get_stdb()
    rows = await stdb.query(f"SELECT * FROM git_credentials WHERE id = '{credential_id}'")
    if not rows:
        raise HTTPException(404, "Credential not found")
    existing = rows[0]

    if body.auth_type is not None and body.auth_type not in _VALID_AUTH_TYPES:
        raise HTTPException(400, f"auth_type must be one of {sorted(_VALID_AUTH_TYPES)}")

    # Update vault secret if a new one was provided
    if body.secret is not None:
        Vault().set(existing["secretRef"], body.secret)

    updates = []
    if body.name is not None:
        updates.append(f"name = '{_escape_sql(body.name)}'")
    if body.auth_type is not None:
        updates.append(f"authType = '{body.auth_type}'")
    if body.host_pattern is not None:
        updates.append(f"hostPattern = '{_escape_sql(body.host_pattern)}'")
    if body.username is not None:
        updates.append(f"username = '{_escape_sql(body.username)}'")
    if body.is_default is not None:
        if body.is_default:
            await stdb.query(
                f"UPDATE git_credentials SET isDefault = false WHERE isDefault = true AND id != '{credential_id}'"
            )
        updates.append(f"isDefault = {str(body.is_default).lower()}")

    # Bump updatedAt whenever any field (including the secret) changes
    if updates or body.secret is not None:
        updates.append(f"updatedAt = {int(time.time() * 1000)}")
        await stdb.query(
            f"UPDATE git_credentials SET {', '.join(updates)} WHERE id = '{credential_id}'"
        )

    rows = await stdb.query(f"SELECT * FROM git_credentials WHERE id = '{credential_id}'")
    return _row_to_dict(rows[0])


@router.delete("/{credential_id}")
async def delete_credential(credential_id: str):
    stdb = get_stdb()
    rows = await stdb.query(
        f"SELECT secretRef FROM git_credentials WHERE id = '{credential_id}'"
    )
    if not rows:
        raise HTTPException(404, "Credential not found")

    # Refuse to delete if any agent_repos row references this credential
    refs = await stdb.query(
        f"SELECT id FROM agent_repos WHERE credentialId = '{credential_id}'"
    )
    if refs:
        raise HTTPException(
            409,
            f"Credential is referenced by {len(refs)} repo(s); detach them first or change their credential.",
        )

    secret_ref = rows[0]["secretRef"]
    await stdb.query(f"DELETE FROM git_credentials WHERE id = '{credential_id}'")
    Vault().delete(secret_ref)
    return {"success": True}
