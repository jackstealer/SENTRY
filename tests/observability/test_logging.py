import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest
import structlog
from structlog.contextvars import clear_contextvars

from sentry_ai.config import get_settings
from sentry_ai.observability.logging import bind_trace, configure_logging, get_logger, new_trace_id


@pytest.fixture(autouse=True)
def restore_logging() -> Iterator[None]:
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers, root.level = handlers, level
    structlog.reset_defaults()
    clear_contextvars()


def lines(capsys: pytest.CaptureFixture[str]) -> list[dict[str, Any]]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


def test_structlog_lines_are_json_with_trace_and_user(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    trace_id = bind_trace("abc123")
    get_logger("sentry_ai.test").info("tool_called", tool="create_task", status="ok")

    [line] = lines(capsys)
    assert trace_id == "abc123"
    assert line["event"] == "tool_called"
    assert line["trace_id"] == "abc123"
    assert line["user_id"] == get_settings().user_id
    assert line["level"] == "info"
    assert line["tool"] == "create_task"
    assert line["timestamp"].endswith("Z")


def test_stdlib_loggers_render_as_json_too(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    logging.getLogger("uvicorn.error").warning("server hiccup")

    [line] = lines(capsys)
    assert line["event"] == "server hiccup"
    assert line["level"] == "warning"
    assert line["logger"] == "uvicorn.error"
    assert "trace_id" in line and line["trace_id"] is None


def test_bind_trace_starts_a_clean_context(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    bind_trace("first", trigger="morning_brief")
    bind_trace("second")
    get_logger().info("reminder_fired")

    [line] = lines(capsys)
    assert line["trace_id"] == "second"
    assert "trigger" not in line


def test_level_filtering_and_exceptions(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("WARNING")
    log = get_logger()
    log.info("dropped")
    try:
        raise RuntimeError("kaboom")
    except RuntimeError:
        log.exception("job_failed")

    [line] = lines(capsys)
    assert line["event"] == "job_failed"
    assert "RuntimeError: kaboom" in line["exception"]


def test_new_trace_ids_are_unique_hex() -> None:
    ids = {new_trace_id() for _ in range(100)}
    assert len(ids) == 100
    assert all(len(i) == 32 and int(i, 16) >= 0 for i in ids)
