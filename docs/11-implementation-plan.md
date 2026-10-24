# 11. Implementation plan: slice index

This is the entry point for the implementing agent. It orders the work; each slice file in
`plan/` holds the tasks. Read `plan/00-executor-guide.md` first. Rules there beat anything here.

## 1. How the plan is organised

| Item | Where |
|---|---|
| Executor rules, frozen files, change requests | [plan/00-executor-guide.md](plan/00-executor-guide.md) |
| One file per backend slice | `plan/slice-NN-*.md` (table below) |
| One file per frontend slice | `plan/frontend/fe-NN-*.md` |
| Test doubles and tooling slices | `plan/slice-T1` to `plan/slice-T4` |
| Verifier checklist | [plan/99-verification.md](plan/99-verification.md) |
| Progress evidence (you write it) | `plan/progress.md`, template in the executor guide section 6 |

Every task is test-first, about one working day for a cheaper agent, and ends in one commit.

## 2. Slice index

| # | Slice file | Requirements | Depends on | Size |
|---|---|---|---|---|
| 0 | [slice-00-walking-skeleton](plan/slice-00-walking-skeleton.md) + [fe-00-shell](plan/frontend/fe-00-shell.md) | NFR-09, NFR-10, NFR-11, NFR-12, NFR-14 groundwork, FR-15 and FR-16 shell | none | L |
| T4 | [slice-T4-verification-tooling](plan/slice-T4-verification-tooling.md) | gates for NFR-06, NFR-14 | 0 | M |
| T3 | [slice-T3-fixtures-and-seed](plan/slice-T3-fixtures-and-seed.md) | enabler for all end-to-end tests | 0 | M |
| 1 | slice-01-identity-rbac-approvals + fe-01-auth | FR-15, FR-16, NFR-06 | 0 | L |
| 2 | slice-02-registry + fe-02-registry | FR-01, FR-16, NFR-11 | 1 | M |
| 3 | slice-03-ledger-core + fe-03-ledger-view | FR-04, NFR-05 | 1 | L |
| T1 | [slice-T1-provider-stub](plan/slice-T1-provider-stub.md) | enabler for 4, 14 | 0 | M |
| 4 | slice-04-topup-webhook + fe-04-portal-topup | FR-02, FR-03, NFR-01, NFR-02, NFR-05 | 2, 3, T1 | L |
| 5 | slice-05-outbox-relay-worker | NFR-01 enabler | 4 | M |
| T2 | [slice-T2-meter-simulator](plan/slice-T2-meter-simulator.md) | enabler for 6, 8 | 0 | M |
| 6 | slice-06-token-issuance | FR-05 | 5, T2 | L |
| 7 | slice-07-notifications | FR-19 | 5 | M |
| 8 | slice-08-telemetry-alerts + fe-08-alerts | FR-06, FR-07, NFR-03 | 5, T2 | L |
| 9 | slice-09-ops-dashboard + fe-09-dashboard | FR-13, NFR-02 | 8 | M |
| 10 | slice-10-support-desk + fe-10-inbox-kb | FR-08, FR-17 | 2 | L |
| 11 | slice-11-ai-gateway-triage | FR-09, FR-11, NFR-07, NFR-08, NFR-13 | 10, 5 | L |
| 12 | slice-12-reply-drafting + fe-12-drafts | FR-10, FR-16 | 11, 7 | M |
| 13 | slice-13-ask-the-data + fe-13-ask-the-data | FR-12 | 9, 11 | L |
| 14 | slice-14-reconciliation + fe-14-reconciliation | FR-14 | 4, T1 | M |
| 15 | slice-15-integrator-gateway-access + fe-15-admin-keys-approvals | FR-18 | 1, 8 | M |
| 16 | slice-16-privacy-lifecycle + fe-16-privacy | FR-20, NFR-07 | all modules | M |
| 17 | [slice-17-hardening](plan/slice-17-hardening.md) + fe-17-a11y-performance | NFR-01 to NFR-04, NFR-06, NFR-09, NFR-10, NFR-14 | all | L |

Full file names for the slices not linked above are in `plan/`; the slice number is the prefix.

## 3. Order, critical path and parallelism

Run in this order unless a second executor is available: 0, T4, T3, 1, 2, 3, T1, 4, 5, T2, 6, 7, 8,
9, 10, 11, 12, 13, 14, 15, 16, 17. The critical path is 0, 3, 4, 5, 6 because a customer payment
must become a token before anything else has business value. Parallel candidates: slices 7 and 9
beside 8; slice 10 after 2 beside 6 to 9; the test-double slices beside the module slices that
need them. Two executors must never edit the same module at once.

## 4. Milestones

| Milestone | After slice | Outcome shown to the owner |
|---|---|---|
| M1 Foundation | 3 | Staff log in with MFA; approved adjustments post to a balanced ledger |
| M2 Money to energy | 6 | A stub payment becomes an acknowledged token exactly once |
| M3 Operations | 9 | Telemetry, alerts and the live dashboard |
| M4 Support and AI | 13 | Triage, cited drafts with approval, ask-the-data |
| M5 Finance control | 16 | Reconciliation, partner access, privacy lifecycle |
| M6 Release candidate | 17 | Load, restore and accessibility evidence |

## 5. Spikes and open items

Spikes run inside the slice that names them and end in a short note in `plan/progress.md`:
frontend bundle budgets of 130 KB (portal) and 250 KB (staff) and the nonce CSP rendering cost
(fe-00); ask-the-data latency against the 8 s budget (slice 13, D-24).

Open business items, each with the default the executor must use as a setting, not a constant:

| Item | Default | Slice |
|---|---|---|
| Pull-verification threshold for confirmations | 100 USD or more | 4 |
| Revenue recognition at token issuance | recognise at issuance; finance lead confirms | 3 |
| AI-disclosure wording to customers | neutral wording in the template, replaceable | 12 |
| Retention periods sign-off | the single retention table in `04-data-model.md` | 16 |
| Synthetic probe wallet acceptance | enabled in staging only | 4, 17 |

## 6. Risks to the build

| Risk | Mitigation |
|---|---|
| The executor weakens a control to pass a test | Forbidden-pattern scan (T4), red flags in the verification checklist |
| Django habits break the design (admin, `ATOMIC_REQUESTS`, managed models) | `tools/check_django_config.py` in CI from slice 0 |
| Cross-module imports creep in | import-linter contracts from slice 0 |
| Large slices overrun a session | Each task is one commit; resume from `progress.md` |
| Plan ambiguity | Change requests, never improvisation |

## 7. Requirement coverage

Every requirement appears in the index above: FR-01 (2), FR-02 and FR-03 (4), FR-04 (3), FR-05
(6), FR-06 and FR-07 (8), FR-08 (10), FR-09 (11), FR-10 (12), FR-11 (11), FR-12 (13), FR-13 (9),
FR-14 (14), FR-15 (1), FR-16 (1, 2, 12), FR-17 (10), FR-18 (15), FR-19 (7), FR-20 (16). NFR-01
to NFR-14 are covered by slices 0, 4, 11, 16 and 17 as listed, with NFR-12 (company-owned
accounts, containers, no single-cloud lock-in) delivered by slice 0 and verified in slice 17.
