-- phase: expand
-- 0001_ledger_system_accounts: the ten (fourteen, counting providers) system accounts.
--
-- No application role may create a ledger account that is not a customer's wallet, and no
-- function creates the system ones either (db/schema.sql 4b: `create_wallet_account` is only
-- for wallets). They are therefore seed data of the deployment, inserted once by the migrator:
--
--   * `provider_clearing.<provider>.<ccy>` — money a provider holds for us, per provider;
--   * `revenue_energy.<ccy>` — what a token sale earned;
--   * `fees.<ccy>` — fixed fees and provider commission;
--   * `suspense.<ccy>` — manual adjustments, until finance explains them;
--   * `chargeback_receivable.<ccy>` — the shortfall of a reversal after energy was issued (D-06).
--
-- Idempotent (`ON CONFLICT (code) DO NOTHING`), because a re-run must not fail a deployment and
-- because a provider added later is a new row in this file, not a new mechanism.
INSERT INTO hub.ledger_accounts (id, code, kind, currency)
SELECT hub.uuid_v7(), v.code, v.kind, v.currency
  FROM (VALUES
    ('provider_clearing.mpesa_cd.USD', 'provider_clearing', 'USD'),
    ('provider_clearing.mpesa_cd.CDF', 'provider_clearing', 'CDF'),
    ('provider_clearing.airtel_cd.USD', 'provider_clearing', 'USD'),
    ('provider_clearing.airtel_cd.CDF', 'provider_clearing', 'CDF'),
    ('provider_clearing.orange_cd.USD', 'provider_clearing', 'USD'),
    ('provider_clearing.orange_cd.CDF', 'provider_clearing', 'CDF'),
    ('revenue_energy.USD', 'revenue_energy', 'USD'),
    ('revenue_energy.CDF', 'revenue_energy', 'CDF'),
    ('fees.USD', 'fees', 'USD'),
    ('fees.CDF', 'fees', 'CDF'),
    ('suspense.USD', 'suspense', 'USD'),
    ('suspense.CDF', 'suspense', 'CDF'),
    ('chargeback_receivable.USD', 'chargeback_receivable', 'USD'),
    ('chargeback_receivable.CDF', 'chargeback_receivable', 'CDF')
  ) AS v(code, kind, currency)
ON CONFLICT (code) DO NOTHING;