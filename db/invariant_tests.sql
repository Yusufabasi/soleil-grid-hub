-- Soleil Grid Hub: invariant tests (revision 1). Run after the schema, as the superuser that loaded it, one
-- statement at a time (autocommit). Every statement is preceded by its expectation. Statements are order
-- dependent and the ids are fixed, so recreate the database for each run.
-- Fixed ids: sites 1111..01/02, plan 2222..01, users 3333..01-06, customers 4444..01-03, meters 5555..01-03,
-- accounts 6666..01 wallet(c1) ..02 clearing ..03 revenue ..04 suspense CDF ..05 chargeback ..06 wallet(c2) ..07 wallet(c3).
-- Users: 01 agent (site 1), 02 approver (all sites), 03 second approver, 04 data-protection admin, 05 no sites, 06 administrator.

-- ===== Setup =====
-- EXPECT: ok
INSERT INTO hub.sites (id, code, name, currency) VALUES
  ('11111111-0000-7000-8000-000000000001', 'T-LUB', 'Test site Lubumbashi', 'USD'),
  ('11111111-0000-7000-8000-000000000002', 'T-KOL', 'Test site Kolwezi', 'USD');
-- EXPECT: ok
INSERT INTO hub.plans (id, code, name, currency, min_topup_minor, max_topup_minor)
VALUES ('22222222-0000-7000-8000-000000000001', 'T-STD', 'Standard', 'USD', 100, 100000);
-- EXPECT: ok
INSERT INTO hub.users (id, email, display_name, password_hash, status, all_sites, dp_admin, finance_lead) VALUES
  ('33333333-0000-7000-8000-000000000001', 'agent@example.test', 'Test Agent', 'argon2id$placeholder', 'active', false, false, false),
  ('33333333-0000-7000-8000-000000000002', 'approver@example.test', 'Test Approver', 'argon2id$placeholder', 'active', true, false, true),
  ('33333333-0000-7000-8000-000000000003', 'approver2@example.test', 'Test Approver Two', 'argon2id$placeholder', 'active', true, false, false),
  ('33333333-0000-7000-8000-000000000004', 'dpo@example.test', 'Test DP Admin', 'argon2id$placeholder', 'active', true, true, false),
  ('33333333-0000-7000-8000-000000000005', 'nosites@example.test', 'Test No Sites', 'argon2id$placeholder', 'active', false, false, false),
  ('33333333-0000-7000-8000-000000000006', 'admin@example.test', 'Test Administrator', 'argon2id$placeholder', 'active', true, false, false);
-- EXPECT: ok
INSERT INTO hub.user_sites (user_id, site_id) VALUES ('33333333-0000-7000-8000-000000000001', '11111111-0000-7000-8000-000000000001');
-- EXPECT: ok
INSERT INTO hub.user_roles (user_id, role_code) VALUES
  ('33333333-0000-7000-8000-000000000001', 'support_agent'),
  ('33333333-0000-7000-8000-000000000006', 'administrator'),
  ('33333333-0000-7000-8000-000000000002', 'finance_officer');
-- EXPECT: ok
INSERT INTO hub.customers (id, site_id, plan_id, full_name, phone_e164, email, language) VALUES
  ('44444444-0000-7000-8000-000000000001', '11111111-0000-7000-8000-000000000001', '22222222-0000-7000-8000-000000000001', 'Amina Test', '+243970000123', 'amina@example.test', 'fr'),
  ('44444444-0000-7000-8000-000000000002', '11111111-0000-7000-8000-000000000002', '22222222-0000-7000-8000-000000000001', 'Bruno Test', '+243970000456', 'bruno@example.test', 'fr'),
  ('44444444-0000-7000-8000-000000000003', '11111111-0000-7000-8000-000000000001', '22222222-0000-7000-8000-000000000001', 'Chantal Test', '+243970000789', 'chantal@example.test', 'fr');
-- EXPECT: ok
INSERT INTO hub.meters (id, serial_no, site_id, customer_id, status) VALUES
  ('55555555-0000-7000-8000-000000000001', 'MTR-T-0001', '11111111-0000-7000-8000-000000000001', '44444444-0000-7000-8000-000000000001', 'active'),
  ('55555555-0000-7000-8000-000000000002', 'MTR-T-0002', '11111111-0000-7000-8000-000000000002', '44444444-0000-7000-8000-000000000002', 'active'),
  ('55555555-0000-7000-8000-000000000003', 'MTR-T-0003', '11111111-0000-7000-8000-000000000001', '44444444-0000-7000-8000-000000000003', 'active');
-- EXPECT: ok
INSERT INTO hub.approvals (id, action, maker_user_id, maker_session_id, payload, status, approver_user_id, approver_session_id, decided_at, expires_at)
VALUES ('88888888-0000-7000-8000-000000000001', 'tariff.change', '33333333-0000-7000-8000-000000000001', '77777777-0000-7000-8000-0000000000a1',
        '{"plan":"T-STD","tariff_minor_per_kwh":35}', 'approved', '33333333-0000-7000-8000-000000000002', '77777777-0000-7000-8000-0000000000b2',
        now(), now() + interval '1 day');
-- EXPECT: ok
INSERT INTO hub.plan_tariffs (id, plan_id, tariff_minor_per_kwh, effective_from, status, proposed_by, approval_id)
VALUES ('a1a1a1a1-0000-7000-8000-000000000001', '22222222-0000-7000-8000-000000000001', 35, now() - interval '1 day', 'active',
        '33333333-0000-7000-8000-000000000001', '88888888-0000-7000-8000-000000000001');
-- EXPECT: ok
INSERT INTO hub.ledger_accounts (id, code, kind, currency, customer_id, site_id) VALUES
  ('66666666-0000-7000-8000-000000000001', 'wallet.test.USD', 'customer_wallet', 'USD', '44444444-0000-7000-8000-000000000001', NULL),
  ('66666666-0000-7000-8000-000000000002', 'provider_clearing.test.USD', 'provider_clearing', 'USD', NULL, NULL),
  ('66666666-0000-7000-8000-000000000003', 'revenue_energy.test.USD', 'revenue_energy', 'USD', NULL, '11111111-0000-7000-8000-000000000001'),
  ('66666666-0000-7000-8000-000000000004', 'suspense.test.CDF', 'suspense', 'CDF', NULL, NULL),
  ('66666666-0000-7000-8000-000000000005', 'chargeback_receivable.test.USD', 'chargeback_receivable', 'USD', NULL, NULL),
  ('66666666-0000-7000-8000-000000000006', 'wallet.test2.USD', 'customer_wallet', 'USD', '44444444-0000-7000-8000-000000000002', NULL),
  ('66666666-0000-7000-8000-000000000007', 'wallet.test3.USD', 'customer_wallet', 'USD', '44444444-0000-7000-8000-000000000003', NULL);

-- ===== Ledger: balance rule =====
-- EXPECT: error
WITH t AS (
  INSERT INTO hub.ledger_transactions (id, posting_key, kind) VALUES (hub.uuid_v7(), 'test:unbalanced:1', 'topup_received') RETURNING id)
INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency, amount_minor)
SELECT hub.uuid_v7(), t.id, v.acc, 'USD', v.amt
FROM t, (VALUES ('66666666-0000-7000-8000-000000000002'::uuid, 1000::bigint),
                ('66666666-0000-7000-8000-000000000001'::uuid, -900::bigint)) AS v(acc, amt);
-- EXPECT: rows=0
SELECT 1 FROM hub.ledger_transactions WHERE posting_key = 'test:unbalanced:1';
-- EXPECT: error
WITH t AS (
  INSERT INTO hub.ledger_transactions (id, posting_key, kind) VALUES (hub.uuid_v7(), 'test:crosscurrency:1', 'topup_received') RETURNING id)
INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency, amount_minor)
SELECT hub.uuid_v7(), t.id, v.acc, v.cur, v.amt
FROM t, (VALUES ('66666666-0000-7000-8000-000000000002'::uuid, 'USD'::char(3), 1000::bigint),
                ('66666666-0000-7000-8000-000000000004'::uuid, 'CDF'::char(3), -1000::bigint)) AS v(acc, cur, amt);
-- EXPECT: error
INSERT INTO hub.ledger_transactions (id, posting_key, kind) VALUES (hub.uuid_v7(), 'test:empty:1', 'topup_received');
-- EXPECT: error
WITH t AS (
  INSERT INTO hub.ledger_transactions (id, posting_key, kind) VALUES (hub.uuid_v7(), 'test:wrongcurrency:1', 'topup_received') RETURNING id)
INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency, amount_minor)
SELECT hub.uuid_v7(), t.id, v.acc, 'CDF', v.amt
FROM t, (VALUES ('66666666-0000-7000-8000-000000000002'::uuid, 1000::bigint),
                ('66666666-0000-7000-8000-000000000004'::uuid, -1000::bigint)) AS v(acc, amt);
-- EXPECT: ok
WITH t AS (
  INSERT INTO hub.ledger_transactions (id, posting_key, kind) VALUES (hub.uuid_v7(), 'test:balanced:1', 'topup_received') RETURNING id)
INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency, amount_minor)
SELECT hub.uuid_v7(), t.id, v.acc, 'USD', v.amt
FROM t, (VALUES ('66666666-0000-7000-8000-000000000002'::uuid, 1000::bigint),
                ('66666666-0000-7000-8000-000000000001'::uuid, -1000::bigint)) AS v(acc, amt);
-- EXPECT: rows=1
SELECT 1 FROM hub.wallet_balances WHERE account_id = '66666666-0000-7000-8000-000000000001' AND balance_minor = 1000;
-- EXPECT: error
WITH t AS (
  INSERT INTO hub.ledger_transactions (id, posting_key, kind) VALUES (hub.uuid_v7(), 'test:balanced:1', 'topup_received') RETURNING id)
INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency, amount_minor)
SELECT hub.uuid_v7(), t.id, v.acc, 'USD', v.amt
FROM t, (VALUES ('66666666-0000-7000-8000-000000000002'::uuid, 1000::bigint),
                ('66666666-0000-7000-8000-000000000001'::uuid, -1000::bigint)) AS v(acc, amt);
-- EXPECT: rows=1
SELECT 1 FROM hub.wallet_balances WHERE account_id = '66666666-0000-7000-8000-000000000001' AND balance_minor = 1000;
-- EXPECT: error
WITH t AS (
  INSERT INTO hub.ledger_transactions (id, posting_key, kind) VALUES (hub.uuid_v7(), 'test:overdraft:1', 'energy_purchase') RETURNING id)
INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency, amount_minor)
SELECT hub.uuid_v7(), t.id, v.acc, 'USD', v.amt
FROM t, (VALUES ('66666666-0000-7000-8000-000000000001'::uuid, 5000::bigint),
                ('66666666-0000-7000-8000-000000000003'::uuid, -5000::bigint)) AS v(acc, amt);
-- EXPECT: ok
WITH t AS (
  INSERT INTO hub.ledger_transactions (id, posting_key, kind) VALUES (hub.uuid_v7(), 'test:purchase:1', 'energy_purchase') RETURNING id)
INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency, amount_minor)
SELECT hub.uuid_v7(), t.id, v.acc, 'USD', v.amt
FROM t, (VALUES ('66666666-0000-7000-8000-000000000001'::uuid, 400::bigint),
                ('66666666-0000-7000-8000-000000000003'::uuid, -400::bigint)) AS v(acc, amt);
-- EXPECT: rows=1
SELECT 1 FROM hub.wallet_balances WHERE account_id = '66666666-0000-7000-8000-000000000001' AND balance_minor = 600;
-- EXPECT: rows=0
SELECT * FROM hub.v_wallet_balance_drift;
-- S-9: a balanced pair inserted later into an already committed transaction would rewrite its effect.
-- EXPECT: error
INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency, amount_minor)
SELECT hub.uuid_v7(), (SELECT id FROM hub.ledger_transactions WHERE posting_key = 'test:balanced:1'), v.acc, 'USD', v.amt
FROM (VALUES ('66666666-0000-7000-8000-000000000002'::uuid, 5::bigint), ('66666666-0000-7000-8000-000000000003'::uuid, -5::bigint)) AS v(acc, amt);
-- EXPECT: ok
DO $$ DECLARE m text; BEGIN
  INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency, amount_minor)
  SELECT hub.uuid_v7(), (SELECT id FROM hub.ledger_transactions WHERE posting_key = 'test:balanced:1'), v.acc, 'USD', v.amt
  FROM (VALUES ('66666666-0000-7000-8000-000000000002'::uuid, 5::bigint), ('66666666-0000-7000-8000-000000000003'::uuid, -5::bigint)) AS v(acc, amt);
  RAISE EXCEPTION 'NOT_RAISED';
EXCEPTION WHEN SQLSTATE '23001' THEN
  GET STACKED DIAGNOSTICS m = MESSAGE_TEXT;
  IF m NOT LIKE 'entries can only be added%' THEN RAISE EXCEPTION 'wrong error: %', m; END IF;
END $$;

-- ===== Accounting period guard (D-23) =====
-- EXPECT: ok
INSERT INTO hub.accounting_periods (id, period_start, period_end, status, locked_by, locked_at)
VALUES ('bbbbbbbb-0000-7000-8000-000000000001', '2020-01-01', '2020-01-31', 'locked', '33333333-0000-7000-8000-000000000002', now());
-- EXPECT: ok
DO $$ DECLARE m text; BEGIN
  INSERT INTO hub.ledger_transactions (id, posting_key, kind, effective_at) VALUES (hub.uuid_v7(), 'test:locked:1', 'fee', '2020-01-15 12:00:00+00');
  RAISE EXCEPTION 'NOT_RAISED';
EXCEPTION WHEN SQLSTATE '23001' THEN
  GET STACKED DIAGNOSTICS m = MESSAGE_TEXT;
  IF m NOT LIKE 'period_locked%' THEN RAISE EXCEPTION 'wrong error: %', m; END IF;
END $$;
-- EXPECT: ok
WITH t AS (
  INSERT INTO hub.ledger_transactions (id, posting_key, kind, effective_at) VALUES (hub.uuid_v7(), 'test:open-period:1', 'fee', '2020-02-15 12:00:00+00') RETURNING id)
INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency, amount_minor)
SELECT hub.uuid_v7(), t.id, v.acc, 'USD', v.amt
FROM t, (VALUES ('66666666-0000-7000-8000-000000000002'::uuid, 50::bigint), ('66666666-0000-7000-8000-000000000003'::uuid, -50::bigint)) AS v(acc, amt);

-- ===== Ledger and audit immutability =====
-- EXPECT: error
UPDATE hub.ledger_entries SET amount_minor = amount_minor + 1;
-- EXPECT: error
UPDATE hub.ledger_entries SET amount_minor = 1 WHERE false;
-- EXPECT: error
DELETE FROM hub.ledger_entries;
-- EXPECT: error
TRUNCATE hub.ledger_entries;
-- EXPECT: error
UPDATE hub.ledger_transactions SET description = 'tampered';
-- EXPECT: error
DELETE FROM hub.ledger_transactions;
-- S-9: the guards are ENABLE ALWAYS, so a replica-role session (restore tooling) cannot bypass them.
-- EXPECT: error
DO $$ BEGIN SET LOCAL session_replication_role = replica; UPDATE hub.ledger_entries SET amount_minor = amount_minor + 1; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL session_replication_role = replica; DELETE FROM hub.audit_log; END $$;
-- EXPECT: rows=14
SELECT t.tgname FROM pg_trigger t WHERE t.tgrelid IN ('hub.ledger_entries'::regclass, 'hub.ledger_transactions'::regclass, 'hub.audit_log'::regclass,
  'hub.audit_anchors'::regclass, 'hub.erasure_log'::regclass) AND NOT t.tgisinternal AND t.tgenabled = 'A';

-- ===== Webhook inbox: duplicates and the quarantine (D-09) =====
-- EXPECT: ok
INSERT INTO hub.provider_events (id, provider, event_id, provider_txn_id, event_type, amount_minor, currency, payload)
VALUES (hub.uuid_v7(), 'mtn_stub', 'evt-0001', 'txn-0001', 'payment.succeeded', 1000, 'USD', '{"payer_msisdn":"+243970000123","amount":1000}');
-- EXPECT: error
INSERT INTO hub.provider_events (id, provider, event_id, provider_txn_id, event_type, amount_minor, currency, payload)
VALUES (hub.uuid_v7(), 'mtn_stub', 'evt-0001', 'txn-0099', 'payment.succeeded', 1000, 'USD', '{}');
-- the second unique key: same provider transaction id under a different event id
-- EXPECT: error
INSERT INTO hub.provider_events (id, provider, event_id, provider_txn_id, event_type, amount_minor, currency, payload)
VALUES (hub.uuid_v7(), 'mtn_stub', 'evt-0098', 'txn-0001', 'payment.succeeded', 1000, 'USD', '{}');
-- EXPECT: rows=0
INSERT INTO hub.provider_events (id, provider, event_id, provider_txn_id, event_type, amount_minor, currency, payload)
VALUES (hub.uuid_v7(), 'mtn_stub', 'evt-0001', 'txn-0001', 'payment.succeeded', 1000, 'USD', '{}')
ON CONFLICT (provider, event_id) DO NOTHING RETURNING id;
-- EXPECT: ok
INSERT INTO hub.provider_events (id, provider, event_id, provider_txn_id, event_type, amount_minor, currency, payload)
VALUES (hub.uuid_v7(), 'other_stub', 'evt-0001', 'txn-0001', 'payment.succeeded', 50, 'USD', '{}');
-- invalid-signature deliveries have no place in provider_events: there is no signature_valid column
-- EXPECT: rows=0
SELECT column_name FROM information_schema.columns WHERE table_schema = 'hub' AND table_name = 'provider_events' AND column_name LIKE 'signature%';
-- EXPECT: error
INSERT INTO hub.provider_events (id, provider, event_id, provider_txn_id, event_type, payload)
VALUES (hub.uuid_v7(), 'mtn_stub', 'evt-bad-type', 'txn-bad', 'payment.invalid_signature', '{}');
-- EXPECT: ok
INSERT INTO hub.webhook_quarantine (id, provider, reason, source_ip, body_sha256, body_length, signature_hdr)
VALUES (hub.uuid_v7(), 'mtn_stub', 'bad_signature', '203.0.113.9', repeat('a', 64), 120, 'v1=deadbeef');

