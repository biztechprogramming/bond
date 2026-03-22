"""Common logging configuration for the mediator pipeline."""

from __future__ import annotations

import logging
import sys

LOG_FORMAT = "%(asctime)s | %(levelname)-5s | %(name)-20s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

NAMED_LOGGERS = [
    "mediator",
    "mediator.handler",
]


def configure_logging(
    level: str = "INFO",
    *,
    log_format: str = LOG_FORMAT,
    date_format: str = DATE_FORMAT,
) -> None:
    """Configure logging with consistent formatting.

    Sets up the root ``bond`` logger so all ``bond.*`` children
    (e.g. ``bond.agent.repomap``) inherit the handler automatically.
    Also configures explicit mediator loggers for backward compat.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))

    resolved_level = getattr(logging, level.upper(), logging.INFO)

    # Root bond logger — all bond.* children inherit this
    bond_root = logging.getLogger("bond")
    bond_root.setLevel(resolved_level)
    bond_root.handlers.clear()
    bond_root.addHandler(handler)
    bond_root.propagate = False

    # Named loggers (mediator etc.) that don't live under bond.*
    for logger_name in NAMED_LOGGERS:
        log = logging.getLogger(logger_name)
        log.setLevel(resolved_level)
        log.handlers.clear()
        log.addHandler(handler)
        log.propagate = False
