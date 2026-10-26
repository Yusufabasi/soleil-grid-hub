"""The one permission class, generated from the contract files (D-14, docs/03 7.7 item 4).

`api/permissions.yaml` says which roles may call each operation; `api/openapi.yaml` says which
operations exist and carries the per-operation extensions (`x-step-up`, `x-requires-attribute`,
`x-approval`, `x-site-scoped`). Both are read once into an `operationId` to rule map, and the
class compares that rule with the principal the authentication class put on the request.

Deny by default is the whole point: a handler decorated with `@operation("...")` whose id is not
in the contract, or a handler not decorated at all, is refused for everyone. The architecture
test in `tests/architecture/test_permissions_yaml.py` proves the mapping is complete, so a
missing rule fails the build rather than quietly closing an endpoint.

`step_up_is_fresh` and `attribute_satisfied` are the shared checks the step-up and approval use
cases call (Tasks 01.8 and 01.9); they are predicates, because raising the 401 with its
`WWW-Authenticate` header belongs to the layer that owns that response.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol, TypeVar
from uuid import UUID

import structlog
import yaml
from django.conf import settings
from rest_framework.exceptions import NotAuthenticated, PermissionDenied
from rest_framework.permissions import BasePermission
from rest_framework.request import Request

from hub.shared.api.problem import DEFAULT_STEP_UP_MAX_AGE_SECONDS
from hub.shared.clock import current
from hub.shared.errors import Unauthenticated

__all__ = [
    "OperationRule",
    "PermissionsYamlPermission",
    "PrincipalLike",
    "attribute_satisfied",
    "operation",
    "operation_rules",
    "roles_of",
    "step_up_is_fresh",
]

# `public` is a pseudo-role: an operation listing it is callable without any credential.
PUBLIC = "public"

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "trace"})

ViewHandler = TypeVar("ViewHandler", bound=Callable[..., Any])


class PrincipalLike(Protocol):
    """The fields of the caller this layer reads.

    ``identity.contracts.Principal`` is the real value (docs/03 2.2), but `shared` may not
    import a business module (docs/03 section 0), so the kernel names the shape it consumes and
    the authentication class supplies an object that satisfies it.
    """

    subject_id: UUID
    kind: str
    roles: frozenset[str]
    attributes: frozenset[str]
    auth_time: datetime | None


@dataclass(frozen=True, slots=True)
class OperationRule:
    """What the contract says about one operation."""

    operation_id: str
    roles: frozenset[str]
    step_up_max_age_seconds: int | None = None
    step_up_problem_code: str = "step_up_required"
    requires_attribute: str | None = None
    approval: str | None = None
    site_scoped: bool = False


# A refusal is recorded by the module that owns the audit log, because `shared` may not import
# it (docs/03 section 0). `identity.apps.IdentityConfig.ready` installs the recorder; without
# one the permission class still refuses, it just cannot say so in the audit trail.
DenialRecorder = Callable[["PrincipalLike | None", OperationRule, str], None]

_denial_recorder: DenialRecorder | None = None

logger = structlog.get_logger(__name__)


def set_denial_recorder(recorder: DenialRecorder | None) -> None:
    """Install (or, with ``None``, remove) the callable that records a refusal."""
    global _denial_recorder  # noqa: PLW0603
    _denial_recorder = recorder


def _record_denial(principal: PrincipalLike | None, rule: OperationRule, reason: str) -> None:
    """Write the refusal if a recorder is installed; never let the audit break the answer."""
    if _denial_recorder is None:
        return
    try:
        _denial_recorder(principal, rule, reason)
    except Exception:  # noqa: BLE001 - a refusal must survive a broken audit sink
        logger.warning(
            "authorization.denial_not_recorded",
            operation_id=rule.operation_id,
            reason=reason,
            exc_info=True,
        )


def operation(operation_id: str) -> Callable[[ViewHandler], ViewHandler]:
    """Mark a handler with its `operationId`; the permission class reads it (docs/03 7.7.2)."""

    def decorate(handler: ViewHandler) -> ViewHandler:
        setattr(handler, "operation_id", operation_id)  # noqa: B010 - functions, not instances
        return handler

    return decorate


def _contract_dir() -> Path:
    return Path(str(settings.HUB_CONTRACT_DIR))


@lru_cache(maxsize=4)
def _load(permissions_path: str, openapi_path: str) -> dict[str, OperationRule]:
    allowed = yaml.safe_load(Path(permissions_path).read_text(encoding="utf-8"))["operations"]
    document = yaml.safe_load(Path(openapi_path).read_text(encoding="utf-8"))

    rules: dict[str, OperationRule] = {}
    for item in document["paths"].values():
        for method, declared in item.items():
            if method not in HTTP_METHODS or not isinstance(declared, dict):
                continue
            operation_id = declared.get("operationId")
            if not isinstance(operation_id, str):
                continue
            step_up = declared.get("x-step-up") or {}
            rules[operation_id] = OperationRule(
                operation_id=operation_id,
                roles=frozenset(allowed.get(operation_id) or ()),
                step_up_max_age_seconds=(
                    int(step_up["max_age_seconds"]) if "max_age_seconds" in step_up else None
                ),
                step_up_problem_code=str(step_up.get("problem_code", "step_up_required")),
                requires_attribute=declared.get("x-requires-attribute"),
                approval=declared.get("x-approval"),
                site_scoped=bool(declared.get("x-site-scoped", False)),
            )
    return rules


def operation_rules() -> Mapping[str, OperationRule]:
    """The contract's rules, parsed once per process."""
    directory = _contract_dir()
    return _load(str(directory / "permissions.yaml"), str(directory / "openapi.yaml"))


