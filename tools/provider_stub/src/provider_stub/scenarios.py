"""The faults a test asks for by name, and the rules for which collection they apply to.

A scenario is a *match* plus a *mode* plus the mode's parameters. Keeping it a frozen value with
a `match` predicate means the same object can be listed, deleted by id and matched against a
collection without the caller knowing how matching works, and it means the control API's job is
only to validate and store it.

Two decisions worth stating, because they are the ones a reader will question:

* **A mode is a closed vocabulary.** `MODES` is the list the control API accepts, so a typo in a
  test is a 422 rather than a scenario that silently never fires — the failure a test double is
  most likely to hide.
* **The last matching scenario wins.** A test that adds `success` for a provider and then
  `wrong_amount` for one payer expects the specific one to apply; first-match would make the
  order of two curl commands decide the outcome.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

__all__ = ["MODES", "Scenario", "ScenarioError", "validate_mode"]

# Every mode the control API accepts, with the parameter names each one reads. A parameter that
# is not listed for its mode is refused, not ignored: a `duplicate` that silently ignored `count`
# would deliver once and the test would blame the hub.
MODES: dict[str, tuple[str, ...]] = {
    "success": (),
    "failure": ("failure_code",),
    "delay": ("seconds",),
    "duplicate": ("count", "spread_ms"),
    "reorder": ("with",),
    "late": ("seconds",),
    "bad_signature": (),
    "stale_signature": ("age_seconds",),
    "wrong_secret": ("secret_provider",),
    "wrong_amount": ("amount_minor",),
    "wrong_currency": ("currency",),
    "wrong_payer": ("payer_msisdn",),
    "drop": (),
    "slow_initiate": ("seconds",),
    "error_initiate": ("status",),
    "reverse_after": ("seconds",),
}

MATCH_FIELDS = ("provider", "payer_msisdn", "reference", "amount_minor")


class ScenarioError(ValueError):
    """A mode or a parameter the vocabulary does not know."""


def validate_mode(mode: str, params: dict[str, Any]) -> None:
    """Refuse an unknown mode, or a parameter outside its mode's list, or a missing one."""
    if mode not in MODES:
        raise ScenarioError(f"unknown scenario mode: {mode!r}")
    allowed = MODES[mode]
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        raise ScenarioError(f"mode {mode!r} has no parameter(s) {unknown}; it reads {list(allowed)}")
    if mode == "duplicate":
        count = int(params.get("count", 1))
        # The plan caps a duplicate burst at twenty: a double that can send a thousand copies
        # would let a load test look like an exactly-once test.
        if not 1 <= count <= 20:
            raise ScenarioError(f"duplicate count must be 1..20, got {count}")


@dataclass(frozen=True, slots=True)
class Scenario:
    """One programmed behaviour and the collections it applies to."""

    scenario_id: str
    mode: str
    params: dict[str, Any] = field(default_factory=dict)
    match: dict[str, Any] = field(default_factory=dict)

    def applies_to(
        self,
        *,
        provider: str,
        payer_msisdn: str | None = None,
        reference: str | None = None,
        amount_minor: int | None = None,
    ) -> bool:
        """Whether this scenario matches one collection.

        A missing `payer_msisdn` (an initiation that names none) cannot match a scenario that
        filters on a payer: `None` means "this collection does not say", not "any value".
        """
        facts: dict[str, Any] = {
            "provider": provider,
            "payer_msisdn": payer_msisdn,
            "reference": reference,
            "amount_minor": amount_minor,
        }
        for key, wanted in self.match.items():
            if key not in MATCH_FIELDS:
                raise ScenarioError(f"unknown match field: {key!r}")
            if facts.get(key) != wanted:
                return False
        return True

    def document(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "mode": self.mode,
            "params": self.params,
            "match": self.match,
        }


def select(scenarios: list[Scenario], facts: dict[str, Any]) -> Scenario | None:
    """The scenario that applies to a collection: the last one added, or ``None``."""
    chosen: Scenario | None = None
    for scenario in scenarios:
        if scenario.applies_to(**facts):
            chosen = scenario
    return chosen
