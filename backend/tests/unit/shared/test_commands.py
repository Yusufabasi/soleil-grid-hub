"""The three worker commands (docs/03 2.1).

Slice 00 ships them as no-op loops, but the shape they must already have is the operational
one: ``--once`` for CI and one-shot jobs, a loop otherwise, ``loop.started`` in the JSON log
before anything else happens, and a stop that returns instead of killing the process. The
loop is stopped through the command's own flag, which is what a SIGTERM handler sets in
production (a handler cannot be installed from a test thread).
"""

from __future__ import annotations

import io
import json
import logging
import threading
from contextlib import redirect_stdout
from importlib import import_module

import pytest
from django.core.management import call_command

COMMANDS = ["run_worker", "run_relay", "run_scheduler"]


@pytest.fixture(autouse=True)
def _restore_logging() -> object:
    """The commands reconfigure the root logger; put the test process back afterwards."""
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.mark.parametrize("name", COMMANDS)
def test_once_logs_loop_started_and_returns_zero(name: str) -> None:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        result = call_command(name, "--once")

    assert result is None  # handle() returned normally: the process exits 0
    lines = [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]
    started = [line for line in lines if line["event"] == "loop.started"]
    assert started, buffer.getvalue()
    assert started[0]["level"] == "info"
    assert started[0]["service"]


@pytest.mark.parametrize("name", COMMANDS)
def test_loop_runs_until_stopped(name: str) -> None:
    module = import_module(f"hub.shared.management.commands.{name}")
    command = module.Command()
    thread = threading.Thread(target=lambda: command.handle(once=False), daemon=True)

    with redirect_stdout(io.StringIO()):
        thread.start()
        assert command.started.wait(5), "the loop must announce itself"
        assert thread.is_alive(), "without --once the command keeps running"
        command.stop()
        thread.join(5)

    assert not thread.is_alive()