-- ===== Top-ups: snapshot, ownership, state machine (D-05, D-08, 7.2) =====
-- EXPECT: ok
INSERT INTO hub.top_ups (id, customer_id, meter_id, provider, provider_ref, provider_txn_id, payer_msisdn, amount_minor, currency,
  plan_tariff_id, tariff_minor_per_kwh, energy_wh, status, expires_at)
VALUES ('c1c1c1c1-0000-7000-8000-000000000001', '44444444-0000-7000-8000-000000000001', '55555555-0000-7000-8000-000000000001', 'mtn_stub',
  'ref-0001', 'txn-0001', '+243970000123', 1000, 'USD', 'a1a1a1a1-0000-7000-8000-000000000001', 35, 28571, 'pending', now() + interval '1 hour');
-- energy is amount * 1000 / tariff rounded down: 1000 * 1000 / 35 = 28571.4 -> 28571, so 28572 is rejected
-- EXPECT: error
INSERT INTO hub.top_ups (id, customer_id, meter_id, provider, amount_minor, currency, plan_tariff_id, tariff_minor_per_kwh, energy_wh, expires_at)
VALUES (hub.uuid_v7(), '44444444-0000-7000-8000-000000000001', '55555555-0000-7000-8000-000000000001', 'mtn_stub', 1000, 'USD',
  'a1a1a1a1-0000-7000-8000-000000000001', 35, 28572, now() + interval '1 hour');
-- the meter must have belonged to the customer (composite foreign key to the assignment history)
-- EXPECT: error
INSERT INTO hub.top_ups (id, customer_id, meter_id, provider, amount_minor, currency, plan_tariff_id, tariff_minor_per_kwh, energy_wh, expires_at)
VALUES (hub.uuid_v7(), '44444444-0000-7000-8000-000000000002', '55555555-0000-7000-8000-000000000001', 'mtn_stub', 1000, 'USD',
  'a1a1a1a1-0000-7000-8000-000000000001', 35, 28571, now() + interval '1 hour');
-- EXPECT: error
INSERT INTO hub.top_ups (id, customer_id, meter_id, provider, amount_minor, currency, plan_tariff_id, tariff_minor_per_kwh, energy_wh, status, expires_at)
VALUES (hub.uuid_v7(), '44444444-0000-7000-8000-000000000001', '55555555-0000-7000-8000-000000000001', 'mtn_stub', 1000, 'USD',
  'a1a1a1a1-0000-7000-8000-000000000001', 35, 28571, 'settled', now() + interval '1 hour');
-- EXPECT: error
INSERT INTO hub.top_ups (id, customer_id, meter_id, provider, provider_ref, amount_minor, currency, plan_tariff_id, tariff_minor_per_kwh, energy_wh, expires_at)
VALUES (hub.uuid_v7(), '44444444-0000-7000-8000-000000000001', '55555555-0000-7000-8000-000000000001', 'mtn_stub', 'ref-0001', 1000, 'USD',
  'a1a1a1a1-0000-7000-8000-000000000001', 35, 28571, now() + interval '1 hour');
-- EXPECT: error
INSERT INTO hub.top_ups (id, customer_id, meter_id, provider, amount_minor, currency, plan_tariff_id, tariff_minor_per_kwh, energy_wh, status, expires_at)
VALUES (hub.uuid_v7(), '44444444-0000-7000-8000-000000000001', '55555555-0000-7000-8000-000000000001', 'mtn_stub', 1000, 'USD',
  'a1a1a1a1-0000-7000-8000-000000000001', 35, 28571, 'confirmed', now() + interval '1 hour');
-- the quoted snapshot never changes
-- EXPECT: error
UPDATE hub.top_ups SET amount_minor = 2000, energy_wh = 57142 WHERE id = 'c1c1c1c1-0000-7000-8000-000000000001';
-- late is only meaningful on a confirmed or reversed top-up
-- EXPECT: error
UPDATE hub.top_ups SET late = true WHERE id = 'c1c1c1c1-0000-7000-8000-000000000001';
-- EXPECT: ok
INSERT INTO hub.top_ups (id, customer_id, meter_id, provider, provider_ref, provider_txn_id, amount_minor, currency,
  plan_tariff_id, tariff_minor_per_kwh, energy_wh, status, expires_at) VALUES
  ('c1c1c1c1-0000-7000-8000-000000000004', '44444444-0000-7000-8000-000000000001', '55555555-0000-7000-8000-000000000001', 'mtn_stub',
   'ref-0004', 'txn-0004', 300, 'USD', 'a1a1a1a1-0000-7000-8000-000000000001', 35, 8571, 'expired', now() - interval '1 hour'),
  ('c1c1c1c1-0000-7000-8000-000000000005', '44444444-0000-7000-8000-000000000001', '55555555-0000-7000-8000-000000000001', 'mtn_stub',
   'ref-0005', NULL, 300, 'USD', 'a1a1a1a1-0000-7000-8000-000000000001', 35, 8571, 'failed', now() - interval '1 hour'),
  ('c1c1c1c1-0000-7000-8000-000000000003', '44444444-0000-7000-8000-000000000001', '55555555-0000-7000-8000-000000000001', 'mtn_stub',
   'ref-0003', NULL, 100, 'USD', 'a1a1a1a1-0000-7000-8000-000000000001', 35, 2857, 'pending', now() + interval '1 hour');
-- a confirmation after expiry must say so (late = true): without the flag it is rejected
-- EXPECT: error
UPDATE hub.top_ups SET status = 'confirmed' WHERE id = 'c1c1c1c1-0000-7000-8000-000000000004';
-- failed is final
-- EXPECT: error
UPDATE hub.top_ups SET status = 'confirmed' WHERE id = 'c1c1c1c1-0000-7000-8000-000000000005';
-- EXPECT: ok
UPDATE hub.top_ups SET status = 'pending_unknown' WHERE id = 'c1c1c1c1-0000-7000-8000-000000000003';
-- a staff session can never confirm a payment: only the payment path may
-- EXPECT: ok
DO $$ BEGIN
  SET LOCAL ROLE hub_api;
  PERFORM set_config('app.user_id', '33333333-0000-7000-8000-000000000002', true);
  UPDATE hub.top_ups SET status = 'confirmed' WHERE id = 'c1c1c1c1-0000-7000-8000-000000000001';
  RESET ROLE;
  RAISE EXCEPTION 'NOT_RAISED';
EXCEPTION WHEN SQLSTATE '23001' THEN RESET ROLE;
END $$;
-- EXPECT: ok
DO $$ BEGIN
  SET LOCAL ROLE hub_api;
  PERFORM set_config('app.user_id', '33333333-0000-7000-8000-000000000002', true);
  INSERT INTO hub.top_ups (id, customer_id, meter_id, provider, amount_minor, currency, plan_tariff_id, tariff_minor_per_kwh, energy_wh, status, expires_at)
  VALUES (hub.uuid_v7(), '44444444-0000-7000-8000-000000000001', '55555555-0000-7000-8000-000000000001', 'mtn_stub', 1000, 'USD',
          'a1a1a1a1-0000-7000-8000-000000000001', 35, 28571, 'confirmed', now() + interval '1 hour');
  RESET ROLE;
  RAISE EXCEPTION 'NOT_RAISED';
EXCEPTION WHEN SQLSTATE '23001' THEN RESET ROLE;
END $$;

-- ===== Posting through post_ledger_transaction: evidence, approvals, replay (D-22, S-2, S-3) =====
-- no stored provider event is linked to the top-up yet
-- EXPECT: ok
DO $$ DECLARE m text; BEGIN
  PERFORM hub.post_ledger_transaction('topup:c1c1c1c1-0000-7000-8000-000000000001:confirm', 'topup_received', NULL, 'test',
    NULL, 'c1c1c1c1-0000-7000-8000-000000000001',
    jsonb_build_array(jsonb_build_object('account_id', '66666666-0000-7000-8000-000000000002', 'currency', 'USD', 'amount_minor', 1000),
                      jsonb_build_object('account_id', '66666666-0000-7000-8000-000000000001', 'currency', 'USD', 'amount_minor', -1000)));
  RAISE EXCEPTION 'NOT_RAISED';
EXCEPTION WHEN SQLSTATE '23001' THEN
  GET STACKED DIAGNOSTICS m = MESSAGE_TEXT;
  IF m NOT LIKE 'evidence_required%' THEN RAISE EXCEPTION 'wrong error: %', m; END IF;
END $$;
-- EXPECT: ok
UPDATE hub.top_ups SET provider_event_id = (SELECT id FROM hub.provider_events WHERE provider = 'mtn_stub' AND event_id = 'evt-0001')
WHERE id = 'c1c1c1c1-0000-7000-8000-000000000001';
-- the application role posts, and replaying the same posting key has no second effect
-- EXPECT: ok
DO $$ DECLARE a uuid; b uuid; n integer; lines jsonb := jsonb_build_array(
    jsonb_build_object('account_id', '66666666-0000-7000-8000-000000000002', 'currency', 'USD', 'amount_minor', 1000),
    jsonb_build_object('account_id', '66666666-0000-7000-8000-000000000001', 'currency', 'USD', 'amount_minor', -1000));
BEGIN
  SET LOCAL ROLE hub_ingest;
  a := hub.post_ledger_transaction('topup:c1c1c1c1-0000-7000-8000-000000000001:confirm', 'topup_received', NULL, 'webhook', NULL,
         'c1c1c1c1-0000-7000-8000-000000000001', lines);
  b := hub.post_ledger_transaction('topup:c1c1c1c1-0000-7000-8000-000000000001:confirm', 'topup_received', NULL, 'webhook', NULL,
         'c1c1c1c1-0000-7000-8000-000000000001', lines);
  RESET ROLE;
  SELECT count(*) INTO n FROM hub.ledger_transactions WHERE posting_key = 'topup:c1c1c1c1-0000-7000-8000-000000000001:confirm';
  IF a <> b OR n <> 1 THEN RAISE EXCEPTION 'replay changed the ledger: % % %', a, b, n; END IF;
END $$;
-- wallet: 600 (direct tests) + 1000 = 1600
-- EXPECT: rows=1
SELECT 1 FROM hub.wallet_balances WHERE account_id = '66666666-0000-7000-8000-000000000001' AND balance_minor = 1600;
-- EXPECT: ok
UPDATE hub.top_ups SET status = 'confirmed', ledger_transaction_id = (SELECT id FROM hub.ledger_transactions WHERE posting_key = 'topup:c1c1c1c1-0000-7000-8000-000000000001:confirm'),
  confirmed_at = now() WHERE id = 'c1c1c1c1-0000-7000-8000-000000000001';
-- one ledger posting per top-up: a second top-up cannot claim the same transaction
-- EXPECT: error
UPDATE hub.top_ups SET status = 'confirmed', ledger_transaction_id = (SELECT id FROM hub.ledger_transactions WHERE posting_key = 'topup:c1c1c1c1-0000-7000-8000-000000000001:confirm'),
  confirmed_at = now() WHERE id = 'c1c1c1c1-0000-7000-8000-000000000003';
-- a wallet posting for a top-up must credit exactly its amount
-- EXPECT: ok
DO $$ DECLARE m text; BEGIN
  PERFORM hub.post_ledger_transaction('topup:wrong-amount', 'topup_received', NULL, 'test', NULL, 'c1c1c1c1-0000-7000-8000-000000000001',
    jsonb_build_array(jsonb_build_object('account_id', '66666666-0000-7000-8000-000000000002', 'currency', 'USD', 'amount_minor', 5000),
                      jsonb_build_object('account_id', '66666666-0000-7000-8000-000000000001', 'currency', 'USD', 'amount_minor', -5000)));
  RAISE EXCEPTION 'NOT_RAISED';
EXCEPTION WHEN SQLSTATE '23001' THEN NULL;
END $$;
-- a staff role cannot post a manual adjustment without an approval
-- EXPECT: ok
DO $$ DECLARE m text; BEGIN
  SET LOCAL ROLE hub_api;
  PERFORM hub.post_ledger_transaction('adj:no-approval', 'adjustment', NULL, 'test', NULL, NULL,
    jsonb_build_array(jsonb_build_object('account_id', '66666666-0000-7000-8000-000000000002', 'currency', 'USD', 'amount_minor', 25),
                      jsonb_build_object('account_id', '66666666-0000-7000-8000-000000000003', 'currency', 'USD', 'amount_minor', -25)));
  RESET ROLE;
  RAISE EXCEPTION 'NOT_RAISED';
EXCEPTION WHEN SQLSTATE '23001' THEN
  RESET ROLE;
  GET STACKED DIAGNOSTICS m = MESSAGE_TEXT;
  IF m NOT LIKE 'approval_required%' THEN RAISE EXCEPTION 'wrong error: %', m; END IF;
END $$;
-- with an approval bound to the exact lines it posts once; the approval is then spent
-- EXPECT: ok
DO $$ DECLARE v_lines jsonb := jsonb_build_array(
    jsonb_build_object('account_id', '66666666-0000-7000-8000-000000000002', 'currency', 'USD', 'amount_minor', 25),
    jsonb_build_object('account_id', '66666666-0000-7000-8000-000000000003', 'currency', 'USD', 'amount_minor', -25));
  v_id uuid := 'dddddddd-0000-7000-8000-0000000000a1'; n integer; m text;
BEGIN
  INSERT INTO hub.approvals (id, action, maker_user_id, maker_session_id, payload, status, approver_user_id, approver_session_id, decided_at, expires_at)
  VALUES (v_id, 'ledger.adjustment', '33333333-0000-7000-8000-000000000001', hub.uuid_v7(), v_lines, 'approved',
          '33333333-0000-7000-8000-000000000002', hub.uuid_v7(), now(), now() + interval '1 hour');
  SET LOCAL ROLE hub_api;
  PERFORM hub.post_ledger_transaction('adj:approved:1', 'adjustment', NULL, 'goodwill', v_id, NULL, v_lines);
  RESET ROLE;
  SELECT count(*) INTO n FROM hub.approvals WHERE id = v_id AND status = 'consumed';
  IF n <> 1 THEN RAISE EXCEPTION 'approval was not consumed'; END IF;
  BEGIN
    SET LOCAL ROLE hub_api;
    PERFORM hub.post_ledger_transaction('adj:approved:2', 'adjustment', NULL, 'again', v_id, NULL, v_lines);
    RESET ROLE;
    RAISE EXCEPTION 'NOT_RAISED';
  EXCEPTION WHEN SQLSTATE '23001' THEN RESET ROLE;
  END;
END $$;
-- an approval bound to different lines is refused
-- EXPECT: ok
DO $$ DECLARE v_id uuid := 'dddddddd-0000-7000-8000-0000000000a2'; BEGIN
  INSERT INTO hub.approvals (id, action, maker_user_id, maker_session_id, payload, status, approver_user_id, approver_session_id, decided_at, expires_at)
  VALUES (v_id, 'ledger.adjustment', '33333333-0000-7000-8000-000000000001', hub.uuid_v7(), '[]'::jsonb, 'approved',
          '33333333-0000-7000-8000-000000000002', hub.uuid_v7(), now(), now() + interval '1 hour');
  PERFORM hub.post_ledger_transaction('adj:wrong-hash', 'adjustment', NULL, 'x', v_id, NULL,
    jsonb_build_array(jsonb_build_object('account_id', '66666666-0000-7000-8000-000000000002', 'currency', 'USD', 'amount_minor', 25),
                      jsonb_build_object('account_id', '66666666-0000-7000-8000-000000000003', 'currency', 'USD', 'amount_minor', -25)));
  RAISE EXCEPTION 'NOT_RAISED';
EXCEPTION WHEN SQLSTATE '23001' THEN NULL;
END $$;
-- application roles have no write path to the ledger tables or wallet balances
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_api; UPDATE hub.wallet_balances SET balance_minor = 1000000; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_api; INSERT INTO hub.ledger_accounts (id, code, kind, currency) VALUES (hub.uuid_v7(), 'x', 'suspense', 'USD'); END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_ingest; INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency, amount_minor) VALUES (hub.uuid_v7(), hub.uuid_v7(), hub.uuid_v7(), 'USD', 1); END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_worker; UPDATE hub.wallet_balances SET balance_minor = 1; END $$;
-- manual kinds always need approval, even for a superuser inserting directly
-- EXPECT: error
INSERT INTO hub.ledger_transactions (id, posting_key, kind) VALUES (hub.uuid_v7(), 'test:manual-without-approval', 'adjustment');

