"""FastAPI dependencies."""

from backend.app.db.session import get_db
from backend.app.mediator import get_mediator

__all__ = ["get_db", "get_mediator"]
