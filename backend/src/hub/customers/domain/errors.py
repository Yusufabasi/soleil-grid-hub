"""Registry failures and their stable codes (docs/05 section 5, api/openapi.yaml).

Each class carries the code a client branches on, the ``message_key`` the problem handler
localises (Task 02.7 fills the catalogues) and the status the contract promises. The kernel
classes are reused where the meaning is already exact — a duplicate phone is a
``duplicate_resource`` 409, not a new code — and a subclass only exists where a caller has to
tell two situations apart that share a status.
"""

from __future__ import annotations

from hub.shared.errors import Conflict, Duplicate, InvalidRequest, NotFound, ValidationFailed

__all__ = [
    "CustomerHasBalance",
    "DuplicatePhone",
    "InvalidPhoneNumber",
    "InvalidStateTransition",
    "InvalidTariff",
    "PlanLocked",
    "RegistryNotFound",
    "TariffChangeTooLarge",
    "VersionConflict",
]


class DuplicatePhone(Duplicate):
    """The number already belongs to a customer: 409 ``duplicate_resource`` (D-11)."""

    message_key = "duplicate_phone"


class InvalidPhoneNumber(ValidationFailed):
    """A number that does not normalise to E.164: 422 ``invalid_phone_number``."""

    code = "invalid_phone_number"
    message_key = "invalid_phone_number"


class RegistryNotFound(NotFound):
    """A row that does not exist *or* is outside the caller's sites: always 404 (D-14)."""

    message_key = "not_found"


class VersionConflict(Conflict):
    """A write lost the version race inside the repository: 409 ``version_conflict``.

    The HTTP-level stale version is 412 ``precondition_failed`` (the ``If-Match`` is
    checked before the write); this is the second layer, for two writers that both passed
    that check and one of them reached the row second.
    """

    code = "version_conflict"
    message_key = "version_conflict"


class CustomerHasBalance(Conflict):
    """A close refused because the wallet is not empty: 409 ``customer_has_balance``."""

    code = "customer_has_balance"
    message_key = "customer_has_balance"


class InvalidStateTransition(Conflict):
    """A lifecycle change the schema does not allow: 409 ``invalid_state_transition``."""

    code = "invalid_state_transition"
    message_key = "invalid_state_transition"


class PlanLocked(ValidationFailed):
    """An edit the plan's own status forbids (limits of an active plan): 422 ``plan_locked``."""

    code = "plan_locked"
    message_key = "plan_locked"


class InvalidTariff(InvalidRequest):
    """A tariff proposal the rules refuse before it reaches the database: 422."""

    code = "invalid_tariff"
    message_key = "invalid_tariff"


class TariffChangeTooLarge(ValidationFailed):
    """The revision moves the price more than the cap allows: 422 ``tariff_change_too_large``."""

    code = "tariff_change_too_large"
    message_key = "tariff_change_too_large"
