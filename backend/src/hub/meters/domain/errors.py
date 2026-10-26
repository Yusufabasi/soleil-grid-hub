"""Meter failures and their stable codes (docs/05 section 5, api/openapi.yaml).

Deliberately separate from ``customers.domain.errors``: a module may not import another
module's domain (docs/03 section 0), and the two ``version_conflict`` classes happen to share
a code without sharing a hierarchy. The code is the contract; the class is not.
"""

from __future__ import annotations

from hub.shared.errors import Conflict, Duplicate, NotFound, ValidationFailed

__all__ = [
    "InvalidMeterState",
    "MeterCreditOutstanding",
    "MeterNotUsable",
    "MeterSerialConflict",
    "MeterUnknown",
    "SiteMismatch",
    "VersionConflict",
]


class MeterSerialConflict(Duplicate):
    """The serial number is already registered: 409 ``serial_conflict`` (TC-FR-01-2)."""

    code = "serial_conflict"
    message_key = "serial_conflict"


class VersionConflict(Conflict):
    """A write lost the version race inside the repository: 409 ``version_conflict``.

    ``If-Match`` gives 412 before the statement runs; this is the layer under it, for two
    writers that both passed the check and one reached the row second.
    """

    code = "version_conflict"
    message_key = "version_conflict"


class MeterUnknown(NotFound):
    """A meter id that does not exist, or one outside the caller's sites: 404."""

    code = "not_found"
    message_key = "not_found"


class MeterNotUsable(Conflict):
    """A meter a credit path may not use (FR-01, TC-FR-01-3).

    The reason is the instance's code — ``meter_decommissioned`` or ``meter_not_owned`` —
    because a top-up refusal has to say which of the two it is.
    """

    code = "meter_not_usable"
    message_key = "meter_not_usable"


class MeterCreditOutstanding(Conflict):
    """A decommission refused because credit is still on the meter: 409 ``meter_has_credit``."""

    code = "meter_has_credit"
    message_key = "meter_has_credit"


class InvalidMeterState(Conflict):
    """A lifecycle change the schema does not allow: 409 ``invalid_state_transition``."""

    code = "invalid_state_transition"
    message_key = "invalid_state_transition"


class SiteMismatch(ValidationFailed):
    """A meter assigned to a customer of another site: 422 ``site_mismatch``."""

    code = "site_mismatch"
    message_key = "site_mismatch"
