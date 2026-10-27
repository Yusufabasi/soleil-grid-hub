"""The quarantine log: a rejected delivery as a hash, a length and a header (FR-03).

The view knows what was rejected; this adapter knows the two facts a row needs and the caller
should not have to supply — its id and the instant — so the view stays short and the clock
stays the injected one. Nothing here can carry the body: the signature takes a digest, and
there is no field for the bytes.

The write is deliberately allowed to fail loudly at the call site (the view catches it): a log
that refuses an entry must not turn a correct 401 into a 500 the provider would retry.
"""

from __future__ import annotations

from hub.payments.infrastructure.repositories import ProviderEventRepository
from hub.shared.ports import Clock, IdGenerator

__all__ = ["WebhookQuarantineLog"]


class WebhookQuarantineLog:
    """``hub.webhook_quarantine`` behind the three fields a rejection knows."""

    def __init__(self, *, clock: Clock, ids: IdGenerator, events: ProviderEventRepository) -> None:
        self._clock = clock
        self._ids = ids
        self._events = events

    def add(
        self,
        *,
        provider: str,
        reason: str,
        source_ip: str | None,
        body_sha256: str,
        body_length: int,
        signature_header: str | None,
    ) -> None:
        self._events.quarantine(
            quarantine_id=self._ids.new_id(),
            provider=provider,
            reason=reason,
            source_ip=source_ip,
            body_sha256=body_sha256,
            body_length=body_length,
            signature_header=signature_header,
            received_at=self._clock.now(),
        )
