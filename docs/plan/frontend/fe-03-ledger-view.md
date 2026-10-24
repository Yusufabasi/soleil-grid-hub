# Frontend slice 03: Ledger and wallet views, adjustment and refund proposals, accounting periods

- **Goal:** finance officers read ledger accounts, balances and balanced transactions with explicit debit and credit direction, view a customer's wallet, propose manual adjustments and refunds (which go to maker-checker approval), and lock accounting periods with a confirmation that states the effect.
- **Depends on:** backend slice 3 (ledger core); frontend slices fe-00, fe-01, fe-02 (customer pages, `useStepUp`, `handleApprovalResult`).
- **Requirements served:** FR-04, NFR-05 (display of balanced postings), NFR-10, NFR-11.
- **Size:** M, 6 days.
- **Spikes or open items:** none. `api/openapi.yaml` marks `createLedgerAdjustment`, `createRefund` and `lockAccountingPeriod` with `x-requires-attribute: finance_lead`; the proposal and lock controls therefore show only to a `finance_officer` who also has the `finance_lead` attribute. Other finance officers read ledger data.

## Read first

- `docs/08-frontend-ux.md#3.7 Reconciliation exceptions queue (FR-14)` (the posting preview and `period_locked` paragraph only)
- `docs/08-frontend-ux.md#3.11 Maker-checker: pending approvals and review (D-15)` (what the maker sees)
- `docs/08-frontend-ux.md#3.16 Accounting-period lock (FR-04, FR-14)`
- `docs/08-frontend-ux.md#6.3 French typographic rules and formatting` (Money and Currency in tables rows)
- `docs/08-frontend-ux.md#7.3 Screen-reader semantics for tables and live regions`
- `docs/03-code-architecture.md#8. Frontend structure (Next.js, hub-web)` (Client state and caching: ledger statements stale 0 s after a mutation)
- `docs/00-shared-context.md` section 3 (ledger sign convention, D-03), section 7: D-03, D-05, D-06, D-15
- OperationIds: `listLedgerAccounts`, `getLedgerAccount`, `listLedgerTransactions`, `getLedgerTransaction`, `getCustomerWallet`, `createLedgerAdjustment`, `createRefund`, `listAccountingPeriods`, `lockAccountingPeriod`, `getTopUp`, `listTopUps`.

## Global constraints

- Server Actions banned; no secrets in `NEXT_PUBLIC_*`; image optimiser off; nonce CSP on every page.
- Access token in memory only; ledger pages are client components under thin server pages.
- Budgets: staff first load at most 250 KB.
- next-intl `/fr` and `/en`; French first.
- WCAG 2.2 AA: native tables, amounts right-aligned with tabular figures, direction shown as text ("Débit"/"Crédit"), not only colour or sign.
- Generated client only; clients never send ledger lines (the API generates postings).
- Money: integer minor units; the UI sums nothing except to display the per-currency debit and credit totals of one transaction it received (display check only); formatting through `formatMoney` with `exact: true` on every screen in this slice (money-moving screens never hide minor units).
- Money mutations are never optimistic: show a pending state until the server answers; ledger queries have stale time 0 after any mutation.
- Proposals (`createLedgerAdjustment`, `createRefund`) require step-up and return an approval; the UI never says "posted" for a proposal.
- Navigation and button visibility from `permissions.generated.ts`; ledger screens are `finance_officer` only; the administrator and support see nothing here. Proposal and lock controls also need the principal attribute `finance_lead` (`x-requires-attribute` in the OpenAPI document); use `usePermission('createLedgerAdjustment')` extended with `hasAttribute(principal,'finance_lead')` (add `requiresAttribute` data to the generated permissions file in Task FE-03.4 from the `x-requires-attribute` entries).
- Do not edit frozen files.

## Review focus

1. A proposed adjustment is displayed as if it were posted, and the finance officer believes the wallet changed: `AdjustmentForm.test.tsx` "shows submitted for approval and no balance change" (Task FE-03.4).
2. A CDF or USD amount loses a minor unit or shows a sign instead of a direction: `TransactionDetail.test.tsx` "lines show direction text and exact amounts" (Task FE-03.2).
3. The period lock is offered or attempted while exceptions remain, or the 409 with the exception count is shown as a generic error: `PeriodLockDialog.test.tsx` "lock disabled while open exceptions remain; 409 shows the count" (Task FE-03.5).
4. A `period_locked` error from an adjustment leaves the form looking stuck: `AdjustmentForm.test.tsx` "period_locked shows plain message and keeps form" (Task FE-03.4).
5. Double submit of a refund creates two proposals: `RefundForm.test.tsx` "one Idempotency-Key per intent, button disabled while pending" (Task FE-03.4).

