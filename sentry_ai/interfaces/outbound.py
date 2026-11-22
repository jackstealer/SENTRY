"""Where proactive messages go. Phase 4 logs them (LogChannel); Phase 5 adds Telegram."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from sentry_ai.observability.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Button:
    label: str
    action: str  # what the reply means, e.g. "reminder:12:done" (Telegram callback data later)


def reminder_buttons(reminder_id: int) -> list[Button]:
    """PERSONA section 4: [Done] [Snooze 15] [Skip]."""
    return [
        Button("Done", f"reminder:{reminder_id}:done"),
        Button("Snooze 15", f"reminder:{reminder_id}:snooze15"),
        Button("Skip", f"reminder:{reminder_id}:skip"),
    ]


@runtime_checkable
class OutboundChannel(Protocol):
    async def send(self, text: str, buttons: Sequence[Button] | None = None) -> int | None:
        """Deliver a message; returns the channel's message id when it has one."""
        ...


class LogChannel:
    """Phase 4 output: each message is one `outbound_message` log line."""

    async def send(self, text: str, buttons: Sequence[Button] | None = None) -> int | None:
        log.info(
            "outbound_message",
            channel="log",
            text=text,
            buttons=[f"{b.label}={b.action}" for b in buttons or []],
        )
        return None
