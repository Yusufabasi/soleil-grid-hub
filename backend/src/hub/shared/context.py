"""Per-request context carried in contextvars (docs/03 section 7.3).

Nothing here reads Django or the environment: the values are set by the first middleware,
read by the logging processors and cleared again in the same request's ``finally``.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

__all__ = [
    "bind_context",
    "causation_id_var",
    "correlation_id_var",
    "db_customer_id_var",
    "db_user_id_var",
    "entrypoint_var",
    "principal_id_var",
    "request_id_var",
    "reset_context",
]

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
correlation_id_var: ContextVar[str | None] = ContextVar("correlation_id", default=None)
causation_id_var: ContextVar[str | None] = ContextVar("causation_id", default=None)
principal_id_var: ContextVar[str | None] = ContextVar("principal_id", default=None)
entrypoint_var: ContextVar[str | None] = ContextVar("entrypoint", default=None)
# Who a read should announce to row-level security (`app.user_id` / `app.customer_id`). The
# authentication class binds these from the verified token; `hub.shared.db.announced` applies
# them to the transaction a read runs in. They are not the principal: they are the two ids the
# policies ask for, and nothing reads them for an authorization decision.
db_user_id_var: ContextVar[str | None] = ContextVar("db_user_id", default=None)
db_customer_id_var: ContextVar[str | None] = ContextVar("db_customer_id", default=None)

_VARS = {
    "request_id": request_id_var,
    "correlation_id": correlation_id_var,
    "causation_id": causation_id_var,
    "principal_id": principal_id_var,
    "entrypoint": entrypoint_var,
    "db_user_id": db_user_id_var,
    "db_customer_id": db_customer_id_var,
}


def bind_context(**values: str | None) -> dict[str, Token[str | None]]:
    """Set the named contextvars and return their tokens for :func:`reset_context`."""
    tokens: dict[str, Token[str | None]] = {}
    for name, value in values.items():
        var = _VARS.get(name)
        if var is None:
            raise ValueError(f"unknown context key: {name}")
        tokens[name] = var.set(value)
    return tokens


def reset_context(tokens: dict[str, Token[str | None]]) -> None:
    """Restore the contextvars to the values they had before :func:`bind_context`."""
    for name, token in tokens.items():
        _VARS[name].reset(token)
