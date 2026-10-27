-- phase: expand
-- 0008_ingest_transaction_currency: the currency of the transaction a reversal is about.
--
-- `ConfirmPayment._reverse` → `LedgerService.reverse` reverses through the definer function
-- `hub.reverse_ledger_transaction`, but it first read the original transaction to learn the
-- currency its `Money` result reports. The webhook pool may read no ledger transaction and no
-- entry (0004, 0006 — `hub_ingest` has neither grant nor policy there), so a real
-- `payment.reversed` delivery answered `permission denied for table ledger_transactions`: the
-- provider's money stayed credited, the provider got a 500 and retried.
--
-- One value, for one transaction id, definer-owned and granted to the three roles that post —
-- the same shape and the same reasoning as 0007. The role cannot list transactions, entries or
-- amounts with it, and the read it replaces is a single currency, not a row.
--
-- Additive and idempotent; `db/schema.sql` is frozen (ADR-0020) and is not regenerated.

CREATE OR REPLACE FUNCTION hub.transaction_currency(p_transaction uuid) RETURNS char(3)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, hub, pg_temp AS $$
  -- The first line by account id, which is the line `LedgerReader` reports first.
  SELECT e.currency FROM hub.ledger_entries e
   WHERE e.transaction_id = p_transaction
   ORDER BY e.account_id
   LIMIT 1
$$;

ALTER FUNCTION hub.transaction_currency(uuid) OWNER TO ledger_owner;
REVOKE ALL ON FUNCTION hub.transaction_currency(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hub.transaction_currency(uuid) TO hub_api, hub_ingest, hub_worker;
