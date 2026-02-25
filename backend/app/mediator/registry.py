"""Registry mapping Command/Query types to their Handler classes."""

from __future__ import annotations

from typing import Any

_HANDLER_MAP: dict[type, type] = {}


def handles(request_type: type) -> Any:
    """Decorator to register a handler for a Command/Query type.

    Usage:
        @handles(CreateSettingCommand)
        class CreateSettingHandler(CommandHandler[CreateSettingCommand, Setting]):
            async def handle(self, command): ...
    """

    def decorator(handler_cls: type) -> type:
        _HANDLER_MAP[request_type] = handler_cls
        return handler_cls

    return decorator


def get_handler_class(request_type: type) -> type:
    """Look up the registered handler for a request type."""
    try:
        return _HANDLER_MAP[request_type]
    except KeyError:
        raise ValueError(f"No handler registered for {request_type.__name__}")


def get_all_handlers() -> dict[type, type]:
    """Return a copy of the handler registry."""
    return dict(_HANDLER_MAP)
