# 12. Review notes

## Process

1. Ten specialist documents, a shared contract and 21 ADRs were written by separate agents.
2. A consistency review (45 findings) and an adversarial security review produced decisions D-01 to
   D-50 and revisions of every document.
3. The stack changed to Django and Next.js at the owner's request (ADR-0019 to ADR-0021); all
   documents and the SQL-first schema were revised and re-tested.
4. Eight plan authors wrote the slice files, frontend plans, test-double specs and verifier checklist.
   Their gap reports were decided in [plan/contract-amendments.md](plan/contract-amendments.md).

## Automated gates (all run from the repository root)

`tests/test_schema_in_postgres.py` (schema and 396 invariant statements against PostgreSQL 16),
`tests/test_alignment.py` (OpenAPI versus schema enums, roles, administrator limits),
`tests/test_traceability.py` and `tools/check_traceability.py` (every requirement to criteria, API,
tests and slices). Last run: 34 passed, 0 checker errors.

## Known limits

- Nothing has been built; performance numbers are targets, to be proven by slice 17.
- Open business items: pull-verification threshold, revenue recognition, AI-disclosure wording,
  retention sign-off, two-approver erasure, staff paging channel, bundle budgets (spike).
- Expected gaps, not defects: the webhook event log status `dead` has no schema twin, and the
  period-reopen approval kind has no route yet. A later change request may add either.
- Plan files exceed their word targets in places (slice 00 about 6,000 words; slices 11 to 13 about
  4,000). The length is test names and assertions the executor needs.
- Some plan notes remain documentation-level mismatches in the older design documents (for example
  directory names in docs/09, a few TTLs in docs/02). The contract amendments file beats them.