def roles_of(principal: PrincipalLike) -> frozenset[str]:
    """The role set a principal acts with: staff roles, or the kind of a machine principal."""
    if principal.kind == "staff":
        return principal.roles
    return frozenset({principal.kind})


def step_up_is_fresh(principal: PrincipalLike, rule: OperationRule, *, now: datetime) -> bool:
    """True when the operation needs no step-up, or this session proved identity recently."""
    if rule.step_up_max_age_seconds is None:
        return True
    if principal.auth_time is None:
        return False
    return (now - principal.auth_time).total_seconds() <= rule.step_up_max_age_seconds


def attribute_satisfied(principal: PrincipalLike, rule: OperationRule) -> bool:
    """True when the operation needs no attribute, or the principal holds the one it names."""
    required = rule.requires_attribute
    return required is None or required in principal.attributes


def _operation_id(view: Any, request: Request) -> str | None:
    """The operation this call implements, from the view or from the handler serving it."""
    declared = getattr(view, "operation_id", None)
    if isinstance(declared, str):
        return declared
    # A viewset exposes the action under `self.action`; a plain view is served by a method.
    for name in (getattr(view, "action", None), getattr(request, "method", "").lower()):
        if isinstance(name, str):
            found = getattr(getattr(view, name, None), "operation_id", None)
            if isinstance(found, str):
                return found
    return None


def _principal(request: Request) -> PrincipalLike | None:
    """The principal the authentication class attached, if any.

    Duck-typed rather than isinstance-checked for the same reason as :class:`PrincipalLike`:
    the concrete class belongs to `identity`, which `shared` does not import.
    """
    principal = getattr(request, "principal", None)
    return principal if isinstance(getattr(principal, "kind", None), str) else None


class PermissionsYamlPermission(BasePermission):
    """The only permission class: the rule of `api/permissions.yaml`, or nothing at all."""

    message = "forbidden"

    def has_permission(self, request: Request, view: Any) -> bool:
        operation_id = _operation_id(view, request)
        rule = operation_rules().get(operation_id) if operation_id is not None else None
        if rule is None:
            # Deny by default: an undeclared handler has no roles and no exception.
            undeclared = OperationRule(operation_id=operation_id or "", roles=frozenset())
            _record_denial(_principal(request), undeclared, "undeclared")
            raise PermissionDenied("forbidden")

        if PUBLIC in rule.roles:
            return True

        principal = _principal(request)
        if principal is None:
            _record_denial(None, rule, "unauthenticated")
            raise NotAuthenticated()

        if not (roles_of(principal) & rule.roles):
            _record_denial(principal, rule, "role")
            raise PermissionDenied("forbidden")

        if not step_up_is_fresh(principal, rule, now=current()):
            # 401, not 403: the caller is allowed to do this, but not with an authentication
            # this old. The challenge tells the client how to ask again (RFC 6750, D-16).
            _record_denial(principal, rule, "step_up")
            raise Unauthenticated(
                code=rule.step_up_problem_code,
                message_key="step_up_required",
                details={
                    "step_up": {
                        "max_age": rule.step_up_max_age_seconds or DEFAULT_STEP_UP_MAX_AGE_SECONDS
                    }
                },
            )
        return True
