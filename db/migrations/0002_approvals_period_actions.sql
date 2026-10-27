-- phase: expand
-- 0002_approvals_period_actions: let the approval table store a period decision (D-40).
--
-- The baseline's `ck_approvals_action` lists the eleven actions known when it was written; a
-- period lock and a reopen are maker-checker decisions too (D-40), so the CHECK gains their two
-- actions. Additive on purpose: no existing row changes, and the constraint is replaced in one
-- statement so a concurrent insert either sees the old or the new list, never neither.
ALTER TABLE hub.approvals DROP CONSTRAINT ck_approvals_action;
ALTER TABLE hub.approvals ADD CONSTRAINT ck_approvals_action CHECK (
  action IN ('ledger.adjustment','ledger.refund','recon.resolve','tariff.change',
             'role.grant','user.create','mfa.reset','apikey.create','erasure.approve',
             'data.export','kb.publish','period.lock','period.reopen')
);