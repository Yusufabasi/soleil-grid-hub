-- phase: expand
-- 0006_ingest_ledger_transaction_read: let the webhook path ask whether it already posted.
--
-- `LedgerWriter.post` decides between "post it" and "it is already there" by reading
-- `hub.ledger_transactions` on the posting key, and then proves the row belongs to *this*
-- database transaction by comparing `created_xid`. `hub_ingest` posts through the definer
-- functions and had SELECT on nothing in the ledger, so the first delivery of a top-up answered
-- `permission denied for table ledger_transactions` and the credit never happened — the money
-- stayed on the provider's side while the provider got a 500 and its own retry.
--
-- Three columns, not the table: the id, the key it is looked up by, and the transaction id that
-- proves who wrote the row. Amounts, kinds, customers and entries stay unreadable to this role,
-- which is what `db/migrations/0004` protected by granting only the account ids a posting names.
--
-- Additive and idempotent; `db/schema.sql` is frozen (ADR-0020) and is not regenerated.

GRANT SELECT (id, posting_key, created_xid) ON hub.ledger_transactions TO hub_ingest;
