-- phase: expand
-- 0007_ingest_wallet_balance_read: one balance, one account, for the roles that post to it.
--
-- `LedgerService.post` stages a `wallet.low_balance` warning when the wallet it just credited
-- ended below `LEDGER_LOW_BALANCE_MINOR`, so the path that credits a wallet has to read that
-- balance. The webhook pool posts through the definer function `hub.post_ledger_transaction`
-- and holds no grant and no policy on `hub.wallet_balances` (docs/03 6.1, D-22; 0004 keeps it
-- that way: ids and codes, no balances). Under `FORCE ROW LEVEL SECURITY` a missing policy is
-- not an error — it is zero rows, and the reader would answer "balance 0", i.e. a low-balance
-- warning on every single top-up.
--
-- The function is the narrowest thing that answers the question: SECURITY DEFINER as
-- `ledger_owner`, the one role with a policy on that table, one account id in and one integer
-- out. It exposes no table, no entries and no way to enumerate accounts — a caller can only ask
-- about an account id it already holds — and it is granted to exactly the three roles the
-- posting function is granted to, because all three post and all three stage that warning.
-- The wallet *view* (statement, entries, balances of other customers) stays behind the row-level
-- security of `hub_api` and is untouched by this.
--
-- Additive and idempotent; `db/schema.sql` is frozen (ADR-0020) and is not regenerated.

CREATE OR REPLACE FUNCTION hub.wallet_balance_minor(p_account uuid) RETURNS bigint
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, hub, pg_temp AS $$
  SELECT coalesce(
    (SELECT w.balance_minor FROM hub.wallet_balances w WHERE w.account_id = p_account), 0
  )
$$;

ALTER FUNCTION hub.wallet_balance_minor(uuid) OWNER TO ledger_owner;
REVOKE ALL ON FUNCTION hub.wallet_balance_minor(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hub.wallet_balance_minor(uuid) TO hub_api, hub_ingest, hub_worker;
