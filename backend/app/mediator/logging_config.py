"""Common logging configuration for the mediator pipeline."""

from __future__ import annotations

import logging
import sys

LOG_FORMAT = "%(asctime)s | %(levelname)-5s | %(name)-20s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

MEDIATOR_LOGGERS = [
    "mediator",
    "mediator.handler",
]


def configure_logging(
    level: str = "INFO",
    *,
    log_format: str = LOG_FORMAT,
    date_format: str = DATE_FORMAT,
) -> None:
    """Configure mediator logging with consistent formatting."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))

    for logger_name in MEDIATOR_LOGGERS:
        log = logging.getLogger(logger_name)
        log.setLevel(getattr(logging, level.upper(), logging.INFO))
        log.handlers.clear()
        log.addHandler(handler)
        log.propagate = False
