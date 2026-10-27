"""A provider that answers from memory: the test double and the ``synthetic`` local adapter.

It is a real implementation of :class:`~hub.payments.application.ports.ProviderGateway`, not a
mock: the signature it checks is the production one, so a test that forges a delivery goes
through the same code a production delivery does. What it *does* replace is the network, and
that is what makes the exactly-once tests possible at all — fifty threads can deliver one
signed body to it without fifty sockets.

``calls`` records every initiation so a test can assert the provider was asked once, and
``next_answer`` chooses what the next initiation says (accepted, refused, unknown). The
alternatives are explicit rather than random: a flaky double would make a failure
unreproducible.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from hub.payments.application.ports import (
    CollectionAck,
    CollectionRequest,
    ProviderTxnStatus,
    VerifiedEvent,
)
from hub.payments.domain.signature import DEFAULT_TOLERANCE_SECONDS, compute_signature
from hub.payments.infrastructure.provider_http import (
    EVENT_ID_HEADER,
    SIGNATURE_HEADER,
    TIMESTAMP_HEADER,
    verify_delivery,
)

__all__ = [
    "DEFAULT_FAKE_SECRET",
    "EVENT_ID_HEADER",
    "SIGNATURE_HEADER",
    "TIMESTAMP_HEADER",
    "FakeProviderGateway",
]

# The stub's signing secret, and deliberately not a secret: it is the development default
# `HUB_PROVIDER_WEBHOOK_SECRETS` already carries for `synthetic`, it lives in the repository,
# and a deployment with a real provider configures its own.
DEFAULT_FAKE_SECRET = "dev-synthetic-webhook-secret"  # noqa: S105


@dataclass
class FakeProviderGateway:
    """A provider in memory: one secret, one recorded call list, one configurable answer."""

    secrets: Sequence[str] = (DEFAULT_FAKE_SECRET,)
    tolerance: int = DEFAULT_TOLERANCE_SECONDS
    calls: list[CollectionRequest] = field(default_factory=list)
    # What the next initiation answers. A test sets it; `accepted` is the default.
    answer: CollectionAck | None = None
    # When set, `initiate_collection` raises it instead of answering (a timeout scenario).
    failure: BaseException | None = None
    statuses: dict[str, ProviderTxnStatus] = field(default_factory=dict)

    def initiate_collection(self, req: CollectionRequest) -> CollectionAck:
        self.calls.append(req)
        if self.failure is not None:
            raise self.failure
        return self.answer or CollectionAck(accepted=True, provider_txn_id=f"txn-{req.reference}")

    def verify_webhook(
        self, headers: Mapping[str, str], body: bytes, *, now: datetime
    ) -> VerifiedEvent:
        return verify_delivery(
            [secret.encode("utf-8") for secret in self.secrets],
            headers,
            body,
            now=now,
            tolerance=self.tolerance,
        )

    def fetch_status(self, provider_txn_id: str) -> ProviderTxnStatus:
        return self.statuses.get(provider_txn_id, ProviderTxnStatus(status="not_found"))

    # --- helpers the tests and the local demo use -----------------------------------------
    def sign(
        self, body: bytes, *, timestamp: str, event_id: str, secret: str | None = None
    ) -> dict[str, str]:
        """The three headers an honest sender of ``body`` would send."""
        chosen = (secret or self.secrets[0]).encode("utf-8")
        return {
            TIMESTAMP_HEADER: timestamp,
            EVENT_ID_HEADER: event_id,
            SIGNATURE_HEADER: compute_signature(chosen, timestamp, event_id, body),
        }

    def signed(
        self, envelope: Mapping[str, Any], *, timestamp: str, secret: str | None = None
    ) -> tuple[dict[str, str], bytes]:
        """``(headers, body)`` for an envelope, signed the way the provider signs."""
        body = json.dumps(dict(envelope)).encode("utf-8")
        headers = self.sign(
            body, timestamp=timestamp, event_id=str(envelope["event_id"]), secret=secret
        )
        return headers, body
