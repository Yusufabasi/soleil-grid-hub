"""Where a one-time code leaves the process (D-21).

Slice 01 ships the *port* and this adapter, not a provider: the SMS gateway is a slice 07
integration, and the contract only requires that an unknown number sends nothing at all. Until a
gateway is configured there is nothing to call, so the adapter records what it would send and
logs the event without the code.

That log line is deliberately code-free and number-free: an OTP in a log is an OTP an operator
can use, and the number is the one piece of personal data this flow exists to avoid keeping.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

__all__ = ["LoggingNotifier", "RecordedOtp"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RecordedOtp:
    """One message the adapter accepted: the test doubles assert on these."""

    phone: str
    code: str
    locale: str


@dataclass
class LoggingNotifier:
    """A notifier that writes a structured line instead of sending an SMS."""

    sent: list[RecordedOtp] = field(default_factory=list)

    def send_otp(self, *, phone: str, code: str, locale: str) -> None:
        self.sent.append(RecordedOtp(phone=phone, code=code, locale=locale))
        logger.info(
            "otp.queued",
            extra={
                "otp_locale": locale,
                "otp_phone_suffix": phone[-2:],
                "otp_count": len(self.sent),
            },
        )
