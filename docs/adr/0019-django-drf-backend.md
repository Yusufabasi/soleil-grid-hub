# ADR-0019: Django with Django REST framework for the backend

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead; the product owner stated Django as their preferred backend after revision 0 had chosen FastAPI

## Context

Revision 0 chose FastAPI, SQLAlchemy and Alembic. The owner prefers Django, and an implementing agent will
work from the plan, so familiarity and maturity matter. The question is whether FastAPI is clearly better
for this system. Measured against the actual load (about 30 webhooks per second on paydays, about 417
telemetry batches per second at 5x peak, about 200 staff users) it is not: FastAPI's main advantage, async
throughput per process, is not needed at these rates.

## Decision

Django 5 with Django REST framework and psycopg 3. gunicorn (sync or threaded) serves the API, webhook and
telemetry pools; uvicorn with async views serves only the dashboard stream pool. `ATOMIC_REQUESTS` is off
and transactions are explicit. The Django admin is not installed (it bypasses maker-checker, ADR-0007).
The authentication stack is custom over `hub.users` (ADR-0006). The OpenAPI file stays the contract and the
implementation is proven against it with schemathesis.

## Alternatives Considered

### FastAPI with SQLAlchemy (the revision 0 choice)
- **Pros**: async, typed request models, specification generated from code, lighter.
- **Cons**: assembles auth, permissions, migrations, admin tooling and security defaults from separate libraries.
- **Why not**: its advantage is throughput we do not need; Django's maturity in data-heavy internal tools, built-in CSRF and XSS protections and ecosystem fit better, and it is the owner's preference.

### Django Ninja instead of DRF
- **Pros**: typed schemas, async-friendly, generates OpenAPI 3.1.
- **Cons**: smaller ecosystem and fewer proven patterns for large permission models.
- **Why not**: DRF's maturity and the wide familiarity of implementing agents; the permission class is generated from `api/permissions.yaml` either way.

## Consequences

### Positive
- Mature migrations tooling for the non-ledger parts, security defaults and a large ecosystem.
- Familiar to the implementer and to the Terms of Reference's Python requirement.

### Negative
- Async work (the stream pool) needs a separate ASGI process type.
- The ORM encourages patterns the design forbids (cross-module table access, direct ledger writes); import-linter and database grants enforce the boundaries instead.
- Per-request overhead is higher than FastAPI's; acceptable at the stated load and measured in the load tests.

### Risks
- A developer enables the Django admin or `ATOMIC_REQUESTS` out of habit. Mitigation: a CI check that neither is configured, and the executor guide forbids both.