## Files

- `frontend/src/features/ledger/keys.ts`, `hooks/useLedgerAccounts.ts`, `useLedgerTransactions.ts`, `useWallet.ts`, `usePeriods.ts`, `useProposeAdjustment.ts`, `useProposeRefund.ts`, `useLockPeriod.ts`.
- `frontend/src/features/ledger/schemas/adjustment.ts`, `refund.ts`.
- `frontend/src/features/ledger/components/AccountList.tsx`, `AccountDetail.tsx`, `TransactionList.tsx`, `TransactionDetail.tsx`, `PostingPreview.tsx`, `WalletCard.tsx`, `AdjustmentForm.tsx`, `RefundForm.tsx`, `PeriodList.tsx`, `PeriodLockDialog.tsx`.
- `frontend/src/app/[locale]/(staff)/staff/ledger/page.tsx` (accounts), `ledger/accounts/[id]/page.tsx`, `ledger/transactions/page.tsx`, `ledger/transactions/[id]/page.tsx`, `ledger/adjustments/new/page.tsx`, `ledger/periods/page.tsx`.
- `frontend/src/features/customers/components/CustomerWalletTab.tsx` (modify fe-02: adds the wallet card for finance on customer detail).
- `frontend/src/i18n/messages/{fr,en}/ledger.json`.
- Tests next to components; `frontend/e2e/ledger.spec.ts`.

## Interfaces

**Routes (staff, finance officer):** `/staff/ledger`, `/staff/ledger/accounts/[id]`, `/staff/ledger/transactions`, `/staff/ledger/transactions/[id]`, `/staff/ledger/adjustments/new?customer=<id>`, `/staff/ledger/periods`. Customer detail (fe-02) gains a "Portefeuille" section for finance.

**Namespace `ledger` (key: fr / en):**