-- ===== Late confirmation is credited once and flagged (D-05) =====
-- EXPECT: ok
INSERT INTO hub.provider_events (id, provider, event_id, provider_txn_id, event_type, amount_minor, currency, payload)
VALUES (hub.uuid_v7(), 'mtn_stub', 'evt-0004', 'txn-0004', 'payment.succeeded', 300, 'USD', '{"payer_msisdn":"+243970000123"}');
-- EXPECT: ok
UPDATE hub.top_ups SET provider_event_id = (SELECT id FROM hub.provider_events WHERE provider = 'mtn_stub' AND event_id = 'evt-0004')
WHERE id = 'c1c1c1c1-0000-7000-8000-000000000004';
-- EXPECT: ok
DO $$ DECLARE v_tx uuid; BEGIN
  v_tx := hub.post_ledger_transaction('topup:c1c1c1c1-0000-7000-8000-000000000004:confirm', 'topup_received', NULL, 'late webhook', NULL,
    'c1c1c1c1-0000-7000-8000-000000000004',
    jsonb_build_array(jsonb_build_object('account_id', '66666666-0000-7000-8000-000000000002', 'currency', 'USD', 'amount_minor', 300),
                      jsonb_build_object('account_id', '66666666-0000-7000-8000-000000000001', 'currency', 'USD', 'amount_minor', -300)));
  UPDATE hub.top_ups SET status = 'confirmed', late = true, ledger_transaction_id = v_tx, confirmed_at = now()
   WHERE id = 'c1c1c1c1-0000-7000-8000-000000000004';
END $$;
-- EXPECT: rows=1
SELECT 1 FROM hub.top_ups WHERE id = 'c1c1c1c1-0000-7000-8000-000000000004' AND status = 'confirmed' AND late;
-- wallet: 1600 + 300 (late credit) + 25-adjustment does not touch the wallet = 1900
-- EXPECT: rows=1
SELECT 1 FROM hub.wallet_balances WHERE account_id = '66666666-0000-7000-8000-000000000001' AND balance_minor = 1900;

-- ===== Provider reversal, chargeback_receivable (D-06) =====
-- full cover: the wallet still holds the money, so the reversal debits it, no shortfall
-- EXPECT: ok
INSERT INTO hub.provider_events (id, provider, event_id, provider_txn_id, event_type, amount_minor, currency, payload)
VALUES (hub.uuid_v7(), 'mtn_stub', 'evt-rev-1', 'txn-rev-1', 'payment.reversed', 1000, 'USD', '{}');
-- EXPECT: ok
DO $$ DECLARE r record; BEGIN
  SELECT * INTO r FROM hub.reverse_ledger_transaction(
    (SELECT id FROM hub.ledger_transactions WHERE posting_key = 'topup:c1c1c1c1-0000-7000-8000-000000000001:confirm'),
    'reversal:c1c1c1c1-0000-7000-8000-000000000001', (SELECT id FROM hub.provider_events WHERE event_id = 'evt-rev-1'), NULL);
  IF r.shortfall_minor <> 0 THEN RAISE EXCEPTION 'unexpected shortfall %', r.shortfall_minor; END IF;
END $$;
-- EXPECT: rows=1
SELECT 1 FROM hub.wallet_balances WHERE account_id = '66666666-0000-7000-8000-000000000001' AND balance_minor = 900;
-- a transaction is reversed at most once
-- EXPECT: error
SELECT * FROM hub.reverse_ledger_transaction(
    (SELECT id FROM hub.ledger_transactions WHERE posting_key = 'topup:c1c1c1c1-0000-7000-8000-000000000001:confirm'),
    'reversal:second-attempt', (SELECT id FROM hub.provider_events WHERE event_id = 'evt-rev-1'), NULL);
-- a reversal needs evidence
-- EXPECT: error
SELECT * FROM hub.reverse_ledger_transaction(
    (SELECT id FROM hub.ledger_transactions WHERE posting_key = 'topup:c1c1c1c1-0000-7000-8000-000000000004:confirm'), 'reversal:no-evidence', NULL, NULL);
-- shortfall: customer 2 buys energy with part of the money, then the provider reverses the whole payment
-- EXPECT: ok
INSERT INTO hub.provider_events (id, provider, event_id, provider_txn_id, event_type, amount_minor, currency, payload) VALUES
  (hub.uuid_v7(), 'mtn_stub', 'evt-0002', 'txn-0002', 'payment.succeeded', 700, 'USD', '{"payer_msisdn":"+243970000456"}'),
  (hub.uuid_v7(), 'mtn_stub', 'evt-rev-2', 'txn-rev-2', 'payment.reversed', 700, 'USD', '{}');
-- EXPECT: ok
INSERT INTO hub.top_ups (id, customer_id, meter_id, provider, provider_ref, provider_txn_id, amount_minor, currency, plan_tariff_id,
  tariff_minor_per_kwh, energy_wh, status, provider_event_id, expires_at)
VALUES ('c1c1c1c1-0000-7000-8000-000000000002', '44444444-0000-7000-8000-000000000002', '55555555-0000-7000-8000-000000000002', 'mtn_stub', 'ref-0002', 'txn-0002',
  700, 'USD', 'a1a1a1a1-0000-7000-8000-000000000001', 35, 20000, 'pending',
  (SELECT id FROM hub.provider_events WHERE event_id = 'evt-0002'), now() + interval '1 hour');
-- EXPECT: ok
DO $$ DECLARE v_tx uuid; r record; BEGIN
  v_tx := hub.post_ledger_transaction('topup:c1c1c1c1-0000-7000-8000-000000000002:confirm', 'topup_received', NULL, 'webhook', NULL,
    'c1c1c1c1-0000-7000-8000-000000000002',
    jsonb_build_array(jsonb_build_object('account_id', '66666666-0000-7000-8000-000000000002', 'currency', 'USD', 'amount_minor', 700),
                      jsonb_build_object('account_id', '66666666-0000-7000-8000-000000000006', 'currency', 'USD', 'amount_minor', -700)));
  UPDATE hub.top_ups SET status = 'confirmed', ledger_transaction_id = v_tx, confirmed_at = now() WHERE id = 'c1c1c1c1-0000-7000-8000-000000000002';
  PERFORM hub.post_ledger_transaction('energy:c1c1c1c1-0000-7000-8000-000000000002', 'energy_purchase', NULL, 'token issuance', NULL,
    'c1c1c1c1-0000-7000-8000-000000000002',
    jsonb_build_array(jsonb_build_object('account_id', '66666666-0000-7000-8000-000000000006', 'currency', 'USD', 'amount_minor', 500),
                      jsonb_build_object('account_id', '66666666-0000-7000-8000-000000000003', 'currency', 'USD', 'amount_minor', -500)));
  SELECT * INTO r FROM hub.reverse_ledger_transaction(v_tx, 'reversal:c1c1c1c1-0000-7000-8000-000000000002',
    (SELECT id FROM hub.provider_events WHERE event_id = 'evt-rev-2'), NULL);
  IF r.shortfall_minor <> 500 THEN RAISE EXCEPTION 'expected a 500 shortfall, got %', r.shortfall_minor; END IF;
END $$;
-- the wallet is empty, the shortfall sits in chargeback_receivable and an exception event was queued
-- EXPECT: rows=1
SELECT 1 FROM hub.wallet_balances WHERE account_id = '66666666-0000-7000-8000-000000000006' AND balance_minor = 0;
-- EXPECT: rows=1
SELECT 1 FROM hub.ledger_entries WHERE account_id = '66666666-0000-7000-8000-000000000005' AND amount_minor = 500;
-- EXPECT: rows=1
SELECT 1 FROM hub.outbox WHERE event_type = 'ledger.chargeback_opened' AND (payload->>'shortfall_minor')::bigint = 500;
-- EXPECT: rows=0
SELECT * FROM hub.v_wallet_balance_drift;
-- EXPECT: ok
SELECT hub.snapshot_ledger_balances(current_date);
-- EXPECT: rows=1
SELECT 1 FROM hub.ledger_balance_snapshots WHERE account_id = '66666666-0000-7000-8000-000000000005' AND balance_minor = 500;

-- ===== Maker-checker approvals (D-15) =====
-- EXPECT: error
INSERT INTO hub.approvals (id, action, maker_user_id, maker_session_id, payload, status, approver_user_id, approver_session_id, decided_at, expires_at)
VALUES (hub.uuid_v7(), 'role.grant', '33333333-0000-7000-8000-000000000001', hub.uuid_v7(), '{"role":"x"}', 'approved',
        '33333333-0000-7000-8000-000000000001', hub.uuid_v7(), now(), now() + interval '1 hour');
-- EXPECT: error
INSERT INTO hub.approvals (id, action, maker_user_id, maker_session_id, payload, status, approver_user_id, approver_session_id, decided_at, expires_at)
VALUES (hub.uuid_v7(), 'role.grant', '33333333-0000-7000-8000-000000000001', '77777777-0000-7000-8000-0000000000c3', '{"role":"x"}', 'approved',
        '33333333-0000-7000-8000-000000000002', '77777777-0000-7000-8000-0000000000c3', now(), now() + interval '1 hour');
-- EXPECT: error
INSERT INTO hub.approvals (id, action, maker_user_id, maker_session_id, payload, status, expires_at)
VALUES (hub.uuid_v7(), 'refund.everything', '33333333-0000-7000-8000-000000000001', hub.uuid_v7(), '{}', 'pending', now() + interval '1 hour');
-- EXPECT: error
UPDATE hub.approvals SET payload = '{"tampered":true}' WHERE id = '88888888-0000-7000-8000-000000000001';
-- EXPECT: rows=1
SELECT 1 FROM hub.approvals WHERE id = '88888888-0000-7000-8000-000000000001' AND payload_hash = encode(sha256(convert_to(payload::text, 'UTF8')), 'hex');
-- a conflict of interest blocks approval of money for a customer linked to the approver
-- EXPECT: ok
INSERT INTO hub.staff_customer_link (user_id, customer_id, relation) VALUES ('33333333-0000-7000-8000-000000000002', '44444444-0000-7000-8000-000000000001', 'relative');
-- EXPECT: error
INSERT INTO hub.approvals (id, action, subject_customer_id, maker_user_id, maker_session_id, payload, status, approver_user_id, approver_session_id, decided_at, expires_at)
VALUES (hub.uuid_v7(), 'ledger.refund', '44444444-0000-7000-8000-000000000001', '33333333-0000-7000-8000-000000000001', hub.uuid_v7(), '{"x":1}', 'approved',
        '33333333-0000-7000-8000-000000000002', hub.uuid_v7(), now(), now() + interval '1 hour');
-- EXPECT: ok
DELETE FROM hub.staff_customer_link WHERE user_id = '33333333-0000-7000-8000-000000000002';
-- tariff changes are bounded to 25 percent per revision
-- EXPECT: error
INSERT INTO hub.plan_tariffs (id, plan_id, tariff_minor_per_kwh, effective_from, status) VALUES
  (hub.uuid_v7(), '22222222-0000-7000-8000-000000000001', 70, now(), 'proposed');
-- EXPECT: ok
INSERT INTO hub.plan_tariffs (id, plan_id, tariff_minor_per_kwh, effective_from, status) VALUES
  (hub.uuid_v7(), '22222222-0000-7000-8000-000000000001', 40, now(), 'proposed');
-- a tariff that is not proposed needs an approval
-- EXPECT: error
INSERT INTO hub.plan_tariffs (id, plan_id, tariff_minor_per_kwh, effective_from, status) VALUES
  (hub.uuid_v7(), '22222222-0000-7000-8000-000000000001', 36, now() + interval '1 day', 'active');
-- EXPECT: rows=1
SELECT 1 FROM hub.current_tariff('22222222-0000-7000-8000-000000000001') WHERE tariff_minor_per_kwh = 35;

-- ===== Tokens: ledger reference, monotonic counter, eligibility, state machine (D-07, D-08) =====
-- EXPECT: error
INSERT INTO hub.meter_tokens (id, meter_id, customer_id, ledger_transaction_id, energy_wh, counter, expires_at)
VALUES (hub.uuid_v7(), '55555555-0000-7000-8000-000000000001', '44444444-0000-7000-8000-000000000001', NULL, 28571, 1, now() + interval '3 days');
-- the counter must be the meter's next counter (the issuer increments it first)
-- EXPECT: error
INSERT INTO hub.meter_tokens (id, meter_id, customer_id, ledger_transaction_id, energy_wh, counter, expires_at)
VALUES (hub.uuid_v7(), '55555555-0000-7000-8000-000000000001', '44444444-0000-7000-8000-000000000001',
        (SELECT id FROM hub.ledger_transactions WHERE posting_key = 'test:purchase:1'), 28571, 1, now() + interval '3 days');
-- EXPECT: ok
UPDATE hub.meters SET token_counter = token_counter + 1 WHERE id = '55555555-0000-7000-8000-000000000001';
-- EXPECT: ok
INSERT INTO hub.meter_tokens (id, meter_id, customer_id, top_up_id, ledger_transaction_id, energy_wh, counter, expires_at)
VALUES ('e1e1e1e1-0000-7000-8000-000000000001', '55555555-0000-7000-8000-000000000001', '44444444-0000-7000-8000-000000000001',
        'c1c1c1c1-0000-7000-8000-000000000001', (SELECT id FROM hub.ledger_transactions WHERE posting_key = 'test:purchase:1'), 28571, 1, now() + interval '3 days');
-- a counter is never reused
-- EXPECT: error
INSERT INTO hub.meter_tokens (id, meter_id, customer_id, ledger_transaction_id, energy_wh, counter, expires_at)
VALUES (hub.uuid_v7(), '55555555-0000-7000-8000-000000000001', '44444444-0000-7000-8000-000000000001',
        (SELECT id FROM hub.ledger_transactions WHERE posting_key = 'test:purchase:1'), 100, 1, now() + interval '3 days');
-- one token per top-up
-- EXPECT: ok
UPDATE hub.meters SET token_counter = token_counter + 1 WHERE id = '55555555-0000-7000-8000-000000000001';
-- EXPECT: error
INSERT INTO hub.meter_tokens (id, meter_id, customer_id, top_up_id, ledger_transaction_id, energy_wh, counter, expires_at)
VALUES (hub.uuid_v7(), '55555555-0000-7000-8000-000000000001', '44444444-0000-7000-8000-000000000001', 'c1c1c1c1-0000-7000-8000-000000000001',
        (SELECT id FROM hub.ledger_transactions WHERE posting_key = 'test:purchase:1'), 28571, 2, now() + interval '3 days');
-- the token's customer must be one the meter belonged to
-- EXPECT: error
INSERT INTO hub.meter_tokens (id, meter_id, customer_id, ledger_transaction_id, energy_wh, counter, expires_at)
VALUES (hub.uuid_v7(), '55555555-0000-7000-8000-000000000001', '44444444-0000-7000-8000-000000000002',
        (SELECT id FROM hub.ledger_transactions WHERE posting_key = 'test:purchase:1'), 100, 2, now() + interval '3 days');
-- an inactive meter gets no token
-- EXPECT: ok
UPDATE hub.meters SET status = 'offline' WHERE id = '55555555-0000-7000-8000-000000000001';
-- EXPECT: error
INSERT INTO hub.meter_tokens (id, meter_id, customer_id, ledger_transaction_id, energy_wh, counter, expires_at)
VALUES (hub.uuid_v7(), '55555555-0000-7000-8000-000000000001', '44444444-0000-7000-8000-000000000001',
        (SELECT id FROM hub.ledger_transactions WHERE posting_key = 'test:purchase:1'), 100, 2, now() + interval '3 days');
-- EXPECT: ok
UPDATE hub.meters SET status = 'active' WHERE id = '55555555-0000-7000-8000-000000000001';
-- EXPECT: error
INSERT INTO hub.meter_tokens (id, meter_id, customer_id, ledger_transaction_id, energy_wh, counter, expires_at, status)
VALUES (hub.uuid_v7(), '55555555-0000-7000-8000-000000000001', '44444444-0000-7000-8000-000000000001',
        (SELECT id FROM hub.ledger_transactions WHERE posting_key = 'test:purchase:1'), 100, 2, now() + interval '3 days', 'queued');
-- EXPECT: error
UPDATE hub.meter_tokens SET status = 'acknowledged', acknowledged_at = now() WHERE id = 'e1e1e1e1-0000-7000-8000-000000000001';
-- EXPECT: ok
UPDATE hub.meter_tokens SET status = 'sent', sent_at = now(), attempts = 1 WHERE id = 'e1e1e1e1-0000-7000-8000-000000000001';
-- a resend reuses the same token id: sent goes back to pending
-- EXPECT: ok
UPDATE hub.meter_tokens SET status = 'pending', attempts = 2, next_attempt_at = now() + interval '1 minute' WHERE id = 'e1e1e1e1-0000-7000-8000-000000000001';
-- EXPECT: error
UPDATE hub.meter_tokens SET counter = 99 WHERE id = 'e1e1e1e1-0000-7000-8000-000000000001';
-- EXPECT: ok
UPDATE hub.meter_tokens SET status = 'sent' WHERE id = 'e1e1e1e1-0000-7000-8000-000000000001';
-- EXPECT: ok
UPDATE hub.meter_tokens SET status = 'unknown' WHERE id = 'e1e1e1e1-0000-7000-8000-000000000001';
-- EXPECT: ok
UPDATE hub.meter_tokens SET status = 'acknowledged', acknowledged_at = now() WHERE id = 'e1e1e1e1-0000-7000-8000-000000000001';
-- EXPECT: error
UPDATE hub.meter_tokens SET status = 'pending', acknowledged_at = NULL WHERE id = 'e1e1e1e1-0000-7000-8000-000000000001';
-- a meter can change hands: history stays, the old owner gets no new token
-- EXPECT: ok
UPDATE hub.meters SET customer_id = '44444444-0000-7000-8000-000000000002' WHERE id = '55555555-0000-7000-8000-000000000001';
-- EXPECT: rows=2
SELECT 1 FROM hub.meter_assignments WHERE meter_id = '55555555-0000-7000-8000-000000000001';
-- EXPECT: ok
UPDATE hub.meters SET token_counter = token_counter + 1 WHERE id = '55555555-0000-7000-8000-000000000001';
-- EXPECT: error
INSERT INTO hub.meter_tokens (id, meter_id, customer_id, ledger_transaction_id, energy_wh, counter, expires_at)
VALUES (hub.uuid_v7(), '55555555-0000-7000-8000-000000000001', '44444444-0000-7000-8000-000000000001',
        (SELECT id FROM hub.ledger_transactions WHERE posting_key = 'test:purchase:1'), 100, 3, now() + interval '3 days');
