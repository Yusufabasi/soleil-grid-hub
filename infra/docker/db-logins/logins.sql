-- Turn the NOLOGIN application roles into local logins (docs/04 10.5, slice 00 Task 00.10).
--
-- db/schema.sql creates every role with NOLOGIN on purpose (section 0): in a real environment
-- the deployment adds LOGIN and a password from the secrets manager, and an attacker who only
-- reaches SQL can never authenticate as one of them. So the baseline migration creates the
-- roles, and this file -- run once by the `db-logins` compose service, after `migrate` has
-- completed -- is the local stand-in for that deployment step.
--
-- Idempotent: ALTER ROLE ... LOGIN PASSWORD sets the same value every time, so re-running the
-- service (or `make up` twice) changes nothing. The passwords arrive as psql variables read
-- from .env by compose, and are never written to a log: this file prints nothing.
--
-- Only the four application logins and the three analytics logins are enabled. The owner
-- roles (ledger_owner, audit_writer, privacy_owner) own SECURITY DEFINER functions and must
-- stay NOLOGIN, so changing a password on them is not part of the local setup.

\set ON_ERROR_STOP on

ALTER ROLE hub_api    LOGIN PASSWORD :'api_pw';
ALTER ROLE hub_ingest LOGIN PASSWORD :'ingest_pw';
ALTER ROLE hub_worker LOGIN PASSWORD :'worker_pw';
ALTER ROLE migrator   LOGIN PASSWORD :'migrator_pw';

ALTER ROLE analytics_support LOGIN PASSWORD :'analytics_pw';
ALTER ROLE analytics_finance LOGIN PASSWORD :'analytics_pw';
ALTER ROLE analytics_ops     LOGIN PASSWORD :'analytics_pw';
