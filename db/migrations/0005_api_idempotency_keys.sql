-- phase: expand
-- 0005_api_idempotency_keys: let the api pool read the key it just took.
--
-- Found by running the top-up route as the role that serves it. The baseline grants `hub_api`
-- INSERT and UPDATE on `hub.idempotency_keys` (the reservation and the stored answer) and
-- `SELECT` on the reference tables around it, but not on this one — so the first statement of
-- `shared.api.idempotency._reserve`, the `SELECT ... FOR UPDATE` that decides whether the key is
-- already held, answers `permission denied for table idempotency_keys` and every idempotent
-- endpoint becomes a 500. `hub_ingest` and `hub_worker` were given the read as well; the api
-- role was the one that was missed.
--
-- A table-level SELECT rather than a column list: every column here is the caller's own request
-- (its key, the hash of its body and the answer it was given), and the erasure path already
-- relies on the same role reading the row it must scrub.
--
-- Additive and idempotent; `db/schema.sql` is frozen (ADR-0020) and is not regenerated.

GRANT SELECT ON hub.idempotency_keys TO hub_api;
