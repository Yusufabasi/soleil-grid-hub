"""One answer to "is this TOTP code valid?", shared by login and step-up.

Two flows accept a six-digit code — the second step of a login and a step-up — and they must
agree about the window and about replay, or the weaker one becomes the way in. The code is
judged against the injected clock, never pyotp's wall clock, and the counter that matched is
remembered in Redis so the same digits cannot be spent twice inside their own 30-second step.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import pyotp

from hub.identity.contracts import AuditLog
from hub.shared.contracts import KeyValueStore
from hub.shared.errors import Unauthenticated
from hub.shared.ports import Clock

__all__ = ["TOTP_STEP_SECONDS", "TOTP_VALID_WINDOW", "verify_totp_code"]

TOTP_STEP_SECONDS = 30
# docs/05: one step either side of now is accepted, two is not.
TOTP_VALID_WINDOW = 1
TOTP_REPLAY_TTL_SECONDS = 90


def verify_totp_code(
    *,
    clock: Clock,
    store: KeyValueStore,
    users: Any,
    audit: AuditLog,
    user_id: UUID,
    code: str,
    ip: str | None,
) -> None:
    """Accept ``code`` once for this user, or raise ``Unauthenticated``.

    The window is three candidate steps, tried explicitly: ``pyotp.TOTP.verify`` compares with
    the wall clock, which a frozen test cannot move, and it does not say which counter matched —
    and the counter is what the replay guard keys on.
    """
    secret = users.totp_secret(user_id)
    now = clock.now()
    totp = pyotp.TOTP(secret)
    for offset in range(-TOTP_VALID_WINDOW, TOTP_VALID_WINDOW + 1):
        instant = now + timedelta(seconds=offset * TOTP_STEP_SECONDS)
        if not pyotp.utils.strings_equal(str(totp.at(instant)), code):
            continue
        counter = _counter_of(totp, instant)
        if not store.add_once(
            f"totp:used:{user_id}:{counter}", "1", ttl_seconds=TOTP_REPLAY_TTL_SECONDS
        ):
            audit.record(
                stream="auth",
                actor=None,
                action="auth.mfa.replay",
                entity_type="user",
                entity_id=user_id,
                site_id=None,
                details={"method": "totp"},
                outcome="denied",
                ip=ip,
            )
            raise Unauthenticated(code="invalid_code")
        return
    raise Unauthenticated(code="invalid_code")


def _counter_of(totp: pyotp.TOTP, instant: datetime) -> int:
    """The 30-second step number ``instant`` belongs to."""
    return int((instant - datetime(1970, 1, 1, tzinfo=instant.tzinfo)).total_seconds()) // (
        totp.interval
    )
