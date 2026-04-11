"""Pydantic models for Faucet database integration (Design Doc 107)."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, SecretStr, model_validator


class DatabaseDriver(str, Enum):
    POSTGRES = "postgres"
    MYSQL = "mysql"
    MARIADB = "mariadb"
    MSSQL = "mssql"
    ORACLE = "oracle"
    SNOWFLAKE = "snowflake"
    SQLITE = "sqlite"


class AccessTier(str, Enum):
    READ_ONLY = "read_only"
    FULL_CONTROL = "full_control"


class DatabaseConnectionCreate(BaseModel):
    name: str = Field(..., pattern=r"^[a-z][a-z0-9_]{1,62}$")
    driver: DatabaseDriver
    dsn: SecretStr
    description: str | None = None


class DatabaseConnectionResponse(BaseModel):
    id: str
    name: str
    driver: DatabaseDriver
    description: str | None
    status: str
    agent_count: int
    created_at: datetime
    updated_at: datetime


class DatabaseConnectionUpdate(BaseModel):
    description: str | None = None
    dsn: SecretStr | None = None


class AgentDatabaseAssign(BaseModel):
    database_id: str | None = None
    connection: DatabaseConnectionCreate | None = None
    access_tier: AccessTier

    @model_validator(mode="after")
    def require_one(self):
        if not self.database_id and not self.connection:
            raise ValueError("Either database_id or connection must be provided")
        return self


class AgentDatabaseResponse(BaseModel):
    id: str
    database_id: str
    database_name: str
    driver: DatabaseDriver
    access_tier: AccessTier
    status: str
    assigned_at: datetime
