# Architecture Decision Records

Each record states the decision, the strongest alternatives rejected and the honest costs. The
decisions of revision 1 are also listed as D-nn in `../00-shared-context.md` section 7.

| ADR | Title | Status | Date |
|-----|-------|--------|------|
| [0001](0001-modular-monolith-three-deployables.md) | Modular monolith with three deployables | accepted | 2026-10-24 |
| [0002](0002-postgresql-double-entry-ledger.md) | PostgreSQL as system of record, with a double-entry ledger | accepted | 2026-10-24 |
| [0003](0003-outbox-and-redis-streams.md) | Transactional outbox with Redis Streams | accepted | 2026-10-24 |
| [0004](0004-synchronous-webhook-exactly-once.md) | Synchronous webhook processing, exactly-once effect in PostgreSQL | accepted | 2026-10-24 |
| [0005](0005-rest-openapi-schema-authority.md) | REST with OpenAPI; the schema is the authority for enumerations | accepted | 2026-10-24 |
| [0006](0006-authentication-and-sessions.md) | Authentication and sessions | accepted | 2026-10-24 |
| [0007](0007-maker-checker-approvals.md) | Maker-checker approvals for money and privilege changes | accepted | 2026-10-24 |
| [0008](0008-l7-load-balancing-separate-pools.md) | Layer-7 load balancing with separate pools | accepted | 2026-10-24 |
| [0009](0009-caching-strategy.md) | Cache-aside with stampede control, and what is never cached | accepted | 2026-10-24 |
| [0010](0010-telemetry-in-partitioned-postgresql.md) | Telemetry in partitioned PostgreSQL with measured exit triggers | accepted | 2026-10-24 |
| [0011](0011-server-sent-events-for-live-updates.md) | Server-Sent Events for the live dashboard | accepted | 2026-10-24 |
| [0012](0012-ai-gateway-single-egress.md) | One AI gateway as the single egress to Claude | accepted | 2026-10-24 |
| [0013](0013-ask-the-data-guarded-read-only.md) | Ask-the-data through guarded, read-only, per-role analytics | accepted | 2026-10-24 |
| [0014](0014-erasure-by-pseudonymisation.md) | Erasure by pseudonymisation, with a single retention table | accepted | 2026-10-24 |
| [0015](0015-postgresql-topology-and-recovery.md) | PostgreSQL topology and recovery targets | accepted | 2026-10-24 |
| [0016](0016-single-default-model-measured-routing.md) | One default model, with cheaper routing only after measurement | accepted | 2026-10-24 |
| [0017](0017-token-issuance-sole-write-to-site-systems.md) | Energy-credit token issuance is the only write to site systems | accepted | 2026-10-24 |
| [0018](0018-per-deployable-database-roles.md) | Per-deployable database roles and definer functions | accepted | 2026-10-24 |
| [0019](0019-django-drf-backend.md) | Django with Django REST framework for the backend | accepted | 2026-10-24 |
| [0020](0020-sql-first-schema-unmanaged-django-models.md) | SQL-first schema with unmanaged Django models | accepted | 2026-10-24 |
| [0021](0021-nextjs-frontend-hub-web.md) | Next.js frontend as its own deployable (`hub-web`) | accepted | 2026-10-24 |
