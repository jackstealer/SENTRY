"""JSON logs with a per-event `trace_id`; stdlib loggers (uvicorn, alembic) render the same way."""

import logging
import sys
import uuid
from typing import TextIO

import structlog
from structlog.contextvars import bind_contextvars, clear_contextvars, merge_contextvars
from structlog.typing import EventDict, Processor, WrappedLogger

from sentry_ai.config import get_settings


def _ensure_trace_id(_logger: WrappedLogger, _method: str, event_dict: EventDict) -> EventDict:
    # every line carries the key; null means "not inside an inbound event or job" (e.g. startup)
    event_dict.setdefault("trace_id", None)
    return event_dict


_SHARED: list[Processor] = [
    merge_contextvars,
    _ensure_trace_id,
    structlog.stdlib.add_log_level,
    structlog.stdlib.add_logger_name,
    structlog.processors.TimeStamper(fmt="iso", utc=True),
]


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """Call once at process start, before anything logs. The CLI logs to stderr."""
    structlog.configure(
        processors=[*_SHARED, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        # off: cached loggers would bypass structlog.testing.capture_logs in tests
        cache_logger_on_first_use=False,
    )
    handler = logging.StreamHandler(stream or sys.stdout)  # looked up now, not at import
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=_SHARED,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True


def new_trace_id() -> str:
    return uuid.uuid4().hex


def bind_trace(trace_id: str | None = None, **fields: object) -> str:
    """Start a fresh log context for one inbound event or scheduled job; returns the trace id."""
    trace_id = trace_id or new_trace_id()
    clear_contextvars()
    bind_contextvars(trace_id=trace_id, user_id=get_settings().user_id, **fields)
    return trace_id


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.stdlib.get_logger(name)
