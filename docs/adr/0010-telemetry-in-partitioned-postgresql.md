# ADR-0010: Telemetry in partitioned PostgreSQL, with measured exit triggers

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, database engineer, system architect, SRE

## Context

At 300,000 meters telemetry is 28.8 million rows a day (about 4 GB a day, 1.5 TB a year raw). It
shares a database with the ledger, which risks payment latency (SRE challenge 2).

## Decision

Keep telemetry in a monthly range-partitioned PostgreSQL table in v1, with its own PgBouncer pool and
database role, batched `COPY` ingestion, and a kill switch. Key `(meter_id, read_at)`, first write wins.
Batches are at most 1,000 readings or 256 KB; readings up to 7 days late are accepted. Raw data is kept
90 days hot, archived for 13 months, then kept as hourly aggregates. Move to weekly partitions above
50,000 meters. Leave PostgreSQL for a dedicated telemetry store when hot data exceeds 500 GB,
sustained ingest exceeds 3,000 rows per second, or insert latency breaks its target.

## Alternatives Considered

### TimescaleDB or a dedicated time-series database from the start
- **Pros**: purpose-built compression and retention.
- **Cons**: another system to run before it is needed.
- **Why not**: the numbers justify it only at the triggers above; the exit path is designed.

### Reduce volume at the source
- **Pros**: cheapest rows are the ones not stored.
- **Cons**: depends on firmware.
- **Why not**: recorded as an option; hourly aggregates for non-diagnostic fields could cut volume sharply.

## Consequences

### Positive
- No new datastore at launch; ingest cannot take payments down because of separate roles and pools.

### Negative
- Telemetry still shares WAL and vacuum with the ledger until the exit trigger fires.

### Risks
- A reconnection storm after an outage; gateways add jitter and the load test includes it.
