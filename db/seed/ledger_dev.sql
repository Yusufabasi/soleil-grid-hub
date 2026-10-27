-- ledger_dev.sql: a wallet per demo customer, for a local stack (`make seed`).
--
-- Wallets are opened by `hub.create_wallet_account`, the same definer function slice 04's
-- `payment.confirmed` consumer calls, so the dev data cannot take a path the application
-- cannot. Idempotent: a second run creates nothing.
--
-- Opening a wallet posts nothing. A wallet at zero is a customer who has not paid yet, which
-- is the honest state of a freshly seeded registry.
DO $$
DECLARE
  c RECORD;
BEGIN
  FOR c IN
    SELECT cu.id AS customer_id, s.currency AS currency
      FROM hub.customers cu
      JOIN hub.sites s ON s.id = cu.site_id
     ORDER BY cu.id
  LOOP
    PERFORM hub.create_wallet_account(c.customer_id, c.currency);
  END LOOP;
END $$;
