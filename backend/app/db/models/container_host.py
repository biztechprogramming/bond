"""ContainerHost model — persistent storage for container host configuration."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.models.settings import Base


class ContainerHost(Base):
    __tablename__ = "container_hosts"

    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    port: Mapped[int] = mapped_column(Integer, nullable=False, server_default="22")
    user: Mapped[str] = mapped_column(String(255), nullable=False, server_default="bond")
    ssh_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    daemon_port: Mapped[int] = mapped_column(Integer, nullable=False, server_default="8990")
    max_agents: Mapped[int] = mapped_column(Integer, nullable=False, server_default="4")
    memory_mb: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    labels: Mapped[str] = mapped_column(Text, nullable=False, server_default="[]")
    enabled: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    status: Mapped[str] = mapped_column(String(50), nullable=False, server_default="active")
    is_local: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=func.datetime("now"))
    updated_at: Mapped[str] = mapped_column(Text, nullable=False, server_default=func.datetime("now"))