| Key | fr | en |
|---|---|---|
| `ledger.accounts.title` | Comptes du grand livre | Ledger accounts |
| `ledger.account.type.customer_wallet` | Portefeuille client | Customer wallet |
| `ledger.account.type.provider_clearing` | Compte de transit opérateur | Provider clearing |
| `ledger.account.type.revenue_energy` | Revenus d'énergie | Energy revenue |
| `ledger.account.type.fees` | Frais | Fees |
| `ledger.account.type.suspense` | Compte d'attente | Suspense |
| `ledger.account.type.chargeback_receivable` | Créance sur rétrofacturation | Chargeback receivable |
| `ledger.balanceAsOf` | Solde au {time} | Balance as of {time} |
| `ledger.tx.kind.topup_received` | Recharge reçue | Top-up received |
| `ledger.tx.kind.energy_purchase` | Achat d'énergie | Energy purchase |
| `ledger.tx.kind.fee` | Frais | Fee |
| `ledger.tx.kind.refund` | Remboursement | Refund |
| `ledger.tx.kind.adjustment` | Ajustement | Adjustment |
| `ledger.tx.kind.reconciliation_fix` | Correction de rapprochement | Reconciliation fix |
| `ledger.tx.kind.reversal` | Annulation | Reversal |
| `ledger.line.debit` | Débit | Debit |
| `ledger.line.credit` | Crédit | Credit |
| `ledger.tx.balanced` | Débits {debits} = Crédits {credits} | Debits {debits} = Credits {credits} |
| `ledger.tx.empty` | Aucune écriture pour ces critères. | No entries for these criteria. |
| `ledger.adjust.title` | Proposer un ajustement | Propose an adjustment |
| `ledger.adjust.direction.credit_wallet` | Créditer le portefeuille | Credit the wallet |
| `ledger.adjust.direction.debit_wallet` | Débiter le portefeuille | Debit the wallet |
| `ledger.adjust.reason` | Motif (au moins 10 caractères) | Reason (at least 10 characters) |
| `ledger.adjust.submit` | Soumettre pour approbation | Submit for approval |
| `ledger.adjust.submitted` | Proposition n° {id} soumise. Rien n'est encore enregistré dans le grand livre. | Proposal no. {id} submitted. Nothing has been posted to the ledger yet. |
| `ledger.refund.title` | Proposer un remboursement | Propose a refund |
| `ledger.period.locked` | Cette période comptable est clôturée. Demandez au responsable financier de la rouvrir. | This accounting period is closed. Ask the finance lead to reopen it. |
| `ledger.period.lock.title` | Clôturer la période du {start} au {end} | Lock the period from {start} to {end} |
| `ledger.period.lock.effect` | Aucune écriture datée dans cette période ne pourra plus être créée ni modifiée. Les corrections iront dans la prochaine période ouverte. | No entry dated in this period can be created or changed afterwards. Corrections go into the next open period. |
| `ledger.period.lock.blocked` | Clôture impossible : {count, plural, one {# exception de rapprochement reste ouverte} other {# exceptions de rapprochement restent ouvertes}}. Résolvez-les d'abord. | Cannot lock: {count, plural, one {# reconciliation exception is still open} other {# reconciliation exceptions are still open}}. Resolve them first. |
| `ledger.period.lock.confirm` | Clôturer la période | Lock the period |

**Hooks:** `useLedgerAccounts({type?, currency?, cursor?})`; `useLedgerTransactions({account_id?, kind?, from?, to?, cursor?})`; `useWallet(customerId)` (staleTime 0); `useProposeAdjustment()` and `useProposeRefund()` return `{mutate, isPending}` and generate one idempotency key per form open; `usePeriods()`; `useLockPeriod()`.

**Components:** `PostingPreview({lines, accountsById})` renders rows with direction text, account code, exact amount, and the per-currency totals line (`ledger.tx.balanced`); `PeriodLockDialog({period})`.

**Operations consumed:** the 11 operationIds under Read first.

## Tasks

### Task FE-03.1: Ledger accounts list and account detail

- [ ] **Step 1: failing test** `AccountList.test.tsx`:
  ```ts
  test('lists accounts with type label, currency and exact balance', async () => {
    mockAccounts([{code:'wallet:C-004211',type:'customer_wallet',currency:'USD',balance:{amount_minor:12,currency:'USD'},balance_as_of:'2026-10-24T08:00:00Z'}]);
    render(<AccountList/>);
    expect(await screen.findByRole('table',{name:'Comptes du grand livre'})).toBeVisible();
    expect(screen.getByText('Portefeuille client')).toBeVisible();
    expect(screen.getByText(/0,12/)).toBeVisible();   // minor units never hidden
  });
  test('states: empty, error with request id, offline banner, partial (balance_as_of older than 5 min shows note)', ...);
  test('hidden for support agent (RequirePermission)', ...);
  ```
- [ ] **Step 2:** `cd frontend && npm test -- AccountList`. Expected: fail.
- [ ] **Step 3: implement** `AccountList` with `DataTable` (code as link, type label, currency, balance via `MoneyText exact`, `balance_as_of` via `DateText`), filters type and currency with an Apply button, cursor links. `AccountDetail` shows header, balance, "as of" note, and a recent transactions table filtered by `account_id`. Pages wrap in `RequirePermission operationId="listLedgerAccounts"`. A balance older than 5 minutes shows `common.state.offline`-style note "Solde au {time}" (key `ledger.balanceAsOf`).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): ledger accounts list and detail`.

### Task FE-03.2: Transactions list and detail with posting preview

- [ ] **Step 1:** `TransactionDetail.test.tsx`:
  ```ts
  test('lines show direction text and exact amounts', async () => {
    render(<TransactionDetail id={TX.id}/>);
    expect(await screen.findAllByText('Débit')).toHaveLength(1); expect(screen.getAllByText('Crédit')).toHaveLength(2);
    expect(screen.getByText('Débits 5,00 $US = Crédits 5,00 $US')).toBeVisible();   // after NBSP normalisation
  });
  test('reversal links to original transaction', ...);
  test('unbalanced payload from API shows integrity alert, not totals line', async () => {
    mockTx({lines:UNBALANCED}); expect(await screen.findByRole('alert')).toHaveTextContent('intégrité');
  });
  test('transactions list filters by kind and range with Appliquer', ...);
  ```
- [ ] **Step 2:** `npm test -- TransactionDetail TransactionList`. Expected: fail.
- [ ] **Step 3: implement** `TransactionList` (columns: effective date, kind label, description, posting key (monospace, copy button), link) and `TransactionDetail` with `PostingPreview`. `PostingPreview` groups lines by currency, sums debits and credits for display only (using integer addition), shows `ledger.tx.balanced` when equal and an alert `ledger.tx.integrity` (fr "Cette écriture n'est pas équilibrée. Signalez-le immédiatement à l'équipe technique.", en "This entry is not balanced. Report it to the technical team immediately.") otherwise. Shows `reverses_transaction_id` as a link. Right-aligned amounts with `font-variant-numeric: tabular-nums`.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): ledger transactions with balanced posting preview`.

### Task FE-03.3: Customer wallet card for finance

- [ ] **Step 1:** `WalletCard.test.tsx`:
  ```ts
  test('shows wallet balance, as-of time and pending top-ups count', ...);
  test('wallet fetch always refetches on mount (staleTime 0)', ...);
  test('non-finance role does not render the card', ...);
  test('error state shows retry without hiding customer profile', ...);
  ```
- [ ] **Step 2:** `npm test -- WalletCard`. Expected: fail.
- [ ] **Step 3: implement** `WalletCard` using `useWallet(customerId)` (`getCustomerWallet`), rendered in `CustomerWalletTab` on the customer detail page only when `usePermission('getCustomerWallet')`. Shows `balance` (exact), `as_of`, `pending_top_ups` ("{count, plural, =0 {Aucune recharge en attente} one {# recharge en attente} other {# recharges en attente}}", key `ledger.wallet.pending`), and links "Voir les écritures" to the transactions list filtered by `ledger_account_id` and "Proposer un ajustement" to the form with `?customer=`. Refetch on window focus.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): finance wallet card on customer detail`.

### Task FE-03.4: Adjustment and refund proposals (TC-FR-04-5 UI)

- [ ] **Step 1:** `AdjustmentForm.test.tsx`, `RefundForm.test.tsx`:
  ```ts
  test('shows submitted for approval and no balance change', async () => {
    server.use(adjust202()); await fill({direction:'credit_wallet', amount:'5,50', reason:'Geste commercial après échec'});
    await submit(); expect(await screen.findByRole('status')).toHaveTextContent('Rien n\'est encore enregistré dans le grand livre');
    expect(walletRefetchCalls).toBe(0);   // wallet not presented as changed
  });
  test('amount parsed from "5,50" to 550 minor units', () => expect(lastBody().amount).toEqual({amount_minor:550,currency:'USD'}));
  test('period_locked shows plain message and keeps form', async () => {
    server.use(problem(409,'period_locked')); await submit();
    expect(screen.getByRole('alert')).toHaveTextContent('Cette période comptable est clôturée'); expect(screen.getByLabelText(/Motif/)).toHaveValue(typed);
  });
  test('one Idempotency-Key per intent, button disabled while pending', ...);
  test('step-up dialog appears then request replays once', ...);
  test('reason shorter than 10 characters shows field error', ...);
  ```
  `e2e/ledger.spec.ts`: "finance officer proposes adjustment and sees pending approval link".
- [ ] **Step 2:** `npm test -- AdjustmentForm RefundForm`; `npm run e2e -- ledger.spec.ts`. Expected: fail.
- [ ] **Step 3: implement.** `AdjustmentForm` (customer picked from `?customer=` or a customer search, direction radio, amount text parsed by the shared locale-aware parser `parseMoneyInput(text, currency, locale)` returning integer minor units, reason textarea 10 to 1,000 characters, optional ticket id), zod schema `adjustment.ts`; submit goes through `withStepUp('Proposer un ajustement', ...)`, then `handleApprovalResult`. `RefundForm` starts from a top-up (`getTopUp` for context: shows amount, status, date; amount defaults to the full top-up amount, never above it) and posts `createRefund`. Both disable the submit button with `aria-disabled` while pending and show exactly the text of `ledger.adjust.submitted` on 202. Problem codes map: `period_locked` to `ledger.period.locked`; `conflict_of_interest` and `self_approval_forbidden` are approval-time errors handled in fe-15; `validation_error` to field errors. Never invalidate wallet or ledger queries on 202 (nothing changed).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): propose ledger adjustments and refunds through approval`.

