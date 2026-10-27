-- phase: expand
-- 0004_payments_ingest_support: let the webhook pool resolve accounts, and give a mismatched
-- delivery a place to land.
--
-- Two gaps in the frozen baseline, both found by running the credit path as the role that
-- actually serves webhooks (docs/03 6.1, D-05, D-09):
--
-- 1. `hub_ingest` can post through `hub.post_ledger_transaction` but cannot read
--    `hub.ledger_accounts`, so `to_signed_lines` — which turns an `AccountRef` into the id the
--    function takes — fails with `permission denied for table ledger_accounts`. The grant below
--    is SELECT only, and a policy makes it survive forced row-level security: ids and codes, no
--    balances (the balance view stays unreadable to this role).
-- 2. `hub.post_ledger_transaction` accepts a `topup_received` only when the stored provider event
--    matches the top-up and the wallet is credited in full, and every other kind needs an
--    approval. A delivery whose payer, amount or currency disagrees has no path to `suspense`
--    at all, which would make D-05's "route the money to suspense" unimplementable. The function
--    below posts the pair (debit clearing, credit suspense) from the *event*, which is the only
--    amount a mismatch can be trusted to state.
--
-- Additive and idempotent; `db/schema.sql` is frozen and not regenerated (docs/plan notes 1-2 of
-- slice 04). A later fold into the baseline leaves both statements harmless.

-- `hub_ingest` reads the account ids a posting names. No write, no balance view, no other table.
GRANT SELECT ON hub.ledger_accounts TO hub_ingest;
CREATE POLICY ledger_accounts_ingest ON hub.ledger_accounts
  FOR SELECT TO hub_ingest USING (true);

CREATE FUNCTION hub.post_suspense_receipt(p_posting_key text, p_provider_event uuid,
  p_clearing_account uuid, p_source_top_up uuid) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, hub, pg_temp AS $$
DECLARE
  v_ev hub.provider_events;
  v_clearing hub.ledger_accounts;
  v_suspense uuid;
  v_tx uuid;
BEGIN
  SELECT * INTO v_ev FROM hub.provider_events WHERE id = p_provider_event;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'evidence_required: provider event % not found', p_provider_event
      USING ERRCODE = 'restrict_violation';
  END IF;
  IF v_ev.event_type <> 'payment.succeeded' OR v_ev.amount_minor IS NULL
     OR v_ev.amount_minor <= 0 OR v_ev.currency IS NULL THEN
    RAISE EXCEPTION 'the suspense receipt needs a stored provider success event with a positive amount'
      USING ERRCODE = 'restrict_violation';
  END IF;
  SELECT * INTO v_clearing FROM hub.ledger_accounts WHERE id = p_clearing_account;
  IF NOT FOUND OR v_clearing.kind <> 'provider_clearing' OR v_clearing.currency <> v_ev.currency THEN
    RAISE EXCEPTION 'the clearing account must be a provider_clearing account in %', v_ev.currency
      USING ERRCODE = 'restrict_violation';
  END IF;
  SELECT id INTO v_suspense FROM hub.ledger_accounts
   WHERE kind = 'suspense' AND currency = v_ev.currency AND customer_id IS NULL
   ORDER BY id LIMIT 1;
  IF v_suspense IS NULL THEN
    RAISE EXCEPTION 'no suspense account exists for %', v_ev.currency
      USING ERRCODE = 'invalid_parameter_value';
  END IF;

  INSERT INTO hub.ledger_transactions (id, posting_key, kind, description, effective_at, source_top_up_id)
  VALUES (hub.uuid_v7(), p_posting_key, 'topup_received', 'suspense receipt', now(), p_source_top_up)
  ON CONFLICT (posting_key) DO NOTHING
  RETURNING id INTO v_tx;
  IF v_tx IS NULL THEN                       -- replay of an already posted key: no second effect
    SELECT id INTO v_tx FROM hub.ledger_transactions WHERE posting_key = p_posting_key;
    RETURN v_tx;
  END IF;
  INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency, amount_minor)
  VALUES (hub.uuid_v7(), v_tx, p_clearing_account, v_ev.currency, v_ev.amount_minor),
         (hub.uuid_v7(), v_tx, v_suspense, v_ev.currency, -v_ev.amount_minor);
  RETURN v_tx;
END $$;

ALTER FUNCTION hub.post_suspense_receipt(text, uuid, uuid, uuid) OWNER TO ledger_owner;
REVOKE ALL ON FUNCTION hub.post_suspense_receipt(text, uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION hub.post_suspense_receipt(text, uuid, uuid, uuid) TO hub_ingest, hub_worker;