-- EXPECT: ok
UPDATE hub.meters SET customer_id = '44444444-0000-7000-8000-000000000001' WHERE id = '55555555-0000-7000-8000-000000000001';

-- ===== Telemetry: duplicates, windows, partitions (D-25, S-12) =====
-- EXPECT: ok
INSERT INTO hub.meter_readings (meter_id, read_at, cum_energy_wh, power_w)
VALUES ('55555555-0000-7000-8000-000000000001', (date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') + interval '15 minutes', 100, 50);
-- EXPECT: error
INSERT INTO hub.meter_readings (meter_id, read_at, cum_energy_wh, power_w)
VALUES ('55555555-0000-7000-8000-000000000001', (date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') + interval '15 minutes', 100, 50);
-- first write wins: the duplicate is ignored and the original value stays
-- EXPECT: rows=0
INSERT INTO hub.meter_readings (meter_id, read_at, cum_energy_wh, power_w)
VALUES ('55555555-0000-7000-8000-000000000001', (date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') + interval '15 minutes', 999, 70)
ON CONFLICT (meter_id, read_at) DO NOTHING RETURNING meter_id;
-- EXPECT: rows=1
SELECT 1 FROM hub.meter_readings
WHERE meter_id = '55555555-0000-7000-8000-000000000001'
  AND read_at = (date_trunc('month', now() AT TIME ZONE 'UTC') AT TIME ZONE 'UTC') + interval '15 minutes'
  AND cum_energy_wh = 100;
-- EXPECT: rows=1
SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'hub' AND c.relkind = 'r'
  AND c.relname = 'meter_readings_' || to_char(now() AT TIME ZONE 'UTC', 'YYYY_MM');
-- EXPECT: ok
INSERT INTO hub.meter_readings (meter_id, read_at, cum_energy_wh, power_w)
VALUES ('55555555-0000-7000-8000-000000000001', now(), 120, 55);
-- EXPECT: rows=1
SELECT 1 FROM hub.meter_readings
WHERE meter_id = '55555555-0000-7000-8000-000000000001'
  AND tableoid = ('hub.meter_readings_' || to_char(now() AT TIME ZONE 'UTC', 'YYYY_MM'))::regclass
  AND cum_energy_wh = 120;
-- a reading 40 days old (a late batch within the hard floor) is accepted and lands in a created partition, not in the default one
-- EXPECT: ok
INSERT INTO hub.meter_readings (meter_id, read_at, cum_energy_wh) VALUES ('55555555-0000-7000-8000-000000000001', now() - interval '40 days', 90);
-- EXPECT: rows=0
SELECT 1 FROM hub.meter_readings_default;
-- hard floor: not older than 45 days
-- EXPECT: error
INSERT INTO hub.meter_readings (meter_id, read_at, cum_energy_wh) VALUES ('55555555-0000-7000-8000-000000000001', now() - interval '46 days', 80);
-- not more than 5 minutes ahead of the receive time
-- EXPECT: error
INSERT INTO hub.meter_readings (meter_id, read_at, cum_energy_wh) VALUES ('55555555-0000-7000-8000-000000000001', now() + interval '10 minutes', 1);
-- EXPECT: ok
INSERT INTO hub.meter_readings (meter_id, read_at, cum_energy_wh) VALUES ('55555555-0000-7000-8000-000000000001', now() + interval '2 minutes', 121);
-- a reading with an explicit old received_at passes the window and lands in the default partition (it must stay empty in production)
-- EXPECT: ok
INSERT INTO hub.meter_readings (meter_id, read_at, cum_energy_wh, received_at) VALUES ('55555555-0000-7000-8000-000000000001', now() - interval '3 years', 5, now() - interval '3 years');
-- EXPECT: rows=1
SELECT 1 FROM hub.meter_readings_default WHERE meter_id = '55555555-0000-7000-8000-000000000001';
-- EXPECT: rows=0
SELECT * FROM hub.ensure_meter_reading_partitions(2, 3);
-- the ingest role cannot choose received_at (column privilege), so it cannot widen the window
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_ingest; INSERT INTO hub.meter_readings (meter_id, read_at, cum_energy_wh, received_at) VALUES ('55555555-0000-7000-8000-000000000002', now(), 1, now() + interval '30 days'); END $$;
-- EXPECT: ok
DO $$ BEGIN SET LOCAL ROLE hub_ingest; INSERT INTO hub.meter_readings (meter_id, read_at, cum_energy_wh) VALUES ('55555555-0000-7000-8000-000000000002', now(), 7) ON CONFLICT (meter_id, read_at) DO NOTHING; RESET ROLE; END $$;
-- the worker creates partitions without any DDL privilege because the function is SECURITY DEFINER
-- EXPECT: ok
DO $$ BEGIN SET LOCAL ROLE hub_worker; PERFORM hub.ensure_meter_reading_partitions(2, 4); RESET ROLE; END $$;
-- EXPECT: ok
INSERT INTO hub.gateways (id, site_id, name) VALUES ('f2f2f2f2-0000-7000-8000-000000000001', '11111111-0000-7000-8000-000000000001', 'gw-lub-1');
-- EXPECT: ok
INSERT INTO hub.reading_batches (gateway_id, batch_key, reading_count, payload_sha256) VALUES ('f2f2f2f2-0000-7000-8000-000000000001', 'batch-1', 4, repeat('b', 64));
-- EXPECT: error
INSERT INTO hub.reading_batches (gateway_id, batch_key, reading_count, payload_sha256) VALUES ('f2f2f2f2-0000-7000-8000-000000000001', 'batch-1', 4, repeat('b', 64));
-- EXPECT: error
INSERT INTO hub.reading_batches (gateway_id, batch_key, reading_count, payload_sha256) VALUES ('f2f2f2f2-0000-7000-8000-000000000001', 'batch-big', 1001, repeat('b', 64));

-- ===== API keys (D-19) =====
-- EXPECT: ok
INSERT INTO hub.api_keys (id, kind, name, key_id, key_hash, scopes, bound_site_ids, allowed_cidrs, owner_user_id, expires_at)
VALUES ('f3f3f3f3-0000-7000-8000-000000000001', 'gateway', 'gw-lub-1 key', 'k_gw1', decode(repeat('ab', 32), 'hex'),
        ARRAY['telemetry:write','gateway:heartbeat'], ARRAY['11111111-0000-7000-8000-000000000001']::uuid[], ARRAY['203.0.113.0/24']::cidr[],
        '33333333-0000-7000-8000-000000000006', now() + interval '90 days');
-- a gateway key needs a source address range and a site binding
-- EXPECT: error
INSERT INTO hub.api_keys (id, kind, name, key_id, key_hash, scopes, bound_site_ids, owner_user_id, expires_at)
VALUES (hub.uuid_v7(), 'gateway', 'no cidr', 'k_gw2', decode(repeat('ac', 32), 'hex'), ARRAY['telemetry:write'],
        ARRAY['11111111-0000-7000-8000-000000000001']::uuid[], '33333333-0000-7000-8000-000000000006', now() + interval '90 days');
-- expiry is required and at most 180 days
-- EXPECT: error
INSERT INTO hub.api_keys (id, kind, name, key_id, key_hash, scopes, owner_user_id, expires_at)
VALUES (hub.uuid_v7(), 'integrator', 'too long', 'k_int1', decode(repeat('ad', 32), 'hex'), ARRAY['customers:read'], '33333333-0000-7000-8000-000000000006', now() + interval '181 days');
-- EXPECT: error
INSERT INTO hub.api_keys (id, kind, name, key_id, key_hash, scopes, owner_user_id, expires_at)
VALUES (hub.uuid_v7(), 'integrator', 'no expiry', 'k_int2', decode(repeat('ae', 32), 'hex'), ARRAY['customers:read'], '33333333-0000-7000-8000-000000000006', NULL);
-- scopes come from one list per kind and the lists are disjoint
-- EXPECT: error
INSERT INTO hub.api_keys (id, kind, name, key_id, key_hash, scopes, owner_user_id, expires_at)
VALUES (hub.uuid_v7(), 'integrator', 'gateway scope', 'k_int3', decode(repeat('af', 32), 'hex'), ARRAY['telemetry:write'], '33333333-0000-7000-8000-000000000006', now() + interval '30 days');
-- EXPECT: error
INSERT INTO hub.api_keys (id, kind, name, key_id, key_hash, scopes, owner_user_id, expires_at)
VALUES (hub.uuid_v7(), 'integrator', 'admin scope', 'k_int4', decode(repeat('a1', 32), 'hex'), ARRAY['customers:read','admin:all'], '33333333-0000-7000-8000-000000000006', now() + interval '30 days');
-- EXPECT: error
INSERT INTO hub.api_keys (id, kind, name, key_id, key_hash, scopes, owner_user_id, expires_at)
VALUES (hub.uuid_v7(), 'integrator', 'bad hash', 'k_int5', '\x0102', ARRAY['customers:read'], '33333333-0000-7000-8000-000000000006', now() + interval '30 days');
-- EXPECT: ok
INSERT INTO hub.api_keys (id, kind, name, key_id, key_hash, scopes, owner_user_id, expires_at)
VALUES (hub.uuid_v7(), 'integrator', 'partner', 'k_int6', decode(repeat('a2', 32), 'hex'), ARRAY['customers:read','topups:read'], '33333333-0000-7000-8000-000000000006', now() + interval '30 days');
-- the ingest role authenticates keys through a function: the right hash from the right address passes, anything else returns no row
-- EXPECT: ok
DO $$ DECLARE n integer; BEGIN
  SET LOCAL ROLE hub_ingest;
  SELECT count(*) INTO n FROM hub.verify_api_key('k_gw1', decode(repeat('ab', 32), 'hex'), '203.0.113.50');
  IF n <> 1 THEN RAISE EXCEPTION 'valid key rejected'; END IF;
  SELECT count(*) INTO n FROM hub.verify_api_key('k_gw1', decode(repeat('ff', 32), 'hex'), '203.0.113.50');
  IF n <> 0 THEN RAISE EXCEPTION 'wrong hash accepted'; END IF;
  SELECT count(*) INTO n FROM hub.verify_api_key('k_gw1', decode(repeat('ab', 32), 'hex'), '198.51.100.1');
  IF n <> 0 THEN RAISE EXCEPTION 'wrong address accepted'; END IF;
  RESET ROLE;
END $$;

-- ===== Least privilege per role (D-22, S-2) =====
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_ingest; PERFORM 1 FROM hub.users; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_ingest; PERFORM 1 FROM hub.api_keys; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_ingest; PERFORM 1 FROM hub.ledger_entries; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_ingest; INSERT INTO hub.user_roles (user_id, role_code) VALUES ('33333333-0000-7000-8000-000000000005', 'administrator'); END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_ingest; INSERT INTO hub.api_keys (id, kind, name, key_id, key_hash, scopes, owner_user_id, expires_at)
  VALUES (hub.uuid_v7(), 'integrator', 'x', 'x', decode(repeat('ab', 32), 'hex'), ARRAY['customers:read'], '33333333-0000-7000-8000-000000000006', now() + interval '1 day'); END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_ingest; PERFORM 1 FROM hub.audit_log; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_worker; PERFORM password_hash FROM hub.users; END $$;
-- EXPECT: ok
DO $$ BEGIN SET LOCAL ROLE hub_worker; PERFORM email, display_name FROM hub.users; RESET ROLE; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_api; INSERT INTO hub.provider_events (id, provider, event_id, provider_txn_id, event_type, payload) VALUES (hub.uuid_v7(), 'p', 'e', 't', 'payment.succeeded', '{}'); END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_api; INSERT INTO hub.meter_readings (meter_id, read_at, cum_energy_wh) VALUES (hub.uuid_v7(), now(), 1); END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_api; PERFORM payload FROM hub.provider_events; END $$;
-- the model gateway can only INSERT ai_runs (invariant 1)
-- EXPECT: ok
DO $$ BEGIN SET LOCAL ROLE ai_gateway;
  INSERT INTO hub.ai_runs (id, feature, model, prompt_template, prompt_hash, status) VALUES (hub.uuid_v7(), 'summary', 'claude-opus-5-5', 'summary.v1', repeat('cd', 32), 'ok');
  RESET ROLE; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE ai_gateway; PERFORM 1 FROM hub.customers; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE ai_gateway; UPDATE hub.ai_runs SET output = '{}'; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE ai_gateway; INSERT INTO hub.tickets (id, site_id, channel, subject) VALUES (hub.uuid_v7(), '11111111-0000-7000-8000-000000000001', 'api', 'x'); END $$;
-- none of the application roles is a superuser or bypasses row-level security
-- EXPECT: rows=0
SELECT rolname FROM pg_roles WHERE rolname IN ('hub_api','hub_ingest','hub_worker','ai_gateway','audit_writer','ledger_owner','privacy_owner','migrator',
  'analytics_support','analytics_finance','analytics_ops') AND (rolsuper OR rolbypassrls OR rolcreaterole OR rolcreatedb);
-- the old shared roles do not exist any more
-- EXPECT: rows=0
SELECT rolname FROM pg_roles WHERE rolname IN ('app_rw','app_ro_replica','analytics_ro');
-- definer functions are owned by the right non-superuser role and pin search_path with pg_temp last
-- EXPECT: rows=1
SELECT 1 FROM pg_proc p WHERE p.proname = 'post_ledger_transaction' AND p.prosecdef AND p.proowner = (SELECT oid FROM pg_roles WHERE rolname = 'ledger_owner');
-- EXPECT: rows=1
SELECT 1 FROM pg_proc p WHERE p.proname = 'erase_customer' AND p.prosecdef AND p.proowner = (SELECT oid FROM pg_roles WHERE rolname = 'privacy_owner');
-- EXPECT: rows=1
SELECT 1 FROM pg_proc p WHERE p.proname = 'audit_append' AND p.prosecdef AND p.proowner = (SELECT oid FROM pg_roles WHERE rolname = 'audit_writer');
-- EXPECT: rows=0
SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'hub' AND p.prosecdef AND NOT (coalesce(p.proconfig::text, '') LIKE '%search_path=pg_catalog, hub, pg_temp%');
-- EXPECT: rows=0
SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'hub' AND p.proowner IN (SELECT oid FROM pg_roles WHERE rolsuper);
-- EXPECT: rows=0
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'hub' AND c.relkind IN ('r','p','v') AND c.relowner IN (SELECT oid FROM pg_roles WHERE rolsuper);
-- PUBLIC has no CONNECT or TEMP on the database and no EXECUTE on any hub function (S-7)
-- EXPECT: rows=0
SELECT 1 WHERE has_database_privilege('analytics_ops', current_database(), 'TEMP') OR has_database_privilege('hub_api', current_database(), 'TEMP');
-- EXPECT: rows=1
SELECT 1 WHERE has_database_privilege('hub_api', current_database(), 'CONNECT');
-- EXPECT: rows=0
SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'hub' AND (p.proacl IS NULL OR EXISTS (SELECT 1 FROM aclexplode(p.proacl) a WHERE a.grantee = 0 AND a.privilege_type = 'EXECUTE'));
-- EXPECT: rows=1
SELECT 1 FROM pg_default_acl d WHERE d.defaclrole = (SELECT oid FROM pg_roles WHERE rolname = 'migrator') AND d.defaclobjtype = 'f' AND d.defaclnamespace = 0;
-- RLS is FORCEd on the seven protected tables
-- EXPECT: rows=7
SELECT relname FROM pg_class WHERE relnamespace = 'hub'::regnamespace AND relrowsecurity AND relforcerowsecurity
  AND relname IN ('customers','tickets','ticket_messages','top_ups','meter_tokens','wallet_balances','ledger_accounts');

-- ===== Tickets, triage vocabulary, AI audit, drafts (7.2, D-32, D-35, D-37) =====
-- EXPECT: ok
INSERT INTO hub.tickets (id, site_id, customer_id, channel, subject) VALUES
  ('f1f1f1f1-0000-7000-8000-000000000001', '11111111-0000-7000-8000-000000000001', '44444444-0000-7000-8000-000000000001', 'portal', 'Compteur hors ligne'),
  ('f1f1f1f1-0000-7000-8000-000000000002', '11111111-0000-7000-8000-000000000002', '44444444-0000-7000-8000-000000000002', 'portal', 'Question tarif');
-- EXPECT: error
INSERT INTO hub.tickets (id, site_id, channel, subject, status) VALUES (hub.uuid_v7(), '11111111-0000-7000-8000-000000000001', 'api', 'x', 'new');
-- EXPECT: error
INSERT INTO hub.tickets (id, site_id, channel, subject, category) VALUES (hub.uuid_v7(), '11111111-0000-7000-8000-000000000001', 'api', 'x', 'unknown');
-- EXPECT: error
INSERT INTO hub.tickets (id, site_id, channel, subject, urgency) VALUES (hub.uuid_v7(), '11111111-0000-7000-8000-000000000001', 'api', 'x', 'p1');
-- EXPECT: error
INSERT INTO hub.tickets (id, site_id, channel, subject, language) VALUES (hub.uuid_v7(), '11111111-0000-7000-8000-000000000001', 'api', 'x', 'de');
-- EXPECT: error
INSERT INTO hub.tickets (id, site_id, channel, subject, pii_flags) VALUES (hub.uuid_v7(), '11111111-0000-7000-8000-000000000001', 'api', 'x', ARRAY['passport']);
-- EXPECT: ok
UPDATE hub.tickets SET category = 'no_power', urgency = 'high', language = 'ln', pii_flags = ARRAY['phone','payment_reference'],
  ai_category = 'meter_fault', ai_urgency = 'normal', ai_language = 'ln', triage_overridden_by = '33333333-0000-7000-8000-000000000001', triage_overridden_at = now()
WHERE id = 'f1f1f1f1-0000-7000-8000-000000000001';
-- optimistic concurrency: every update increments the version
-- EXPECT: rows=1
SELECT 1 FROM hub.tickets WHERE id = 'f1f1f1f1-0000-7000-8000-000000000001' AND version = 2;
-- EXPECT: ok
UPDATE hub.customers SET language = 'en' WHERE id = '44444444-0000-7000-8000-000000000002';
-- EXPECT: rows=1
SELECT 1 FROM hub.customers WHERE id = '44444444-0000-7000-8000-000000000002' AND version = 2;
-- D-11: one customer per phone number
-- EXPECT: error
INSERT INTO hub.customers (id, site_id, plan_id, full_name, phone_e164) VALUES (hub.uuid_v7(), '11111111-0000-7000-8000-000000000001', '22222222-0000-7000-8000-000000000001', 'Duplicate Phone', '+243970000123');
-- EXPECT: ok
INSERT INTO hub.ai_runs (id, feature, model, served_model, fallback_ran, prompt_template, prompt_hash, payload_ref, input_tokens, output_tokens, cost_micro_usd,
  status, stop_category, ticket_id, customer_id, requested_by)
VALUES ('aaaaaaaa-0000-7000-8000-000000000001', 'reply_draft', 'claude-opus-5-5', 'claude-opus-5-5', false, 'reply_draft.v1', repeat('ab', 32), 's3://ai/ab', 1200, 300, 15000,
        'ok', 'completed', 'f1f1f1f1-0000-7000-8000-000000000001', '44444444-0000-7000-8000-000000000001', '33333333-0000-7000-8000-000000000001');
-- EXPECT: ok
INSERT INTO hub.ai_runs (id, feature, model, prompt_template, prompt_hash, status) VALUES
  (hub.uuid_v7(), 'triage', 'claude-opus-5-5', 't1', repeat('ab', 32), 'refused'),
  (hub.uuid_v7(), 'ask_data', 'claude-opus-5-5', 'a1', repeat('ab', 32), 'guard_blocked'),
  (hub.uuid_v7(), 'summary', 'claude-opus-5-5', 's1', repeat('ab', 32), 'started'),
  (hub.uuid_v7(), 'summary', 'claude-opus-5-5', 's1', repeat('ab', 32), 'budget_blocked');
-- the previous names are gone
-- EXPECT: error
INSERT INTO hub.ai_runs (id, feature, model, prompt_template, prompt_hash, status) VALUES (hub.uuid_v7(), 'triage', 'm', 't', repeat('ab', 32), 'blocked_guard');
-- EXPECT: error
INSERT INTO hub.ai_runs (id, feature, model, prompt_template, prompt_hash, status) VALUES (hub.uuid_v7(), 'chat', 'm', 't', repeat('ab', 32), 'ok');
-- EXPECT: error
INSERT INTO hub.ai_runs (id, feature, model, prompt_template, prompt_hash, status, fallback_ran) VALUES (hub.uuid_v7(), 'triage', 'm', 't', repeat('ab', 32), 'ok', true);
-- EXPECT: ok
INSERT INTO hub.ai_runs (id, feature, model, served_model, fallback_ran, prompt_template, prompt_hash, status) VALUES (hub.uuid_v7(), 'triage', 'm', 'approved-fallback', true, 't', repeat('ab', 32), 'ok');
-- EXPECT: error
INSERT INTO hub.ai_runs (id, feature, model, prompt_template, prompt_hash, status) VALUES (hub.uuid_v7(), 'triage', 'm', 't', 'not-a-hash', 'ok');
-- EXPECT: error
UPDATE hub.ai_runs SET human_decision = 'approved' WHERE id = 'aaaaaaaa-0000-7000-8000-000000000001';
-- EXPECT: ok
UPDATE hub.ai_runs SET human_decision = 'approved', reviewer_id = '33333333-0000-7000-8000-000000000002', decided_at = now() WHERE id = 'aaaaaaaa-0000-7000-8000-000000000001';
-- an AI draft cannot be approved or sent without an approver
-- EXPECT: error
INSERT INTO hub.ticket_messages (id, ticket_id, direction, author_type, body, status, ai_run_id)
VALUES (hub.uuid_v7(), 'f1f1f1f1-0000-7000-8000-000000000001', 'outbound', 'ai_draft', 'Bonjour', 'sent', 'aaaaaaaa-0000-7000-8000-000000000001');
-- EXPECT: ok
INSERT INTO hub.ticket_messages (id, ticket_id, direction, author_type, body, status, ai_run_id)
VALUES (hub.uuid_v7(), 'f1f1f1f1-0000-7000-8000-000000000001', 'outbound', 'ai_draft', 'Bonjour', 'draft', 'aaaaaaaa-0000-7000-8000-000000000001');
-- EXPECT: ok
INSERT INTO hub.ticket_messages (id, ticket_id, direction, author_type, body, status, ai_run_id, approved_by, approved_at)
VALUES ('abababab-0000-7000-8000-000000000001', 'f1f1f1f1-0000-7000-8000-000000000001', 'outbound', 'ai_draft', 'Bonjour, votre compteur est reconnecte.', 'sent',
        'aaaaaaaa-0000-7000-8000-000000000001', '33333333-0000-7000-8000-000000000002', now());
-- recall window: a message can be recalled, with its timestamp
-- EXPECT: error
INSERT INTO hub.ticket_messages (id, ticket_id, direction, author_type, body, status, ai_run_id, approved_by, approved_at)
VALUES (hub.uuid_v7(), 'f1f1f1f1-0000-7000-8000-000000000001', 'outbound', 'ai_draft', 'Bonjour', 'recalled', 'aaaaaaaa-0000-7000-8000-000000000001', '33333333-0000-7000-8000-000000000002', now());
-- EXPECT: ok
INSERT INTO hub.ticket_messages (id, ticket_id, direction, author_type, body, status, ai_run_id, approved_by, approved_at, recalled_at)
VALUES (hub.uuid_v7(), 'f1f1f1f1-0000-7000-8000-000000000001', 'outbound', 'ai_draft', 'Bonjour', 'recalled', 'aaaaaaaa-0000-7000-8000-000000000001', '33333333-0000-7000-8000-000000000002', now(), now());
-- a money-affecting draft needs a finance co-approval from a different person
-- EXPECT: error
INSERT INTO hub.ticket_messages (id, ticket_id, direction, author_type, body, status, ai_run_id, approved_by, approved_at, money_affecting)
VALUES (hub.uuid_v7(), 'f1f1f1f1-0000-7000-8000-000000000001', 'outbound', 'ai_draft', 'Remboursement accorde', 'sent', 'aaaaaaaa-0000-7000-8000-000000000001', '33333333-0000-7000-8000-000000000001', now(), true);
-- EXPECT: error
INSERT INTO hub.ticket_messages (id, ticket_id, direction, author_type, body, status, ai_run_id, approved_by, approved_at, money_affecting, finance_approver)
VALUES (hub.uuid_v7(), 'f1f1f1f1-0000-7000-8000-000000000001', 'outbound', 'ai_draft', 'Remboursement accorde', 'sent', 'aaaaaaaa-0000-7000-8000-000000000001', '33333333-0000-7000-8000-000000000001', now(), true, '33333333-0000-7000-8000-000000000001');
-- EXPECT: ok
INSERT INTO hub.ticket_messages (id, ticket_id, direction, author_type, body, status, ai_run_id, approved_by, approved_at, money_affecting, finance_approver)
VALUES (hub.uuid_v7(), 'f1f1f1f1-0000-7000-8000-000000000001', 'outbound', 'ai_draft', 'Remboursement accorde', 'sent', 'aaaaaaaa-0000-7000-8000-000000000001', '33333333-0000-7000-8000-000000000001', now(), true, '33333333-0000-7000-8000-000000000002');
-- message length cap (D-30)
-- EXPECT: error
INSERT INTO hub.ticket_messages (id, ticket_id, direction, author_type, body) VALUES (hub.uuid_v7(), 'f1f1f1f1-0000-7000-8000-000000000001', 'inbound', 'customer', repeat('x', 5001));
-- EXPECT: ok
INSERT INTO hub.ticket_messages (id, ticket_id, direction, author_type, body, status) VALUES
  ('abababab-0000-7000-8000-000000000002', 'f1f1f1f1-0000-7000-8000-000000000001', 'internal', 'agent', 'internal note: customer is a relative of staff', 'sent'),
  ('abababab-0000-7000-8000-000000000003', 'f1f1f1f1-0000-7000-8000-000000000001', 'inbound', 'customer', 'Mon compteur est eteint', 'received');
-- attachments: at most 3 per message, listed types, 5 MB
-- EXPECT: ok
INSERT INTO hub.attachments (id, ticket_id, message_id, object_key, sha256, content_type, size_bytes) VALUES
  (hub.uuid_v7(), 'f1f1f1f1-0000-7000-8000-000000000001', 'abababab-0000-7000-8000-000000000003', 'att/1', repeat('1', 64), 'image/jpeg', 1000),
  (hub.uuid_v7(), 'f1f1f1f1-0000-7000-8000-000000000001', 'abababab-0000-7000-8000-000000000003', 'att/2', repeat('2', 64), 'image/png', 1000),
  (hub.uuid_v7(), 'f1f1f1f1-0000-7000-8000-000000000001', 'abababab-0000-7000-8000-000000000003', 'att/3', repeat('3', 64), 'application/pdf', 1000);
-- EXPECT: error
INSERT INTO hub.attachments (id, ticket_id, message_id, object_key, sha256, content_type, size_bytes) VALUES
  (hub.uuid_v7(), 'f1f1f1f1-0000-7000-8000-000000000001', 'abababab-0000-7000-8000-000000000003', 'att/4', repeat('4', 64), 'image/jpeg', 1000);
-- EXPECT: error
INSERT INTO hub.attachments (id, ticket_id, object_key, sha256, content_type, size_bytes) VALUES
  (hub.uuid_v7(), 'f1f1f1f1-0000-7000-8000-000000000001', 'att/5', repeat('5', 64), 'text/html', 1000);
-- EXPECT: error
INSERT INTO hub.attachments (id, ticket_id, object_key, sha256, content_type, size_bytes) VALUES
  (hub.uuid_v7(), 'f1f1f1f1-0000-7000-8000-000000000001', 'att/6', repeat('6', 64), 'image/png', 5242881);
-- incidents link tickets
-- EXPECT: ok
INSERT INTO hub.incidents (id, site_id, title, created_by) VALUES ('f4f4f4f4-0000-7000-8000-000000000001', '11111111-0000-7000-8000-000000000001', 'Gateway down', '33333333-0000-7000-8000-000000000001');
-- EXPECT: ok
UPDATE hub.tickets SET incident_id = 'f4f4f4f4-0000-7000-8000-000000000001' WHERE id = 'f1f1f1f1-0000-7000-8000-000000000001';

-- ===== Row-level security: staff by site, customers by id (S-5, D-22) =====
-- no identity set: nothing is visible (fail closed)
-- EXPECT: ok
DO $$ DECLARE n integer; BEGIN
  SET LOCAL ROLE hub_api;
  SELECT count(*) INTO n FROM hub.customers; IF n <> 0 THEN RAISE EXCEPTION 'customers visible without identity: %', n; END IF;
  SELECT count(*) INTO n FROM hub.tickets; IF n <> 0 THEN RAISE EXCEPTION 'tickets visible without identity: %', n; END IF;
  SELECT count(*) INTO n FROM hub.top_ups; IF n <> 0 THEN RAISE EXCEPTION 'top-ups visible without identity: %', n; END IF;
  SELECT count(*) INTO n FROM hub.wallet_balances; IF n <> 0 THEN RAISE EXCEPTION 'wallets visible without identity: %', n; END IF;
  RESET ROLE;
END $$;
-- a site-assigned agent sees site 1 only; a user with no sites sees nothing; an all-sites user sees both
-- EXPECT: ok
DO $$ DECLARE n integer; BEGIN
  SET LOCAL ROLE hub_api;
  PERFORM set_config('app.user_id', '33333333-0000-7000-8000-000000000001', true);
  SELECT count(*) INTO n FROM hub.customers WHERE id = '44444444-0000-7000-8000-000000000001'; IF n <> 1 THEN RAISE EXCEPTION 'agent cannot see own-site customer'; END IF;
  SELECT count(*) INTO n FROM hub.customers WHERE id = '44444444-0000-7000-8000-000000000002'; IF n <> 0 THEN RAISE EXCEPTION 'agent sees another site customer'; END IF;
  SELECT count(*) INTO n FROM hub.tickets WHERE site_id = '11111111-0000-7000-8000-000000000002'; IF n <> 0 THEN RAISE EXCEPTION 'agent sees another site ticket'; END IF;
  SELECT count(*) INTO n FROM hub.ticket_messages WHERE ticket_id = 'f1f1f1f1-0000-7000-8000-000000000002'; IF n <> 0 THEN RAISE EXCEPTION 'agent sees another site messages'; END IF;
  SELECT count(*) INTO n FROM hub.top_ups WHERE customer_id = '44444444-0000-7000-8000-000000000002'; IF n <> 0 THEN RAISE EXCEPTION 'agent sees another site top-ups'; END IF;
  SELECT count(*) INTO n FROM hub.wallet_balances WHERE account_id = '66666666-0000-7000-8000-000000000006'; IF n <> 0 THEN RAISE EXCEPTION 'agent sees another site wallet'; END IF;
  SELECT count(*) INTO n FROM hub.ticket_messages WHERE ticket_id = 'f1f1f1f1-0000-7000-8000-000000000001'; IF n < 4 THEN RAISE EXCEPTION 'agent cannot read own-site thread'; END IF;
  PERFORM set_config('app.user_id', '33333333-0000-7000-8000-000000000005', true);
  SELECT count(*) INTO n FROM hub.customers; IF n <> 0 THEN RAISE EXCEPTION 'user without sites sees customers'; END IF;
  PERFORM set_config('app.user_id', '33333333-0000-7000-8000-000000000002', true);
  SELECT count(*) INTO n FROM hub.customers WHERE id IN ('44444444-0000-7000-8000-000000000001', '44444444-0000-7000-8000-000000000002'); IF n <> 2 THEN RAISE EXCEPTION 'all-sites user misses customers'; END IF;
  RESET ROLE;
END $$;
-- the site agent cannot create a customer on a site it is not assigned to
-- EXPECT: error
DO $$ BEGIN
  SET LOCAL ROLE hub_api;
  PERFORM set_config('app.user_id', '33333333-0000-7000-8000-000000000001', true);
  INSERT INTO hub.customers (id, site_id, plan_id, full_name, phone_e164) VALUES (hub.uuid_v7(), '11111111-0000-7000-8000-000000000002', '22222222-0000-7000-8000-000000000001', 'Intruder', '+243970009999');
END $$;
-- the portal: a customer sees only own rows, never internal notes, and not other customers' money
-- EXPECT: ok
DO $$ DECLARE n integer; BEGIN
  SET LOCAL ROLE hub_api;
  PERFORM set_config('app.customer_id', '44444444-0000-7000-8000-000000000001', true);
  SELECT count(*) INTO n FROM hub.customers; IF n <> 1 THEN RAISE EXCEPTION 'portal sees % customers', n; END IF;
  SELECT count(*) INTO n FROM hub.tickets; IF n <> 1 THEN RAISE EXCEPTION 'portal sees % tickets', n; END IF;
  SELECT count(*) INTO n FROM hub.ticket_messages WHERE direction = 'internal'; IF n <> 0 THEN RAISE EXCEPTION 'portal sees internal notes'; END IF;
  SELECT count(*) INTO n FROM hub.ticket_messages WHERE status = 'draft'; IF n <> 0 THEN RAISE EXCEPTION 'portal sees drafts'; END IF;
  SELECT count(*) INTO n FROM hub.ticket_messages; IF n < 2 THEN RAISE EXCEPTION 'portal cannot read its own thread'; END IF;
  SELECT count(*) INTO n FROM hub.top_ups WHERE customer_id <> '44444444-0000-7000-8000-000000000001'; IF n <> 0 THEN RAISE EXCEPTION 'portal sees other top-ups'; END IF;
  SELECT count(*) INTO n FROM hub.top_ups; IF n < 3 THEN RAISE EXCEPTION 'portal cannot see own top-ups'; END IF;
  SELECT count(*) INTO n FROM hub.wallet_balances; IF n <> 1 THEN RAISE EXCEPTION 'portal sees % wallets', n; END IF;
  SELECT count(*) INTO n FROM hub.ledger_accounts; IF n <> 1 THEN RAISE EXCEPTION 'portal sees % ledger accounts', n; END IF;
  SELECT count(*) INTO n FROM hub.meter_tokens WHERE customer_id <> '44444444-0000-7000-8000-000000000001'; IF n <> 0 THEN RAISE EXCEPTION 'portal sees other tokens'; END IF;
  RESET ROLE;
END $$;
-- system accounts are visible only to staff holding ledger.read (an agent has no such permission, finance has)
-- EXPECT: ok
DO $$ DECLARE n integer; BEGIN
  SET LOCAL ROLE hub_api;
  PERFORM set_config('app.user_id', '33333333-0000-7000-8000-000000000001', true);
  SELECT count(*) INTO n FROM hub.ledger_accounts WHERE customer_id IS NULL; IF n <> 0 THEN RAISE EXCEPTION 'system accounts visible without ledger.read'; END IF;
  PERFORM set_config('app.user_id', '33333333-0000-7000-8000-000000000002', true);
  SELECT count(*) INTO n FROM hub.ledger_accounts WHERE customer_id IS NULL; IF n < 4 THEN RAISE EXCEPTION 'finance cannot see system accounts'; END IF;
  RESET ROLE;
END $$;

-- ===== Outbox, inbox, idempotency (D-12, D-27, invariant 2) =====
-- EXPECT: ok
INSERT INTO hub.outbox (id, aggregate_type, aggregate_id, event_type, payload) VALUES
  (hub.uuid_v7(), 'top_up', 'c1c1c1c1-0000-7000-8000-000000000001', 'payment.confirmed', '{}'),
  (hub.uuid_v7(), 'top_up', 'c1c1c1c1-0000-7000-8000-000000000001', 'token.requested', '{}');
-- EXPECT: rows=3
UPDATE hub.outbox SET claimed_by = 'relay-1', claimed_at = now(), attempts = attempts + 1
WHERE id IN (SELECT id FROM hub.outbox
             WHERE published_at IS NULL AND dead_at IS NULL AND available_at <= now()
               AND (claimed_at IS NULL OR claimed_at < now() - interval '60 seconds')
             ORDER BY available_at, id LIMIT 100 FOR UPDATE SKIP LOCKED)
RETURNING id;
-- EXPECT: rows=0
UPDATE hub.outbox SET claimed_by = 'relay-2', claimed_at = now()
WHERE id IN (SELECT id FROM hub.outbox
             WHERE published_at IS NULL AND dead_at IS NULL AND available_at <= now()
               AND (claimed_at IS NULL OR claimed_at < now() - interval '60 seconds')
             ORDER BY available_at, id LIMIT 100 FOR UPDATE SKIP LOCKED)
RETURNING id;
-- a customer-facing message derived from AI output must carry the approval that released it
-- EXPECT: error
INSERT INTO hub.outbox (id, aggregate_type, aggregate_id, event_type, payload, customer_facing, ai_derived)
VALUES (hub.uuid_v7(), 'ticket_message', 'abababab-0000-7000-8000-000000000001', 'notification.requested', '{}', true, true);
-- EXPECT: ok
INSERT INTO hub.outbox (id, aggregate_type, aggregate_id, event_type, payload, customer_facing, ai_derived, approval_ref, available_at)
VALUES (hub.uuid_v7(), 'ticket_message', 'abababab-0000-7000-8000-000000000001', 'notification.requested', '{}', true, true,
        'abababab-0000-7000-8000-000000000001', now() + interval '30 seconds');
-- EXPECT: ok
INSERT INTO hub.consumer_inbox (consumer, event_id) VALUES ('notifier', 'abababab-0000-7000-8000-000000000001');
-- EXPECT: error
INSERT INTO hub.consumer_inbox (consumer, event_id) VALUES ('notifier', 'abababab-0000-7000-8000-000000000001');
-- EXPECT: ok
INSERT INTO hub.idempotency_keys (scope, idem_key, request_hash) VALUES
  ('POST /api/v1/top-ups:principal-1', 'key-1', repeat('0', 64)),
  ('POST /api/v1/tickets:principal-1', 'key-1', repeat('0', 64));
-- EXPECT: error
INSERT INTO hub.idempotency_keys (scope, idem_key, request_hash) VALUES ('POST /api/v1/top-ups:principal-1', 'key-1', repeat('0', 64));
-- D-12: seven days for top-ups, 24 hours elsewhere, whatever the client asks for
-- EXPECT: rows=1
SELECT 1 FROM hub.idempotency_keys WHERE scope LIKE 'POST /api/v1/top-ups%' AND expires_at - created_at = interval '7 days';
-- EXPECT: rows=1
SELECT 1 FROM hub.idempotency_keys WHERE scope LIKE 'POST /api/v1/tickets%' AND expires_at - created_at = interval '24 hours';

-- ===== Alerts, rules (7.2) =====
-- EXPECT: rows=4
SELECT kind FROM hub.alert_rules WHERE site_id IS NULL;
-- EXPECT: error
INSERT INTO hub.alert_rules (id, kind, threshold_value, unit) VALUES (hub.uuid_v7(), 'meter_offline', 60, 'minutes');
-- EXPECT: ok
INSERT INTO hub.alerts (id, site_id, gateway_id, kind, severity, dedup_key) VALUES
  (hub.uuid_v7(), '11111111-0000-7000-8000-000000000001', 'f2f2f2f2-0000-7000-8000-000000000001', 'site_offline', 'critical', 'site_offline:T-LUB');
-- EXPECT: error
INSERT INTO hub.alerts (id, site_id, gateway_id, kind, dedup_key) VALUES
  (hub.uuid_v7(), '11111111-0000-7000-8000-000000000001', 'f2f2f2f2-0000-7000-8000-000000000001', 'site_offline', 'site_offline:T-LUB');
-- EXPECT: error
INSERT INTO hub.alerts (id, site_id, meter_id, kind, dedup_key) VALUES
  (hub.uuid_v7(), '11111111-0000-7000-8000-000000000001', '55555555-0000-7000-8000-000000000001', 'site_offline', 'site_offline:x');
-- EXPECT: ok
INSERT INTO hub.alerts (id, site_id, meter_id, kind, dedup_key) VALUES
  (hub.uuid_v7(), '11111111-0000-7000-8000-000000000001', '55555555-0000-7000-8000-000000000001', 'meter_offline', 'meter_offline:MTR-T-0001');
-- EXPECT: error
INSERT INTO hub.alerts (id, site_id, kind, dedup_key) VALUES (hub.uuid_v7(), '11111111-0000-7000-8000-000000000001', 'tamper', 'tamper:1');

-- ===== Reconciliation (7.2, D-15) =====
-- EXPECT: ok
INSERT INTO hub.reconciliation_batches (id, provider, currency, period_start, period_end, statement_sha256, source)
VALUES ('f5f5f5f5-0000-7000-8000-000000000001', 'mtn_stub', 'USD', '2026-10-01', '2026-10-31', repeat('e', 64), 'fetched');
-- EXPECT: ok
INSERT INTO hub.reconciliation_items (id, batch_id, provider_ref, provider_amount_minor, currency, kind) VALUES
  ('f6f6f6f6-0000-7000-8000-000000000001', 'f5f5f5f5-0000-7000-8000-000000000001', 'txn-9001', 500, 'USD', 'statement_only'),
  ('f6f6f6f6-0000-7000-8000-000000000002', 'f5f5f5f5-0000-7000-8000-000000000001', 'txn-9002', 500, 'USD', 'duplicate_in_statement');
-- EXPECT: error
INSERT INTO hub.reconciliation_items (id, batch_id, provider_ref, provider_amount_minor, currency, kind) VALUES
  (hub.uuid_v7(), 'f5f5f5f5-0000-7000-8000-000000000001', 'txn-9003', 500, 'USD', 'missing_in_ledger');
-- leaving 'open' needs a note
-- EXPECT: error
UPDATE hub.reconciliation_items SET status = 'resolved', resolved_by = '33333333-0000-7000-8000-000000000002', resolved_at = now()
WHERE id = 'f6f6f6f6-0000-7000-8000-000000000002';
-- a blank note is not a note
-- EXPECT: error
UPDATE hub.reconciliation_items SET status = 'resolved', resolved_by = '33333333-0000-7000-8000-000000000002', resolved_at = now(), resolution_note = '   '
WHERE id = 'f6f6f6f6-0000-7000-8000-000000000002';
-- a money difference needs an approval
-- EXPECT: error
UPDATE hub.reconciliation_items SET status = 'resolved', resolved_by = '33333333-0000-7000-8000-000000000002', resolved_at = now(), resolution_note = 'booked to suspense'
WHERE id = 'f6f6f6f6-0000-7000-8000-000000000001';
-- EXPECT: ok
UPDATE hub.reconciliation_items SET status = 'resolved', resolved_by = '33333333-0000-7000-8000-000000000002', resolved_at = now(), resolution_note = 'same line twice in the provider file'
WHERE id = 'f6f6f6f6-0000-7000-8000-000000000002';
-- matching to a top-up needs evidence the system fetched, not an upload alone
-- EXPECT: error
UPDATE hub.reconciliation_items SET top_up_id = 'c1c1c1c1-0000-7000-8000-000000000001', status = 'pending_approval', evidence_source = 'upload',
  approval_id = '88888888-0000-7000-8000-000000000001', resolution_note = 'match requested'
WHERE id = 'f6f6f6f6-0000-7000-8000-000000000001';
-- EXPECT: ok
UPDATE hub.reconciliation_items SET top_up_id = 'c1c1c1c1-0000-7000-8000-000000000001', status = 'pending_approval', evidence_source = 'status_pull',
  approval_id = '88888888-0000-7000-8000-000000000001', resolution_note = 'match requested'
WHERE id = 'f6f6f6f6-0000-7000-8000-000000000001';

-- ===== Knowledge base: publication needs a second person (D-36), search EN and FR =====
-- EXPECT: ok
INSERT INTO hub.kb_articles (id, slug, status) VALUES ('bbbbbbbb-0000-7000-8000-0000000000b1', 'meter-offline', 'published');
-- EXPECT: error
INSERT INTO hub.kb_article_versions (id, article_id, language, version_no, title, body, published_at, created_by, approved_by, published_by) VALUES
  (hub.uuid_v7(), 'bbbbbbbb-0000-7000-8000-0000000000b1', 'fr', 9, 'Titre', 'Corps', now(), '33333333-0000-7000-8000-000000000001', '33333333-0000-7000-8000-000000000001', '33333333-0000-7000-8000-000000000002');
-- EXPECT: error
INSERT INTO hub.kb_article_versions (id, article_id, language, version_no, title, body, published_at, created_by, approved_by, published_by) VALUES
  (hub.uuid_v7(), 'bbbbbbbb-0000-7000-8000-0000000000b1', 'fr', 9, 'Titre', 'Corps', now(), '33333333-0000-7000-8000-000000000001', '33333333-0000-7000-8000-000000000002', '33333333-0000-7000-8000-000000000002');
-- EXPECT: error
INSERT INTO hub.kb_article_versions (id, article_id, language, version_no, title, body, published_at, created_by) VALUES
  (hub.uuid_v7(), 'bbbbbbbb-0000-7000-8000-0000000000b1', 'fr', 9, 'Titre', 'Corps', now(), '33333333-0000-7000-8000-000000000001');
-- EXPECT: ok
INSERT INTO hub.kb_article_versions (id, article_id, language, version_no, title, body, published_at, created_by, approved_by, published_by) VALUES
  ('cccccccc-0000-7000-8000-000000000001', 'bbbbbbbb-0000-7000-8000-0000000000b1', 'fr', 1, 'Compteur hors ligne', 'Si le compteur est hors ligne, verifiez le disjoncteur.', now(),
   '33333333-0000-7000-8000-000000000001', '33333333-0000-7000-8000-000000000002', '33333333-0000-7000-8000-000000000003'),
  ('cccccccc-0000-7000-8000-000000000002', 'bbbbbbbb-0000-7000-8000-0000000000b1', 'en', 1, 'Meter offline', 'If the meter is offline, check the circuit breaker.', now(),
   '33333333-0000-7000-8000-000000000001', '33333333-0000-7000-8000-000000000002', '33333333-0000-7000-8000-000000000003');
-- EXPECT: rows=1
SELECT id FROM hub.kb_article_versions WHERE search @@ websearch_to_tsquery('french', 'compteurs') AND language = 'fr';
-- EXPECT: rows=1
SELECT id FROM hub.kb_article_versions WHERE search @@ websearch_to_tsquery('english', 'breakers') AND language = 'en';
-- EXPECT: error
UPDATE hub.kb_article_versions SET body = 'edited after publication' WHERE id = 'cccccccc-0000-7000-8000-000000000001';

-- ===== Notifications, exports, authentication tables =====
-- EXPECT: ok
INSERT INTO hub.notification_templates (id, code, channel, language, body_template, param_schema)
VALUES ('f7f7f7f7-0000-7000-8000-000000000001', 'low_balance', 'sms', 'fr', 'Votre solde est bas: {balance}', '{"balance":"string"}');
-- EXPECT: ok
INSERT INTO hub.notifications (id, customer_id, template_id, channel, params, created_by)
VALUES (hub.uuid_v7(), '44444444-0000-7000-8000-000000000001', 'f7f7f7f7-0000-7000-8000-000000000001', 'sms', '{"balance":"2.50 USD"}', '33333333-0000-7000-8000-000000000001');
-- EXPECT: error
INSERT INTO hub.notifications (id, customer_id, template_id, channel, status) VALUES (hub.uuid_v7(), '44444444-0000-7000-8000-000000000001', 'f7f7f7f7-0000-7000-8000-000000000001', 'sms', 'sent');
-- exports: personal scopes only, at most a 7-day window, approved before they are prepared
-- EXPECT: error
INSERT INTO hub.data_exports (id, customer_id, scope, expires_at) VALUES (hub.uuid_v7(), '44444444-0000-7000-8000-000000000001', ARRAY['audit'], now() + interval '1 day');
-- EXPECT: error
INSERT INTO hub.data_exports (id, customer_id, scope, expires_at) VALUES (hub.uuid_v7(), '44444444-0000-7000-8000-000000000001', ARRAY['ledger'], now() + interval '1 day');
-- EXPECT: error
INSERT INTO hub.data_exports (id, customer_id, scope, expires_at) VALUES (hub.uuid_v7(), '44444444-0000-7000-8000-000000000001', ARRAY['profile'], now() + interval '8 days');
-- EXPECT: error
INSERT INTO hub.data_exports (id, customer_id, scope, expires_at, status) VALUES (hub.uuid_v7(), '44444444-0000-7000-8000-000000000001', ARRAY['profile'], now() + interval '1 day', 'ready');
-- EXPECT: ok
INSERT INTO hub.data_exports (id, customer_id, scope, expires_at) VALUES (hub.uuid_v7(), '44444444-0000-7000-8000-000000000001', ARRAY['profile','tickets'], now() + interval '1 day');
-- EXPECT: ok
SELECT 1 WHERE EXISTS (SELECT 1 FROM hub.permissions WHERE code = 'export:bulk');
-- EXPECT: error
INSERT INTO hub.users (id, email, display_name, password_hash, status) VALUES (hub.uuid_v7(), 'locked@example.test', 'x', 'x', 'locked');
-- EXPECT: rows=1
SELECT 1 FROM hub.users WHERE id = '33333333-0000-7000-8000-000000000001' AND token_version = 1;
-- EXPECT: ok
UPDATE hub.users SET token_version = token_version + 1 WHERE id = '33333333-0000-7000-8000-000000000005';
-- EXPECT: rows=1
SELECT 1 FROM hub.users WHERE id = '33333333-0000-7000-8000-000000000005' AND token_version = 2 AND version = 2;
-- EXPECT: ok
INSERT INTO hub.refresh_tokens (id, user_id, family_id, token_hash, expires_at) VALUES (hub.uuid_v7(), '33333333-0000-7000-8000-000000000001', hub.uuid_v7(), decode(repeat('11', 32), 'hex'), now() + interval '8 hours');
-- EXPECT: ok
INSERT INTO hub.refresh_tokens (id, customer_id, family_id, token_hash, expires_at) VALUES (hub.uuid_v7(), '44444444-0000-7000-8000-000000000002', hub.uuid_v7(), decode(repeat('12', 32), 'hex'), now() + interval '12 hours');
-- a token belongs to a user or a customer, never both and never neither
-- EXPECT: error
INSERT INTO hub.refresh_tokens (id, user_id, customer_id, family_id, token_hash, expires_at) VALUES (hub.uuid_v7(), '33333333-0000-7000-8000-000000000001', '44444444-0000-7000-8000-000000000002', hub.uuid_v7(), decode(repeat('13', 32), 'hex'), now() + interval '8 hours');
-- EXPECT: error
INSERT INTO hub.refresh_tokens (id, family_id, token_hash, expires_at) VALUES (hub.uuid_v7(), hub.uuid_v7(), decode(repeat('14', 32), 'hex'), now() + interval '8 hours');
-- EXPECT: error
INSERT INTO hub.refresh_tokens (id, user_id, family_id, token_hash, expires_at) VALUES (hub.uuid_v7(), '33333333-0000-7000-8000-000000000001', hub.uuid_v7(), decode(repeat('11', 32), 'hex'), now() + interval '8 hours');
-- EXPECT: ok
INSERT INTO hub.customer_otp (id, phone_hmac, code_hash, request_ip, expires_at) VALUES (hub.uuid_v7(), decode(repeat('21', 32), 'hex'), decode(repeat('22', 32), 'hex'), '198.51.100.20', now() + interval '5 minutes');
-- EXPECT: ok
INSERT INTO hub.mfa_recovery_codes (id, user_id, code_hash) VALUES (hub.uuid_v7(), '33333333-0000-7000-8000-000000000001', decode(repeat('31', 32), 'hex'));
-- EXPECT: ok
INSERT INTO hub.webauthn_credentials (id, user_id, credential_id, public_key, name) VALUES (hub.uuid_v7(), '33333333-0000-7000-8000-000000000002', '\xdeadbeef', '\x0102', 'yubikey');
-- EXPECT: error
INSERT INTO hub.webauthn_credentials (id, user_id, credential_id, public_key, name, sign_count) VALUES (hub.uuid_v7(), '33333333-0000-7000-8000-000000000002', '\xdeadbef0', '\x0102', 'bad', -1);

-- ===== Audit log: hash chain, no forging, tamper detection (D-22, S-8) =====
-- application roles append through audit_append; they cannot read or write the table
-- EXPECT: ok
DO $$ BEGIN SET LOCAL ROLE hub_ingest;
  PERFORM hub.audit_append('test', 'system', NULL, 'test.first', 'customer', '44444444-0000-7000-8000-000000000001', NULL, 'req-1', '{"k":1}', '198.51.100.7', '44444444-0000-7000-8000-000000000001');
  PERFORM hub.audit_append('test', 'system', NULL, 'test.second', 'customer', NULL, NULL, 'req-2', '{"k":2}');
  RESET ROLE; END $$;
-- EXPECT: ok
DO $$ BEGIN SET LOCAL ROLE hub_api; PERFORM hub.audit_append('test', 'user', '33333333-0000-7000-8000-000000000001', 'test.third', 'ticket', NULL, NULL, 'req-3', '{"k":3}'); RESET ROLE; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_api; PERFORM 1 FROM hub.audit_log; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_worker; PERFORM 1 FROM hub.audit_log; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_api; INSERT INTO hub.audit_log (id, stream, seq, hash, occurred_at, actor_type, action, entity_type) VALUES (hub.uuid_v7(), 'test', 99, '\x00', now(), 'user', 'forged', 'x'); END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE audit_writer; UPDATE hub.audit_log SET action = 'tampered'; END $$;
-- EXPECT: rows=3
SELECT seq FROM hub.audit_log WHERE stream = 'test';
-- each row's prev_hash is the previous row's hash
-- EXPECT: rows=2
SELECT 1 FROM hub.audit_log a JOIN hub.audit_log b ON b.stream = a.stream AND b.seq = a.seq + 1 WHERE a.stream = 'test' AND b.prev_hash = a.hash;
-- EXPECT: rows=1
SELECT 1 FROM hub.audit_log WHERE stream = 'test' AND seq = 1 AND prev_hash IS NULL;
-- EXPECT: rows=1
SELECT 1 WHERE hub.verify_audit_chain('test') IS NULL;
-- the IP address is outside the chain, in a side table keyed by the audit row
-- EXPECT: rows=1
SELECT 1 FROM hub.audit_ip;
-- EXPECT: error
UPDATE hub.audit_log SET action = 'tampered';
-- EXPECT: error
DELETE FROM hub.audit_log;
-- EXPECT: error
TRUNCATE hub.audit_log;
-- EXPECT: error
UPDATE hub.audit_anchors SET signature = '\x00';
-- altering a row (with the trigger disabled by a superuser) is detected by the verifier; the probe is rolled back
-- EXPECT: ok
DO $$ DECLARE bad bigint; BEGIN
  BEGIN
    ALTER TABLE hub.audit_log DISABLE TRIGGER trg_audit_log_immutable;
    UPDATE hub.audit_log SET details = '{"k":"changed"}' WHERE stream = 'test' AND seq = 2;
    bad := hub.verify_audit_chain('test');
    RAISE EXCEPTION 'probe:%', coalesce(bad::text, 'undetected');
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'probe:2' THEN RAISE EXCEPTION 'tampering not detected correctly: %', SQLERRM; END IF;
  END;
END $$;
-- deleting a row in the middle leaves a gap, also detected
-- EXPECT: ok
DO $$ DECLARE bad bigint; BEGIN
  BEGIN
    ALTER TABLE hub.audit_log DISABLE TRIGGER trg_audit_log_immutable;
    DELETE FROM hub.audit_ip;
    DELETE FROM hub.audit_log WHERE stream = 'test' AND seq = 2;
    bad := hub.verify_audit_chain('test');
    RAISE EXCEPTION 'probe:%', coalesce(bad::text, 'undetected');
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'probe:3' THEN RAISE EXCEPTION 'gap not detected correctly: %', SQLERRM; END IF;
  END;
END $$;
-- truncating the tail is caught by the signed anchor
-- EXPECT: ok
SELECT hub.audit_anchor('test', '\x0102');
-- EXPECT: ok
DO $$ DECLARE bad bigint; BEGIN
  BEGIN
    ALTER TABLE hub.audit_log DISABLE TRIGGER trg_audit_log_immutable;
    DELETE FROM hub.audit_ip;
    DELETE FROM hub.audit_log WHERE stream = 'test' AND seq = 3;
    bad := hub.verify_audit_chain('test');
    RAISE EXCEPTION 'probe:%', coalesce(bad::text, 'undetected');
  EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'probe:3' THEN RAISE EXCEPTION 'truncation not detected correctly: %', SQLERRM; END IF;
  END;
END $$;
-- the audit screen reads through audit_page, which checks the permission (administrator has audit.read, an agent does not)
-- EXPECT: ok
DO $$ DECLARE n integer; BEGIN
  SET LOCAL ROLE hub_api;
  PERFORM set_config('app.user_id', '33333333-0000-7000-8000-000000000006', true);
  SELECT count(*) INTO n FROM hub.audit_page('test', 0, 10); IF n <> 3 THEN RAISE EXCEPTION 'audit_page returned %', n; END IF;
  RESET ROLE;
END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_api; PERFORM set_config('app.user_id', '33333333-0000-7000-8000-000000000001', true); PERFORM 1 FROM hub.audit_page('test', 0, 10); END $$;

-- ===== Ask-the-data: grants and views (D-24, S-1, S-6) =====
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE analytics_support; PERFORM 1 FROM hub.customers; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE analytics_support; PERFORM phone_e164 FROM hub.customers; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE analytics_finance; PERFORM 1 FROM hub.ledger_entries; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE analytics_finance; PERFORM hub.uuid_v7(); END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE analytics_ops; PERFORM 1 FROM hub.meter_readings; END $$;
-- no row-level customer view exists for support any more
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE analytics_support; PERFORM 1 FROM analytics_support.v_customers; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE analytics_support; PERFORM 1 FROM analytics_finance.v_top_ups; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE analytics_ops; PERFORM 1 FROM analytics_support.v_ticket_daily; END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE analytics_support; INSERT INTO analytics_support.v_alert_daily (alerts) VALUES (1); END $$;
-- EXPECT: ok
DO $$ DECLARE n integer; BEGIN
  SET LOCAL ROLE analytics_support;
  SELECT count(*) INTO n FROM analytics_support.v_ticket_daily; IF n < 1 THEN RAISE EXCEPTION 'support view empty'; END IF;
  SELECT count(*) INTO n FROM analytics_common.v_sites; IF n < 2 THEN RAISE EXCEPTION 'common view empty'; END IF;
  SELECT count(*) INTO n FROM analytics_common.v_plans WHERE tariff_minor_per_kwh = 35; IF n <> 1 THEN RAISE EXCEPTION 'plan view lacks the tariff'; END IF;
  RESET ROLE;
END $$;
-- EXPECT: ok
DO $$ DECLARE n integer; r text; BEGIN
  SET LOCAL ROLE analytics_finance;
  SELECT count(*) INTO n FROM analytics_finance.v_ledger_lines WHERE account_kind = 'customer_wallet'; IF n < 3 THEN RAISE EXCEPTION 'finance ledger view empty'; END IF;
  SELECT customer_ref INTO r FROM analytics_finance.v_top_ups LIMIT 1;
  IF r IS NULL OR length(r) <> 24 OR r ~ '^[0-9a-f]{8}-' THEN RAISE EXCEPTION 'customer reference is not pseudonymous: %', r; END IF;
  SELECT count(*) INTO n FROM analytics_finance.v_top_up_daily; IF n < 1 THEN RAISE EXCEPTION 'finance daily view empty'; END IF;
  RESET ROLE;
END $$;
-- EXPECT: ok
DO $$ DECLARE n integer; BEGIN
  SET LOCAL ROLE analytics_ops;
  SELECT count(*) INTO n FROM analytics_ops.v_site_status; IF n < 2 THEN RAISE EXCEPTION 'ops status view empty'; END IF;
  SELECT count(*) INTO n FROM analytics_ops.v_token_daily; IF n < 1 THEN RAISE EXCEPTION 'ops token view empty'; END IF;
  RESET ROLE;
END $$;
-- proof by catalog: no analytics role holds any privilege on any object in schema hub
-- EXPECT: rows=0
SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'hub' AND c.relkind IN ('r','p','v')
  AND (has_any_column_privilege('analytics_support', c.oid, 'SELECT') OR has_any_column_privilege('analytics_finance', c.oid, 'SELECT')
    OR has_any_column_privilege('analytics_ops', c.oid, 'SELECT'));
-- EXPECT: rows=0
SELECT r.rolname FROM pg_roles r
WHERE r.rolname IN ('analytics_support','analytics_finance','analytics_ops') AND has_schema_privilege(r.rolname, 'hub', 'USAGE');
-- exactly the three role schemas plus the common one; the administrator has none
-- EXPECT: rows=4
SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'analytics\_%';
-- no personal-data, secret, phone or customer-row column in any analytics view
-- EXPECT: rows=0
SELECT table_schema, table_name, column_name FROM information_schema.columns
WHERE table_schema LIKE 'analytics\_%'
  AND column_name IN ('full_name','phone_e164','phone_masked','email','payer_msisdn','external_ref','pseudonym','password_hash','mfa_secret_enc',
                      'key_hash','payload','body','subject','token_enc','generated_sql','output','ip','customer_id','meter_id','serial_no');
-- support and operations expose no customer reference of any kind
-- EXPECT: rows=0
SELECT table_name, column_name FROM information_schema.columns
WHERE table_schema IN ('analytics_support','analytics_ops') AND (column_name LIKE '%customer%' OR column_name LIKE '%phone%' OR column_name LIKE '%ref');
-- no customer timestamp finer than a day anywhere in the analytics layer
-- EXPECT: rows=0
SELECT table_schema, table_name, column_name FROM information_schema.columns
WHERE table_schema LIKE 'analytics\_%' AND data_type LIKE 'timestamp%';
-- every analytics login role carries its own guard rails (pg_db_role_setting), read-only default included (S-1)
-- EXPECT: rows=3
SELECT r.rolname FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole
WHERE r.rolname LIKE 'analytics\_%'
  AND s.setconfig @> ARRAY['default_transaction_read_only=on','statement_timeout=5s','idle_in_transaction_session_timeout=10s','temp_file_limit=256MB'];
-- EXPECT: rows=3
SELECT rolname FROM pg_roles WHERE rolname LIKE 'analytics\_%' AND rolconnlimit = 10;
-- a migration run by the migrator can replace a definer function in place: the owner and the pinned search_path survive
-- EXPECT: ok
DO $$ DECLARE o text; c text; BEGIN
  SET LOCAL ROLE migrator;
  CREATE OR REPLACE FUNCTION hub.replay_erasure_log() RETURNS integer
  LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, hub, pg_temp AS $f$
  DECLARE c uuid; n integer := 0;
  BEGIN
    FOR c IN SELECT customer_id FROM hub.erasure_log ORDER BY erased_at LOOP
      PERFORM hub.scrub_customer(c);
      n := n + 1;
    END LOOP;
    RETURN n;
  END $f$;
  RESET ROLE;
  SELECT r.rolname, p.proconfig::text INTO o, c FROM pg_proc p JOIN pg_roles r ON r.oid = p.proowner WHERE p.proname = 'replay_erasure_log';
  IF o <> 'privacy_owner' OR c NOT LIKE '%search_path=pg_catalog, hub, pg_temp%' THEN RAISE EXCEPTION 'replacement changed owner or search_path: % %', o, c; END IF;
END $$;
-- the migrator is not a superuser, so it cannot read the ledger as an application by accident of privilege (it owns the tables, nothing more)
-- EXPECT: rows=0
SELECT 1 FROM pg_roles WHERE rolname = 'migrator' AND (rolsuper OR rolbypassrls OR rolcanlogin);
-- search_path is a role setting: the application roles and the migrator resolve plain table names to schema hub (Django db_table)
-- EXPECT: rows=5
SELECT r.rolname FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole
WHERE r.rolname IN ('hub_api','hub_ingest','hub_worker','ai_gateway','migrator') AND s.setconfig @> ARRAY['search_path=hub, pg_catalog'];

-- ===== Erasure (D-29, S-4) =====
-- EXPECT: ok
CREATE SCHEMA hub_test;
-- EXPECT: ok
CREATE FUNCTION hub_test.mk_erasure(p_req uuid, p_customer uuid, p_approver1 uuid, p_approver2 uuid, p_status text DEFAULT 'approved',
  p_hold text DEFAULT NULL, p_elapsed boolean DEFAULT true) RETURNS void LANGUAGE plpgsql AS $$
DECLARE a1 uuid := hub.uuid_v7(); a2 uuid := hub.uuid_v7();
BEGIN
  INSERT INTO hub.approvals (id, action, maker_user_id, maker_session_id, payload, status, approver_user_id, approver_session_id, decided_at, expires_at) VALUES
    (a1, 'erasure.approve', '33333333-0000-7000-8000-000000000001', hub.uuid_v7(), jsonb_build_object('request_id', p_req), 'approved', p_approver1, hub.uuid_v7(), now(), now() + interval '1 day'),
    (a2, 'erasure.approve', '33333333-0000-7000-8000-000000000001', hub.uuid_v7(), jsonb_build_object('request_id', p_req), 'approved', p_approver2, hub.uuid_v7(), now(), now() + interval '1 day');
  INSERT INTO hub.data_erasure_requests (id, customer_id, kind, requested_via, status, second_proof_at, legal_hold_reason, approval_1_id, approval_2_id, received_at, cooling_off_until)
  VALUES (p_req, p_customer, 'erasure', 'portal', p_status, now(), p_hold, a1, a2, now() - interval '9 days',
          CASE WHEN p_elapsed THEN now() - interval '1 day' ELSE now() + interval '6 days' END);
END $$;
-- customer 3 holds data in every place the function must scrub, and a zero-sum ledger history
-- EXPECT: ok
INSERT INTO hub.provider_events (id, provider, event_id, provider_txn_id, event_type, amount_minor, currency, payload) VALUES
  ('f8f8f8f8-0000-7000-8000-000000000001', 'mtn_stub', 'evt-c3-linked', 'txn-c3-1', 'payment.failed', 100, 'USD', '{"payer_msisdn":"+243970000789"}'),
  ('f8f8f8f8-0000-7000-8000-000000000002', 'mtn_stub', 'evt-c3-orphan', 'txn-c3-2', 'payment.failed', 100, 'USD', '{"msisdn":"+243970000789","note":"orphan, linked to no top-up"}'),
  ('f8f8f8f8-0000-7000-8000-000000000003', 'mtn_stub', 'evt-unrelated', 'txn-unrelated', 'payment.failed', 100, 'USD', '{"msisdn":"+243970001111"}');
-- EXPECT: ok
INSERT INTO hub.top_ups (id, customer_id, meter_id, provider, provider_ref, payer_msisdn, amount_minor, currency, plan_tariff_id, tariff_minor_per_kwh, energy_wh,
  status, provider_event_id, expires_at)
VALUES ('c1c1c1c1-0000-7000-8000-0000000000c3', '44444444-0000-7000-8000-000000000003', '55555555-0000-7000-8000-000000000003', 'mtn_stub', 'ref-c3', '+243970000789',
  100, 'USD', 'a1a1a1a1-0000-7000-8000-000000000001', 35, 2857, 'failed', 'f8f8f8f8-0000-7000-8000-000000000001', now() - interval '1 day');
-- EXPECT: ok
INSERT INTO hub.tickets (id, site_id, customer_id, channel, subject) VALUES ('f1f1f1f1-0000-7000-8000-0000000000c3', '11111111-0000-7000-8000-000000000001', '44444444-0000-7000-8000-000000000003', 'portal', 'Chantal: facture');
-- EXPECT: ok
INSERT INTO hub.ticket_messages (id, ticket_id, direction, author_type, body, status) VALUES
  ('abababab-0000-7000-8000-0000000000c3', 'f1f1f1f1-0000-7000-8000-0000000000c3', 'inbound', 'customer', 'Mon numero est +243970000789', 'received');
-- EXPECT: ok
INSERT INTO hub.attachments (id, ticket_id, message_id, object_key, sha256, content_type, size_bytes)
VALUES ('f9f9f9f9-0000-7000-8000-0000000000c3', 'f1f1f1f1-0000-7000-8000-0000000000c3', 'abababab-0000-7000-8000-0000000000c3', 'att/c3', repeat('7', 64), 'image/png', 1000);
-- EXPECT: ok
INSERT INTO hub.ai_runs (id, feature, model, prompt_template, prompt_hash, payload_ref, status, ticket_id, customer_id, output, generated_sql)
VALUES ('aaaaaaaa-0000-7000-8000-0000000000c3', 'summary', 'claude-opus-5-5', 's1', repeat('ab', 32), 's3://ai/c3', 'ok', 'f1f1f1f1-0000-7000-8000-0000000000c3',
        '44444444-0000-7000-8000-000000000003', '{"summary":"Chantal asks about her bill"}', 'select 1');
-- EXPECT: ok
INSERT INTO hub.outbox (id, aggregate_type, aggregate_id, event_type, payload) VALUES
  ('f0f0f0f0-0000-7000-8000-0000000000c3', 'notification', hub.uuid_v7(), 'notification.requested', '{"customer_id":"44444444-0000-7000-8000-000000000003","phone":"+243970000789"}');
-- EXPECT: ok
INSERT INTO hub.idempotency_keys (scope, idem_key, request_hash, subject_customer_id, response_body)
VALUES ('POST /api/v1/tickets:c3', 'k-c3', repeat('0', 64), '44444444-0000-7000-8000-000000000003', '{"phone":"+243970000789"}');
-- EXPECT: ok
SELECT hub.audit_append('privacy-test', 'customer', '44444444-0000-7000-8000-000000000003', 'login', 'customer', '44444444-0000-7000-8000-000000000003', NULL, 'req-c3', '{}', '203.0.113.77', '44444444-0000-7000-8000-000000000003');
-- EXPECT: ok
INSERT INTO hub.notifications (id, customer_id, template_id, channel, params) VALUES ('f0f0f0f0-0000-7000-8000-0000000000d3', '44444444-0000-7000-8000-000000000003', 'f7f7f7f7-0000-7000-8000-000000000001', 'sms', '{"balance":"1 USD"}');
-- EXPECT: ok
WITH t AS (INSERT INTO hub.ledger_transactions (id, posting_key, kind) VALUES (hub.uuid_v7(), 'c3:in', 'topup_received') RETURNING id)
INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency, amount_minor)
SELECT hub.uuid_v7(), t.id, v.acc, 'USD', v.amt FROM t, (VALUES ('66666666-0000-7000-8000-000000000002'::uuid, 100::bigint), ('66666666-0000-7000-8000-000000000007'::uuid, -100::bigint)) AS v(acc, amt);
-- EXPECT: ok
WITH t AS (INSERT INTO hub.ledger_transactions (id, posting_key, kind) VALUES (hub.uuid_v7(), 'c3:out', 'energy_purchase') RETURNING id)
INSERT INTO hub.ledger_entries (id, transaction_id, account_id, currency, amount_minor)
SELECT hub.uuid_v7(), t.id, v.acc, 'USD', v.amt FROM t, (VALUES ('66666666-0000-7000-8000-000000000007'::uuid, 100::bigint), ('66666666-0000-7000-8000-000000000003'::uuid, -100::bigint)) AS v(acc, amt);
-- EXPECT: ok
UPDATE hub.meters SET token_counter = token_counter + 1 WHERE id = '55555555-0000-7000-8000-000000000003';
-- EXPECT: ok
INSERT INTO hub.meter_tokens (id, meter_id, customer_id, ledger_transaction_id, energy_wh, counter, expires_at)
VALUES ('e1e1e1e1-0000-7000-8000-0000000000c3', '55555555-0000-7000-8000-000000000003', '44444444-0000-7000-8000-000000000003',
        (SELECT id FROM hub.ledger_transactions WHERE posting_key = 'c3:out'), 2857, 1, now() + interval '3 days');
-- request not yet approved
-- EXPECT: ok
SELECT hub_test.mk_erasure('dddddddd-0000-7000-8000-0000000000e1', '44444444-0000-7000-8000-000000000003', '33333333-0000-7000-8000-000000000002', '33333333-0000-7000-8000-000000000003', 'pending_approval');
-- EXPECT: ok
DO $$ DECLARE m text; BEGIN
  SET LOCAL ROLE hub_worker;
  PERFORM hub.erase_customer('44444444-0000-7000-8000-000000000003', 'dddddddd-0000-7000-8000-0000000000e1', '33333333-0000-7000-8000-000000000004');
  RAISE EXCEPTION 'NOT_RAISED';
EXCEPTION WHEN SQLSTATE '23001' THEN
  GET STACKED DIAGNOSTICS m = MESSAGE_TEXT;
  IF m NOT LIKE 'erasure_invalid: request status is%' THEN RAISE EXCEPTION 'wrong error: %', m; END IF;
END $$;
-- cooling-off period not over
-- EXPECT: ok
SELECT hub_test.mk_erasure('dddddddd-0000-7000-8000-0000000000e2', '44444444-0000-7000-8000-000000000003', '33333333-0000-7000-8000-000000000002', '33333333-0000-7000-8000-000000000003', 'approved', NULL, false);
-- EXPECT: ok
DO $$ DECLARE m text; BEGIN
  SET LOCAL ROLE hub_worker;
  PERFORM hub.erase_customer('44444444-0000-7000-8000-000000000003', 'dddddddd-0000-7000-8000-0000000000e2', '33333333-0000-7000-8000-000000000004');
  RAISE EXCEPTION 'NOT_RAISED';
EXCEPTION WHEN SQLSTATE '23001' THEN
  GET STACKED DIAGNOSTICS m = MESSAGE_TEXT;
  IF m NOT LIKE 'erasure_blocked: cooling-off%' THEN RAISE EXCEPTION 'wrong error: %', m; END IF;
END $$;
-- two approvals by the same person
-- EXPECT: ok
SELECT hub_test.mk_erasure('dddddddd-0000-7000-8000-0000000000e3', '44444444-0000-7000-8000-000000000003', '33333333-0000-7000-8000-000000000002', '33333333-0000-7000-8000-000000000002');
-- EXPECT: ok
DO $$ DECLARE m text; BEGIN
  SET LOCAL ROLE hub_worker;
  PERFORM hub.erase_customer('44444444-0000-7000-8000-000000000003', 'dddddddd-0000-7000-8000-0000000000e3', '33333333-0000-7000-8000-000000000004');
  RAISE EXCEPTION 'NOT_RAISED';
EXCEPTION WHEN SQLSTATE '23001' THEN
  GET STACKED DIAGNOSTICS m = MESSAGE_TEXT;
  IF m NOT LIKE 'erasure_invalid: two distinct approvers%' THEN RAISE EXCEPTION 'wrong error: %', m; END IF;
END $$;
-- legal hold
-- EXPECT: ok
SELECT hub_test.mk_erasure('dddddddd-0000-7000-8000-0000000000e4', '44444444-0000-7000-8000-000000000003', '33333333-0000-7000-8000-000000000002', '33333333-0000-7000-8000-000000000003', 'approved', 'tax audit 2026');
-- EXPECT: ok
DO $$ DECLARE m text; BEGIN
  SET LOCAL ROLE hub_worker;
  PERFORM hub.erase_customer('44444444-0000-7000-8000-000000000003', 'dddddddd-0000-7000-8000-0000000000e4', '33333333-0000-7000-8000-000000000004');
  RAISE EXCEPTION 'NOT_RAISED';
EXCEPTION WHEN SQLSTATE '23001' THEN
  GET STACKED DIAGNOSTICS m = MESSAGE_TEXT;
  IF m NOT LIKE 'erasure_blocked: legal hold%' THEN RAISE EXCEPTION 'wrong error: %', m; END IF;
END $$;
-- the operator must be a data-protection administrator; the request must belong to the customer
-- EXPECT: ok
SELECT hub_test.mk_erasure('dddddddd-0000-7000-8000-0000000000e5', '44444444-0000-7000-8000-000000000003', '33333333-0000-7000-8000-000000000002', '33333333-0000-7000-8000-000000000003');
-- EXPECT: ok
DO $$ DECLARE m text; BEGIN
  SET LOCAL ROLE hub_worker;
  PERFORM hub.erase_customer('44444444-0000-7000-8000-000000000003', 'dddddddd-0000-7000-8000-0000000000e5', '33333333-0000-7000-8000-000000000005');
  RAISE EXCEPTION 'NOT_RAISED';
EXCEPTION WHEN SQLSTATE '23001' THEN
  GET STACKED DIAGNOSTICS m = MESSAGE_TEXT;
  IF m NOT LIKE 'erasure_invalid: the operator%' THEN RAISE EXCEPTION 'wrong error: %', m; END IF;
END $$;
-- EXPECT: ok
DO $$ DECLARE m text; BEGIN
  SET LOCAL ROLE hub_worker;
  PERFORM hub.erase_customer('44444444-0000-7000-8000-000000000001', 'dddddddd-0000-7000-8000-0000000000e5', '33333333-0000-7000-8000-000000000004');
  RAISE EXCEPTION 'NOT_RAISED';
EXCEPTION WHEN SQLSTATE '23001' THEN
  GET STACKED DIAGNOSTICS m = MESSAGE_TEXT;
  IF m NOT LIKE 'erasure_invalid: request % is not an erasure request for customer%' THEN RAISE EXCEPTION 'wrong error: %', m; END IF;
END $$;
-- a token that is not acknowledged blocks erasure
-- EXPECT: ok
DO $$ DECLARE m text; BEGIN
  SET LOCAL ROLE hub_worker;
  PERFORM hub.erase_customer('44444444-0000-7000-8000-000000000003', 'dddddddd-0000-7000-8000-0000000000e5', '33333333-0000-7000-8000-000000000004');
  RAISE EXCEPTION 'NOT_RAISED';
EXCEPTION WHEN SQLSTATE '23001' THEN
  GET STACKED DIAGNOSTICS m = MESSAGE_TEXT;
  IF m NOT LIKE 'erasure_blocked: tokens%' THEN RAISE EXCEPTION 'wrong error: %', m; END IF;
END $$;
-- a wallet that is not zero blocks erasure (customer 1 holds 900)
-- EXPECT: ok
SELECT hub_test.mk_erasure('dddddddd-0000-7000-8000-0000000000e6', '44444444-0000-7000-8000-000000000001', '33333333-0000-7000-8000-000000000002', '33333333-0000-7000-8000-000000000003');
-- EXPECT: ok
DO $$ DECLARE m text; BEGIN
  SET LOCAL ROLE hub_worker;
  PERFORM hub.erase_customer('44444444-0000-7000-8000-000000000001', 'dddddddd-0000-7000-8000-0000000000e6', '33333333-0000-7000-8000-000000000004');
  RAISE EXCEPTION 'NOT_RAISED';
EXCEPTION WHEN SQLSTATE '23001' THEN
  GET STACKED DIAGNOSTICS m = MESSAGE_TEXT;
  IF m NOT LIKE 'erasure_blocked: the wallet%' THEN RAISE EXCEPTION 'wrong error: %', m; END IF;
END $$;
-- the application cannot call the scrub or the replay directly, nor erase as another role
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_api; PERFORM hub.erase_customer('44444444-0000-7000-8000-000000000003', 'dddddddd-0000-7000-8000-0000000000e5', '33333333-0000-7000-8000-000000000004'); END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_worker; PERFORM hub.scrub_customer('44444444-0000-7000-8000-000000000003'); END $$;
-- EXPECT: error
DO $$ BEGIN SET LOCAL ROLE hub_worker; PERFORM hub.replay_erasure_log(); END $$;
-- nothing was erased by the failed attempts
-- EXPECT: rows=0
SELECT 1 FROM hub.erasure_log;
-- the token is acknowledged: now the request goes through
-- EXPECT: ok
UPDATE hub.meter_tokens SET status = 'sent', sent_at = now() WHERE id = 'e1e1e1e1-0000-7000-8000-0000000000c3';
-- EXPECT: ok
UPDATE hub.meter_tokens SET status = 'acknowledged', acknowledged_at = now() WHERE id = 'e1e1e1e1-0000-7000-8000-0000000000c3';
-- EXPECT: ok
DO $$ BEGIN SET LOCAL ROLE hub_worker;
  PERFORM hub.erase_customer('44444444-0000-7000-8000-000000000003', 'dddddddd-0000-7000-8000-0000000000e5', '33333333-0000-7000-8000-000000000004');
  RESET ROLE; END $$;
-- EXPECT: rows=1
SELECT 1 FROM hub.customers WHERE id = '44444444-0000-7000-8000-000000000003' AND status = 'erased' AND phone_e164 IS NULL AND email IS NULL AND external_ref IS NULL AND full_name = '[erased]';
-- every place that held the customer's data is scrubbed
-- EXPECT: rows=1
SELECT 1 FROM hub.ticket_messages WHERE id = 'abababab-0000-7000-8000-0000000000c3' AND body = '[erased]';
-- EXPECT: rows=1
SELECT 1 FROM hub.tickets WHERE id = 'f1f1f1f1-0000-7000-8000-0000000000c3' AND subject = '[erased]';
-- EXPECT: rows=2
SELECT 1 FROM hub.provider_events WHERE event_id IN ('evt-c3-linked', 'evt-c3-orphan') AND payload = '{"erased": true}'::jsonb;
-- an unrelated event is left alone
-- EXPECT: rows=1
SELECT 1 FROM hub.provider_events WHERE event_id = 'evt-unrelated' AND payload ? 'msisdn';
-- EXPECT: rows=1
SELECT 1 FROM hub.top_ups WHERE id = 'c1c1c1c1-0000-7000-8000-0000000000c3' AND payer_msisdn IS NULL;
-- EXPECT: rows=1
SELECT 1 FROM hub.outbox WHERE id = 'f0f0f0f0-0000-7000-8000-0000000000c3' AND payload = '{"erased": true}'::jsonb;
-- EXPECT: rows=1
SELECT 1 FROM hub.idempotency_keys WHERE subject_customer_id = '44444444-0000-7000-8000-000000000003' AND response_body IS NULL;
-- EXPECT: rows=1
SELECT 1 FROM hub.ai_runs WHERE id = 'aaaaaaaa-0000-7000-8000-0000000000c3' AND output IS NULL AND generated_sql IS NULL AND payload_ref IS NULL;
-- EXPECT: rows=1
SELECT 1 FROM hub.notifications WHERE id = 'f0f0f0f0-0000-7000-8000-0000000000d3' AND params = '{}'::jsonb;
-- EXPECT: rows=1
SELECT 1 FROM hub.attachments WHERE id = 'f9f9f9f9-0000-7000-8000-0000000000c3' AND object_key IS NULL AND deleted_at IS NOT NULL;
-- the purge of stored files is queued, the audit address is gone, the chain still verifies
-- EXPECT: rows=1
SELECT 1 FROM hub.outbox WHERE event_type = 'attachment.purge' AND payload->>'object_key' = 'att/c3';
-- EXPECT: rows=1
SELECT 1 FROM hub.outbox WHERE event_type = 'payload.purge' AND payload->>'payload_ref' = 's3://ai/c3';
-- EXPECT: rows=0
SELECT 1 FROM hub.audit_ip WHERE subject_customer_id = '44444444-0000-7000-8000-000000000003';
-- EXPECT: rows=1
SELECT 1 WHERE hub.verify_audit_chain('privacy') IS NULL AND hub.verify_audit_chain('privacy-test') IS NULL;
-- EXPECT: rows=1
SELECT 1 FROM hub.audit_log WHERE stream = 'privacy' AND action = 'customer.erase';
-- the ledger is untouched and still balances
-- EXPECT: rows=4
SELECT 1 FROM hub.ledger_entries WHERE account_id = '66666666-0000-7000-8000-000000000007' OR (transaction_id IN (SELECT id FROM hub.ledger_transactions WHERE posting_key IN ('c3:in', 'c3:out')) AND account_id IN ('66666666-0000-7000-8000-000000000002','66666666-0000-7000-8000-000000000003') AND amount_minor IN (100, -100));
-- EXPECT: rows=0
SELECT * FROM hub.v_wallet_balance_drift;
-- the erasure is logged, the log is append-only, a second erasure of the same request is refused
-- EXPECT: rows=1
SELECT 1 FROM hub.erasure_log WHERE customer_id = '44444444-0000-7000-8000-000000000003';
-- EXPECT: error
UPDATE hub.erasure_log SET performed_by = NULL;
-- EXPECT: error
DELETE FROM hub.erasure_log;
-- EXPECT: ok
DO $$ DECLARE m text; BEGIN
  SET LOCAL ROLE hub_worker;
  PERFORM hub.erase_customer('44444444-0000-7000-8000-000000000003', 'dddddddd-0000-7000-8000-0000000000e5', '33333333-0000-7000-8000-000000000004');
  RAISE EXCEPTION 'NOT_RAISED';
EXCEPTION WHEN SQLSTATE '23001' THEN NULL;
END $$;
-- an erased customer cannot hold identifiers again
-- EXPECT: error
UPDATE hub.customers SET phone_e164 = '+243970000789' WHERE id = '44444444-0000-7000-8000-000000000003';
-- simulate a restore from an older backup: the identifiers come back, then the replay (after re-importing the log) erases them again
-- EXPECT: ok
UPDATE hub.customers SET status = 'active', erased_at = NULL, full_name = 'Restored Name', phone_e164 = '+243970000789', email = 'restored@example.test' WHERE id = '44444444-0000-7000-8000-000000000003';
-- EXPECT: ok
UPDATE hub.ticket_messages SET body = 'restored body' WHERE id = 'abababab-0000-7000-8000-0000000000c3';
-- EXPECT: rows=1
SELECT 1 WHERE hub.replay_erasure_log() >= 1;
-- EXPECT: rows=1
SELECT 1 FROM hub.customers WHERE id = '44444444-0000-7000-8000-000000000003' AND status = 'erased' AND phone_e164 IS NULL AND email IS NULL;
-- EXPECT: rows=1
SELECT 1 FROM hub.ticket_messages WHERE id = 'abababab-0000-7000-8000-0000000000c3' AND body = '[erased]';