### Task FE-03.5: Accounting periods and lock confirmation

- [ ] **Step 1:** `PeriodLockDialog.test.tsx`:
  ```ts
  test('lock disabled while open exceptions remain; 409 shows the count', async () => {
    render(<PeriodLockDialog period={{...P, open_exceptions:3}}/>);
    expect(screen.getByText(/Aucune écriture datée dans cette période/)).toBeVisible();
    expect(screen.getByText(/Clôture impossible : 3 exceptions de rapprochement restent ouvertes/)).toBeVisible();
    expect(screen.getByRole('button',{name:'Clôturer la période'})).toBeDisabled();
  });
  test('zero open exceptions enables lock; a 409 from the server (race) shows the returned count', ...);
  test('button hidden without finance_lead attribute', ...);
  test('lock goes through step-up then lockAccountingPeriod, then refetches periods', ...);
  test('locked period shows locked_by and date, no lock button', ...);
  ```
- [ ] **Step 2:** `npm test -- PeriodLockDialog PeriodList`. Expected: fail.
- [ ] **Step 3: implement** `PeriodList` (table: start, end, status badge "Ouverte"/"Clôturée" with text, open exceptions count, lock action) and `PeriodLockDialog` per `docs/08` 3.16 with one change: the API refuses a lock with 409 while exceptions remain (`lockAccountingPeriod` description), so there is no acceptance checkbox. The dialog shows the effect text, shows `ledger.period.lock.blocked` and disables the button when `open_exceptions > 0`, otherwise enables it; optional note, `withStepUp` then `lockAccountingPeriod({note})`; a 409 shows the same message using the count in the problem detail; if the response is an approval (202) use `handleApprovalResult`, otherwise refetch periods and announce "Période clôturée" in the live region (`ledger.period.lockedDone`: fr "Période clôturée.", en "Period locked."). Reopening has no operation in the API; no reopen button is built.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): accounting periods with lock confirmation`.

## Verification

From `frontend/`:
1. `npm run lint && npm run typecheck` exit 0.
2. `npm test` passes (ledger suites included).
3. `npm run i18n:check` exits 0.
4. `npm run build && npm run size` exit 0.
5. `npm run e2e -- ledger.spec.ts` passes in mock mode; with backend slice 3 up, `E2E_MODE=stack npm run e2e -- ledger.spec.ts` passes.
6. `npm run e2e:a11y -- --grep ledger` zero serious or critical violations at `/fr/staff/ledger`, `/staff/ledger/transactions/[id]`, `/staff/ledger/periods`.
7. Search check: `grep -rn "toFixed\|parseFloat" src/features/ledger` returns nothing.

## Demo script

1. Sign in as a finance officer; open `/fr/staff/ledger`: "Comptes du grand livre" with exact balances. EN: "Ledger accounts".
2. Open a transaction: lines read "Débit" and "Crédit" with "Débits 5,00 $US = Crédits 5,00 $US". EN: "Debits $5.00 = Credits $5.00".
3. As a finance lead (attribute `finance_lead`), open a customer, press "Proposer un ajustement" / "Propose an adjustment", credit 5,50 USD with a reason, confirm the step-up: "Proposition n° … soumise. Rien n'est encore enregistré dans le grand livre." / "Proposal no. … submitted. Nothing has been posted to the ledger yet."
4. Open `/fr/staff/ledger/periods` as a finance lead, press lock on a period with open exceptions: the dialog states the effect and says "Clôture impossible : 3 exceptions de rapprochement restent ouvertes" / "Cannot lock: 3 reconciliation exceptions are still open"; on a period with none the lock succeeds after step-up.
5. Sign in as a support agent or the administrator: no Ledger entry in the navigation; typing the URL shows "Accès non autorisé" / "You do not have access".

## Done criteria

- Requirements: FR-04 (ledger and wallet views, adjustment proposals, period lock), NFR-10, NFR-11.
- Test cases from `docs/10-test-strategy.md` section 8 (UI contribution): TC-FR-04-5 (`period_locked` shown plainly), TC-FR-14-3 (the adjustment path used by finance, completed in fe-14 and fe-15), TC-NFR-11-1 (money display in ledger tables), TC-FR-15-15 (support and administrator denied the ledger).

## Plan notes for the tech lead

- **Lock authority and exceptions:** `api/permissions.yaml` lists only the role `finance_officer`, while `api/openapi.yaml` adds `x-requires-attribute: finance_lead` on the lock, adjustment and refund operations; the plan follows both (role plus attribute). `docs/08` 3.16 lets the lock proceed after accepting open exceptions; the OpenAPI description says the lock is refused while exceptions remain. Followed the OpenAPI (refusal). `permissions.yaml` does not carry attributes, so the permissions generator must also read `x-requires-attribute` from `api/openapi.yaml`.
- **Lock result shape:** the API does not say whether the lock returns an approval; the UI handles a direct 200 and a 202 approval.
- **Reopen:** `docs/08` 3.16 describes reopening with a reason; the API has no reopen operation. Not built.
- **No ledger line input:** `AdjustmentCreate` takes `direction` and `amount`; the UI never builds lines, consistent with D-03 and the invariant that clients never send postings.
- **Parsing helper:** `parseMoneyInput` is introduced here in `frontend/src/shared/format/money.ts` (add it with its own test in Task FE-03.4 step 1: "5,50" and "5.50" give 550 for USD; "28000" gives 2800000 for CDF; "1,234" is rejected as ambiguous).
