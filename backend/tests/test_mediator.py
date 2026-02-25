"""Tests for the mediator pipeline."""

from __future__ import annotations

import pytest

from backend.app.mediator.base import Command, CommandHandler
from backend.app.mediator.pipeline import PipelineBehavior, run_pipeline
from backend.app.mediator.registry import get_handler_class, handles
from backend.app.mediator import Mediator


# --- Test command/handler pair ---


class PingCommand(Command):
    payload: str = "ping"


class PingResult:
    def __init__(self, value: str):
        self.value = value


@handles(PingCommand)
class PingHandler(CommandHandler[PingCommand, PingResult]):
    async def handle(self, command: PingCommand) -> PingResult:
        return PingResult(value=f"pong:{command.payload}")


# --- Tests ---


def test_handler_registration():
    cls = get_handler_class(PingCommand)
    assert cls is PingHandler


@pytest.mark.asyncio
async def test_pipeline_calls_handler():
    handler = PingHandler()
    cmd = PingCommand(payload="test")

    result = await run_pipeline(
        behaviors=[],
        request=cmd,
        handler_call=lambda: handler.handle(cmd),
    )
    assert result.value == "pong:test"


@pytest.mark.asyncio
async def test_pipeline_behaviors_wrap_handler():
    """Verify behaviors execute in order around the handler."""
    call_order: list[str] = []

    class TrackingBehavior(PipelineBehavior):
        def __init__(self, name: str):
            self.name = name

        async def handle(self, request, next_behavior, *, http_request=None, db=None):
            call_order.append(f"{self.name}:before")
            result = await next_behavior()
            call_order.append(f"{self.name}:after")
            return result

    handler = PingHandler()
    cmd = PingCommand()

    result = await run_pipeline(
        behaviors=[TrackingBehavior("outer"), TrackingBehavior("inner")],
        request=cmd,
        handler_call=lambda: handler.handle(cmd),
    )

    assert result.value == "pong:ping"
    assert call_order == ["outer:before", "inner:before", "inner:after", "outer:after"]


@pytest.mark.asyncio
async def test_mediator_send():
    mediator = Mediator(db=None, behaviors=[])
    cmd = PingCommand(payload="mediator")
    result = await mediator.send(cmd)
    assert result.value == "pong:mediator"


@pytest.mark.asyncio
async def test_mediator_with_default_behaviors():
    """Mediator with default behaviors still processes the command."""
    mediator = Mediator(db=None)
    cmd = PingCommand(payload="full")
    result = await mediator.send(cmd)
    assert result.value == "pong:full"


def test_unregistered_handler_raises():
    class UnknownCommand(Command):
        pass

    with pytest.raises(ValueError, match="No handler registered"):
        get_handler_class(UnknownCommand)
