"""Collections and deliveries, in memory and in an append-only journal.

The journal exists because of one failure the double has to survive: the hub pulls a collection's
status *after* a webhook has already been delivered. If a restart forgot the collection, that pull
would answer `not_found` for money the hub has already credited, and the hub's own sweeper would
start asking about a charge its provider denies — the exact contradiction Task T1.1's
`test_status_matches_last_webhook_after_restart` pins.

So every state change is appended (and fsynced) *before* the answer is sent, and the store replays
the journal at start-up. The journal is JSON lines because it must be readable by a human during a
debug session and appendable without rewriting: a crash leaves at most one torn line, which the
replay drops.

Provider references are derived from the caller's `reference` rather than minted randomly, so the
same collection has the same provider reference across restarts and across replicas, which is what
makes the initiation endpoint idempotent in practice and not just in one process's memory.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

__all__ = ["Collection", "Delivery", "Store", "provider_reference_for"]

# The statuses the provider API reports. `reversed` is a settled status: the money went back.
PENDING = "pending"
SUCCESSFUL = "successful"
FAILED = "failed"
REVERSED = "reversed"
STATUSES = (PENDING, SUCCESSFUL, FAILED, REVERSED)


def provider_reference_for(reference: str) -> str:
    """The provider's own id for a collection, derived from the caller's reference.

    Derived, not random: initiation is idempotent on `reference`, and an id that changed with
    each attempt would make the second answer look like a different charge. Ten hex characters
    of a digest is the shape a mobile-money provider's reference has and is stable everywhere.
    """
    return "MP" + sha256(reference.encode("utf-8")).hexdigest()[:10].upper()


@dataclass(frozen=True, slots=True)
class Collection:
    """One collection request and where it currently stands."""

    provider: str
    reference: str
    provider_reference: str
    amount_minor: int
    currency: str
    payer_msisdn: str
    status: str = PENDING
    updated_at: str = ""
    # Set when a reversal was delivered: the hub keys the reversal as its own provider
    # transaction (`<reference>-R`), and the double has to be able to say what that id was.
    reversal_reference: str | None = None

    @property
    def amount(self) -> dict[str, int | str]:
        return {"amount_minor": self.amount_minor, "currency": self.currency}

    def document(self) -> dict[str, Any]:
        """The `collection_status` document `GET .../collections/{ref}` answers with."""
        return {
            "provider_reference": self.provider_reference,
            "reference": self.reference,
            "status": self.status,
            "amount": self.amount,
            "payer_msisdn": self.payer_msisdn,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class Delivery:
    """One attempt to deliver one envelope, as `GET /control/deliveries` reports it."""

    event_id: str
    provider: str
    attempt: int
    sent_at: str
    hub_status: int | None
    mode: str | None = None
    note: str | None = None


class Store:
    """Collections, deliveries and the journal that survives a restart."""

    def __init__(
        self,
        journal_path: str,
        *,
        clock: Callable[[], float],
        on_delivery: Callable[[Delivery], None] | None = None,
    ) -> None:
        self._path = Path(journal_path)
        self._clock = clock
        self._lock = threading.RLock()
        self._collections: dict[tuple[str, str], Collection] = {}
        self._deliveries: list[Delivery] = []
        self._on_delivery = on_delivery
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._replay()

    # --- reads ---------------------------------------------------------------------------
    @property
    def path(self) -> Path:
        return self._path

    def now_iso(self) -> str:
        return datetime.fromtimestamp(self._clock(), UTC).isoformat().replace("+00:00", "Z")

    def get(self, provider: str, provider_reference: str) -> Collection | None:
        with self._lock:
            return self._collections.get((provider, provider_reference))

    def by_reference(self, provider: str, reference: str) -> Collection | None:
        with self._lock:
            return self._collections.get((provider, provider_reference_for(reference)))

    def collections(self, provider: str | None = None) -> list[Collection]:
        with self._lock:
            found = list(self._collections.values())
        return [item for item in found if provider is None or item.provider == provider]

    def deliveries(self) -> list[Delivery]:
        with self._lock:
            return list(self._deliveries)

    # --- writes --------------------------------------------------------------------------
    def add_collection(
        self,
        *,
        provider: str,
        reference: str,
        amount_minor: int,
        currency: str,
        payer_msisdn: str,
    ) -> Collection:
        """Record a new collection, or return the existing one for the same reference."""
        provider_reference = provider_reference_for(reference)
        with self._lock:
            existing = self._collections.get((provider, provider_reference))
            if existing is not None:
                return existing
            collection = Collection(
                provider=provider,
                reference=reference,
                provider_reference=provider_reference,
                amount_minor=amount_minor,
                currency=currency,
                payer_msisdn=payer_msisdn,
                status=PENDING,
                updated_at=self.now_iso(),
            )
            self._collections[(provider, provider_reference)] = collection
            self._append({"kind": "collection", **asdict(collection)})
            return collection

    def set_status(self, provider: str, provider_reference: str, status: str) -> Collection:
        """Move a collection to ``status``; the journal entry is durable before it returns."""
        if status not in STATUSES:
            raise ValueError(f"not a collection status: {status!r}")
        with self._lock:
            current = self._collections[(provider, provider_reference)]
            updated = replace(current, status=status, updated_at=self.now_iso())
            self._collections[(provider, provider_reference)] = updated
            self._append(
                {
                    "kind": "status",
                    "provider": provider,
                    "provider_reference": provider_reference,
                    "status": status,
                    "updated_at": updated.updated_at,
                }
            )
            return updated

    def note_reversal(self, provider: str, provider_reference: str) -> str:
        """Record the reversal transaction id and flip the status; returns the new id."""
        with self._lock:
            current = self._collections[(provider, provider_reference)]
            reversal_reference = f"{provider_reference}-R"
            updated = replace(
                current,
                status=REVERSED,
                updated_at=self.now_iso(),
                reversal_reference=reversal_reference,
            )
            self._collections[(provider, provider_reference)] = updated
            self._append(
                {
                    "kind": "reversal",
                    "provider": provider,
                    "provider_reference": provider_reference,
                    "reversal_reference": reversal_reference,
                    "updated_at": updated.updated_at,
                }
            )
            return reversal_reference

    def record_delivery(self, delivery: Delivery) -> None:
        """Remember a delivery attempt, so `/control/deliveries` can be asserted on."""
        with self._lock:
            self._deliveries.append(delivery)
            self._append({"kind": "delivery", **asdict(delivery)})
        if self._on_delivery is not None:
            self._on_delivery(delivery)

    def reset(self) -> None:
        """Forget everything a scenario left behind, on disk included."""
        with self._lock:
            self._collections.clear()
            self._deliveries.clear()
            self._path.unlink(missing_ok=True)

    # --- the journal ---------------------------------------------------------------------
    def _append(self, entry: dict[str, Any]) -> None:
        """One JSON line, flushed and fsynced: a change that is answered must be durable."""
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def _replay(self) -> None:
        """Rebuild the state from the journal; a torn final line is dropped, not fatal."""
        if not self._path.exists():
            return
        with self._path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    # A crash mid-append: the entry never happened, so neither did its effect.
                    continue
                self._apply(entry)

    def _apply(self, entry: dict[str, Any]) -> None:
        kind = entry.get("kind")
        if kind == "collection":
            collection = Collection(
                provider=entry["provider"],
                reference=entry["reference"],
                provider_reference=entry["provider_reference"],
                amount_minor=entry["amount_minor"],
                currency=entry["currency"],
                payer_msisdn=entry["payer_msisdn"],
                status=entry["status"],
                updated_at=entry["updated_at"],
                reversal_reference=entry.get("reversal_reference"),
            )
            self._collections[(collection.provider, collection.provider_reference)] = collection
        elif kind == "status":
            key = (entry["provider"], entry["provider_reference"])
            current = self._collections.get(key)
            if current is not None:
                self._collections[key] = replace(
                    current, status=entry["status"], updated_at=entry["updated_at"]
                )
        elif kind == "reversal":
            key = (entry["provider"], entry["provider_reference"])
            current = self._collections.get(key)
            if current is not None:
                self._collections[key] = replace(
                    current,
                    status=REVERSED,
                    updated_at=entry["updated_at"],
                    reversal_reference=entry["reversal_reference"],
                )
        elif kind == "delivery":
            self._deliveries.append(
                Delivery(
                    event_id=entry["event_id"],
                    provider=entry["provider"],
                    attempt=entry["attempt"],
                    sent_at=entry["sent_at"],
                    hub_status=entry["hub_status"],
                    mode=entry.get("mode"),
                    note=entry.get("note"),
                )
            )

    def iter_deliveries(self) -> Iterator[Delivery]:  # pragma: no cover - diagnostics helper
        yield from self.deliveries()
