# Frontend slice 04: Customer portal, top-up, balance, history and offline behaviour

- **Goal:** a customer on a low-end Android phone on 3G opens the portal in French, sees energy credit and wallet remainder, tops up in at most three actions, watches the payment reach a clear money state, reads history and tokens, and still sees last-known values when offline. Nothing is ever shown as paid before the server says `confirmed`.
- **Depends on:** backend slice 4 (top-up and webhook; also uses registry data from slice 2 and tokens from slice 6 when present); frontend slices fe-00, fe-01.
- **Requirements served:** FR-02, FR-05 (credited state display), FR-16, NFR-02 (portal budget), NFR-10, NFR-11.
- **Size:** L, 8 days.
- **Spikes or open items:** (1) Preset amounts per currency are not in the API and customers cannot read plans (`getPlan` is not allowed for `customer`); defaults: USD 5, 10, 20 and CDF 10 000, 20 000, 50 000 as configuration constants in `features/payments/presets.ts` **[open: product to confirm]**. (2) "Days left" estimate is not in the API; not built.

## Read first

- `docs/08-frontend-ux.md#1.2 Design principles that follow`
- `docs/08-frontend-ux.md#3.1 Customer: balance, top-up, history` (wireframes, balance rule, status label table)
- `docs/08-frontend-ux.md#2.2 Customer portal sitemap`
- `docs/08-frontend-ux.md#5.4 Data fetching and caching (TanStack Query)` (Persistence, Invalidations rows)
- `docs/08-frontend-ux.md#5.5 Optimistic updates policy`
- `docs/08-frontend-ux.md#8.4 Offline behaviour of the customer portal`
- `docs/08-frontend-ux.md#7.3 Screen-reader semantics for tables and live regions` (payment live region row)
- `docs/03-code-architecture.md#8. Frontend structure (Next.js, hub-web)`
- `docs/00-shared-context.md` section 7: D-05, D-07, D-10, D-11, D-12
- OperationIds: `getMe`, `getCustomerWallet`, `listMeters`, `createTopUp`, `getTopUp`, `listTopUps`, `cancelTopUp`, `listTokens`, `getToken`, `getNotificationPreferences`, `replaceNotificationPreferences`.

## Global constraints

- Server Actions banned; no secrets in `NEXT_PUBLIC_*`; image optimiser off; nonce CSP on every page.
- Access token in memory only. The service worker never caches `/api/*`, nor HTML under `/portal` or `/staff`, nor anything with credentials.
- Budget: portal first load at most 130 KB gzipped; the portal imports no staff code and no heavy library (no QR, no charts, no Markdown).
- next-intl `/fr` and `/en`; French first, sentences at about grade 6; no jargon ("ledger", "reconciliation" never appear).
- WCAG 2.2 AA: portal targets at least 48 px, text scaling to 200 percent at 360 px width, status always icon plus text plus colour.
- Generated client only; `Idempotency-Key` is generated once per top-up intent (UUID, kept in `sessionStorage` key `hub-topup-intent`) and reused on retry; the server window is 7 days for top-ups (D-12).
- Money is integer minor units; amounts typed as text, parsed by `parseMoneyInput`, never floats; money-moving screens use `exact: true`.
- Never optimistic for money: balance and payment state change only after a server response.
- No payment is ever queued offline; the Pay button is disabled offline with an explanation.
- Persistence of last-known values is app-level IndexedDB only (balance, last 20 history items, open tickets), wiped on logout and refresh-reuse sign-out, disabled in "shared phone" mode.
- Do not edit frozen files.

## Review focus

1. A double tap or reload creates two payments: `TopUpFlow.test.tsx` "double tap sends one request with one Idempotency-Key" and `e2e/topup.spec.ts` "reload during pending resumes the same top-up" (Task FE-04.3).
2. The status says success before `confirmed`, or tells the user to pay again when the state is `pending_unknown`: `statusLabel.test.ts` table test (Task FE-04.4).
3. A late confirmation (`late: true`) or reversal confuses the user: `PaymentStatus.test.tsx` "late flag shown beside Credited" and "reversed offers ticket" (Task FE-04.4).
4. Offline shows a blank screen or a stale balance with no time: `e2e/offline.spec.ts` "offline shows last-known balance with time and disabled Pay" (Task FE-04.6).
5. Personal data lingers on a shared phone after logout: `persistence.test.ts` "logout wipes IndexedDB and shared-phone mode never writes" (Task FE-04.6).

## Files

- `frontend/src/features/payments/keys.ts`, `presets.ts`, `statusLabel.ts`, `intent.ts` (idempotency intent), `prefs.ts` (remembered provider and payer number).
- `frontend/src/features/payments/hooks/useTopUps.ts`, `useTopUp.ts` (polling), `useCreateTopUp.ts`, `useCancelTopUp.ts`.
- `frontend/src/features/payments/schemas/topup.ts`.
- `frontend/src/features/payments/components/TopUpFlow.tsx`, `AmountStep.tsx`, `ConfirmStep.tsx`, `PaymentStatus.tsx`, `StatusBadgeForTopUp.tsx`, `HistoryList.tsx`, `ReceiptDetail.tsx`.
- `frontend/src/features/ledger/hooks/useMyBalance.ts` (customer wallet plus meter credit), `components/BalanceCard.tsx` (in the `ledger` feature because `getCustomerWallet` belongs to it).
- `frontend/src/features/meters/components/TokenList.tsx`, `hooks/useTokens.ts`.
- `frontend/src/features/customers/components/NotificationPrefs.tsx`, `hooks/useNotificationPrefs.ts`.
- `frontend/src/shared/offline/persistence.ts` (IndexedDB persister), `connectivity.ts` (`useConnectivity`), `OfflineBanner.tsx`, `SharedPhoneToggle.tsx`.
- `frontend/public/sw.js`, `frontend/src/app/manifest.ts`, `frontend/src/shared/offline/registerSw.ts`.
- `frontend/src/app/[locale]/(public)/offline/page.tsx`.
- `frontend/src/app/[locale]/(portal)/portal/page.tsx` (home), `topup/page.tsx`, `topup/[paymentId]/page.tsx`, `history/page.tsx`, `history/[id]/page.tsx`, `tokens/page.tsx`, `profile/page.tsx` (modify: preferences).
- `frontend/src/i18n/messages/{fr,en}/portal.json`, `topup.json`.
- Tests next to code; `frontend/e2e/topup.spec.ts`, `offline.spec.ts`.

## Interfaces

**Routes (portal):** `/portal`, `/portal/topup`, `/portal/topup/[paymentId]`, `/portal/history`, `/portal/history/[id]`, `/portal/tokens`, `/portal/profile`. Bottom tab bar: Accueil, Recharger, Historique, Aide (public `/help`) and Assistance (`/portal/support`, built in fe-10).

**Namespace `topup` (key: fr / en):**

| Key | fr | en |
|---|---|---|
| `topup.home.credit` | Votre crédit d'énergie | Your energy credit |
| `topup.home.kwh` | {kwh} kWh | {kwh} kWh |
| `topup.home.remainder` | Reste du portefeuille : {amount} | Wallet remainder: {amount} |
| `topup.home.remainderHint` | Argent gardé pour votre prochaine recharge | Money kept for your next top-up |
| `topup.home.updated` | Mis à jour à {time} | Updated at {time} |
| `topup.home.cta` | Recharger | Top up |
| `topup.home.empty` | Pas encore de crédit. Rechargez pour allumer votre compteur. | No credit yet. Top up to switch your meter on. |
| `topup.amount.title` | Combien voulez-vous payer ? | How much do you want to pay? |
| `topup.amount.other` | Autre montant | Other amount |
| `topup.provider` | Opérateur de paiement | Payment provider |
| `topup.payerPhone` | Numéro qui paie | Number paying |
| `topup.confirm.title` | Vérifiez avant de payer | Check before you pay |
| `topup.confirm.pay` | Payer {amount} | Pay {amount} |
| `topup.confirm.sending` | Envoi en cours | Sending |
| `topup.offline.cannotPay` | Il faut une connexion pour payer. | You need a connection to pay. |
| `topup.error.out_of_range` | Ce montant n'est pas autorisé pour votre offre. Essayez un autre montant. | This amount is not allowed for your plan. Try another amount. |
| `topup.error.meter_unavailable` | Ce compteur ne peut pas être rechargé pour le moment. Contactez l'assistance. | This meter cannot be topped up right now. Contact support. |
| `topup.status.initiated` | Démarrage | Starting |
| `topup.status.pending` | En attente de votre téléphone | Waiting for your phone |
| `topup.status.pending_unknown` | À vérifier | Needs review |
| `topup.status.paid_adding` | Payé, énergie en cours d'ajout | Paid, adding energy |
| `topup.status.credited` | Crédité | Credited |
| `topup.status.paid_delayed` | Payé, énergie retardée | Paid, energy delayed |
| `topup.status.failed` | Échec | Failed |
| `topup.status.expired` | Expiré | Expired |
| `topup.status.cancelled` | Annulé | Cancelled |
| `topup.status.reversed` | Remboursé par l'opérateur | Reversed |
| `topup.status.lateFlag` | Confirmé en retard | Confirmed late |
| `topup.sentence.pending` | Validez la demande sur votre téléphone. Ne payez pas une deuxième fois. | Approve the request on your phone. Do not pay again. |
| `topup.sentence.pending_unknown` | Nous vérifions ce paiement. Si de l'argent a quitté votre compte, vous ne le perdrez pas. Ne payez pas une deuxième fois. | We are checking this payment. If money left your account you will not lose it. Do not pay again. |
| `topup.sentence.credited` | L'énergie a été ajoutée à votre compteur. | Energy has been added to your meter. |
| `topup.sentence.paid_adding` | Paiement reçu. L'énergie arrive sur votre compteur. | Payment received. Energy is on its way to your meter. |
| `topup.sentence.paid_delayed` | Paiement reçu. Nous réessayons d'envoyer l'énergie à votre compteur. Vous n'avez pas besoin de payer à nouveau. | Payment received. We are retrying delivery to your meter. You do not need to pay again. |
| `topup.sentence.failed` | La demande a été refusée. Rien n'a été débité. | The payment was refused. Nothing was charged. |
| `topup.sentence.expired` | La demande a expiré. Si de l'argent a quitté votre compte, il sera quand même crédité. | The request timed out. If money left your account, it will still be credited. |
| `topup.sentence.cancelled` | Vous avez annulé cette demande. Si de l'argent a quitté votre compte, il sera quand même crédité. | You cancelled this request. If money left your account, it will still be credited. |
| `topup.sentence.reversed` | L'opérateur a rendu ce paiement. Contactez l'assistance si cela vous surprend. | The provider returned this payment. Contact support if this surprises you. |
| `topup.sentence.late` | Ce paiement est arrivé après l'expiration de la demande. Il a bien été crédité. | This payment arrived after the request timed out. It has been credited. |
| `topup.announce.credited` | Paiement confirmé. {amount} ajouté. | Payment confirmed. {amount} added. |
| `topup.announce.failed` | Paiement échoué. Rien n'a été débité. | Payment failed. Nothing was charged. |
| `topup.checkNow` | Vérifier maintenant | Check now |
| `topup.openTicket` | Ouvrir une demande d'assistance | Open a support request |
| `topup.sharedPhone` | Ceci est un téléphone partagé | This is a shared phone |

**Label mapping function (`statusLabel.ts`):** `topUpLabel(t: TopUp, token?: Token): {key: 'initiated'|'pending'|'pending_unknown'|'paid_adding'|'credited'|'paid_delayed'|'failed'|'expired'|'cancelled'|'reversed'; tone: 'ok'|'warn'|'bad'|'info'|'neutral'; late: boolean}`. Rules: `confirmed` with token `acknowledged` gives `credited`; with token `pending` or `sent` or no token yet gives `paid_adding`; with token `failed` or `unknown` gives `paid_delayed`; every other status maps to the key of the same name; `late` copies `t.late`.

**Hooks:** `useMyBalance()` returns `{creditWh: number|null, wallet: Money|null, asOf: string, isStale: boolean}` from `getCustomerWallet` (own customer id from `getMe` principal id) and `listMeters` (sum of `credit_remaining_wh` over the customer's non-decommissioned meters); `useTopUp(id)` polls every 3 s for the first 2 minutes then every 15 s, stops when `status` is final (`confirmed` with token acknowledged or failed, `failed`, `expired`, `cancelled`, `reversed`); `useCreateTopUp()` sends `Idempotency-Key` from `intent.ts`; `useConnectivity(): {online: boolean; lastOkAt?: string}` (`navigator.onLine` plus failed-request heuristic).

**Operations consumed:** the 11 operationIds under Read first.

## Tasks

### Task FE-04.1: Portal home with energy credit, wallet remainder and recent items

- [ ] **Step 1: failing test** `BalanceCard.test.tsx`, `portal-home.test.tsx`:
  ```ts
  test('headline is energy credit in kWh, remainder secondary', async () => {
    mockWallet({balance:{amount_minor:12,currency:'USD'}}); mockMeters([{credit_remaining_wh:42300,status:'active'}]);
    render(<BalanceCard/>);
    expect(await screen.findByText('42,3 kWh')).toBeVisible();
    expect(screen.getByText(/Reste du portefeuille : 0,12/)).toBeVisible();
  });
  test('empty state', ...);                      // topup.home.empty plus Recharger button 56px high
  test('error state shows last known value with time when cached', ...);
  test('partial: wallet ok, meters failed shows remainder and "Crédit indisponible"', ...);
  test('recent list shows 3 items with status text', ...);
  ```
- [ ] **Step 2:** `cd frontend && npm test -- BalanceCard portal-home`. Expected: fail.
- [ ] **Step 3: implement** `BalanceCard` and the portal home page (client component under server `page.tsx`): skeleton with a single hidden "Chargement du solde" text; `useMyBalance`; kWh with one decimal via `format.number`; remainder via `MoneyText exact`; "Recharger" `Button` 56 px high linking to `/portal/topup`; recent three items from `useTopUps({limit:3})` rendered with `StatusBadgeForTopUp`. Balance query `staleTime: 0` and refetch on focus.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): portal home with energy credit and wallet remainder`.

### Task FE-04.2: Money status labels (pure mapping)

- [ ] **Step 1:** `statusLabel.test.ts` table test:
  ```ts
  test.each([
    ['initiated',undefined,'initiated'],['pending',undefined,'pending'],['pending_unknown',undefined,'pending_unknown'],
    ['confirmed',undefined,'paid_adding'],['confirmed','sent','paid_adding'],['confirmed','acknowledged','credited'],
    ['confirmed','failed','paid_delayed'],['confirmed','unknown','paid_delayed'],
    ['failed',undefined,'failed'],['expired',undefined,'expired'],['cancelled',undefined,'cancelled'],['reversed',undefined,'reversed'],
  ])('%s + token %s => %s', (s,tok,key) => expect(topUpLabel(topUp(s),tok&&token(tok)).key).toBe(key));
  test('late flag copied', () => expect(topUpLabel({...topUp('confirmed'),late:true}, token('acknowledged')).late).toBe(true));
  test('never returns credited without acknowledged token', ...);
  ```
- [ ] **Step 2:** `npm test -- statusLabel`. Expected: fail.
- [ ] **Step 3: implement** `statusLabel.ts` exactly as the rules under Interfaces; tones: ok for `credited`, warn for `pending`, `paid_adding`, `paid_delayed`, `pending_unknown`, bad for `failed`, `reversed`, neutral for `expired`, `cancelled`, info for `initiated`. Add `StatusBadgeForTopUp` rendering icon, translated text and the late flag text.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): canonical money status labels for customers`.

### Task FE-04.3: Top-up flow with idempotent submit (TC-FR-02-4, TC-NFR-10-2)

- [ ] **Step 1:** `TopUpFlow.test.tsx`, `e2e/topup.spec.ts`:
  ```ts
  test('double tap sends one request with one Idempotency-Key', async () => {
    await toConfirmStep(); await user.dblClick(screen.getByRole('button',{name:'Payer 10,00 $US'}));
    expect(createCalls).toHaveLength(1); expect(createCalls[0].headers['idempotency-key']).toMatch(/^[0-9a-f-]{36}$/);
  });
  test('retry after network error reuses the same key', ...);
  test('amount chips preselect last used and "Autre montant" parses 12,50', ...);
  test('422 out_of_range shows translated message on amount field', ...);
  test('decommissioned or unavailable meter shows topup.error.meter_unavailable', ...);   // TC-FR-01-3
  test('offline disables Pay with explanation', ...);
  test('remembers provider and payer phone for next time (not in shared-phone mode)', ...);
  ```
  Playwright (keyboard only, 200 percent zoom at 360 px): amount, confirm, status reached, no mouse use.
- [ ] **Step 2:** `npm test -- TopUpFlow`; `npm run e2e -- topup.spec.ts`. Expected: fail.
- [ ] **Step 3: implement** `TopUpFlow` as a two-step client component (amount, confirm), URL step param `?step=confirm`. `AmountStep`: chips (`presets.ts` by wallet currency), optional text field with `inputmode="decimal"`, provider radio group (`mpesa_cd`, `airtel_cd`, `orange_cd`, labels from `topup.provider.*`), payer phone field prefilled from `prefs.ts`. `ConfirmStep`: summary (amount, estimated energy not shown since not known, provider, payer number masked), Pay button. On entering step 2 call `intent.ensure()` creating a UUID in `sessionStorage` `hub-topup-intent`. Pay: disable with `aria-disabled` and text `topup.confirm.sending`, `createTopUp` with `Idempotency-Key`; on success clear nothing yet and navigate to `/portal/topup/[id]`; clear the intent only when the status page reaches a final state. Meter choice: the customer's active meter from `listMeters` (if more than one, a select). Zod schema in `schemas/topup.ts` (E.164 payer, amount integer minor units > 0). Error mapping by `ApiError.code`; confirm actual codes with `grep -n "code:" api/openapi.yaml` near `/top-ups`; unknown codes show `errors.unknown` plus request id.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): top-up flow with idempotent submit`.

### Task FE-04.4: Payment status page with polling and live announcements

- [ ] **Step 1:** `PaymentStatus.test.tsx`:
  ```ts
  test('polls every 3 s then slows down and stops on final state', async () => {
    vi.useFakeTimers(); mockSequence(['pending','pending','confirmed+ack']); render(<PaymentStatus id={ID}/>);
    await vi.advanceTimersByTimeAsync(3000*2); expect(pollCalls).toBe(3); await vi.advanceTimersByTimeAsync(60000); expect(pollCalls).toBe(3);
  });
  test('announces confirmed once via status region', () => expect(liveRegion()).toHaveTextContent('Paiement confirmé. 10,00 $US ajouté.'));
  test('failure uses alert role', ...);
  test('pending_unknown shows do-not-pay-again and ticket button', ...);
  test('late flag shown beside Credited', ...);
  test('reversed offers support request', ...);
  test('offline shows last known status and "Ne payez pas à nouveau"', ...);
  test('cancel button only in initiated or pending and calls cancelTopUp', ...);
  ```
- [ ] **Step 2:** `npm test -- PaymentStatus`. Expected: fail.
- [ ] **Step 3: implement** `PaymentStatus` using `useTopUp(id)` and, when `token_id` is set, `getToken` for the token status; label from `topUpLabel`; render icon, text label, sentence, amount, time; "Vérifier maintenant" button forces a refetch; "Annuler" (`cancelTopUp`) only for `initiated`/`pending`; final states trigger a single announcement (`topup.announce.*`) and refetch of balance and history; `pending_unknown` and `reversed` show `topup.openTicket` linking to `/portal/support/new?payment=<id>` (page built in fe-10). Polling stops on final state; while hidden tab, polling pauses. Offline: last data from the query cache with the connectivity banner.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): payment status page with polling and announcements`.

### Task FE-04.5: History, receipt and tokens

- [ ] **Step 1:** `HistoryList.test.tsx`, `TokenList.test.tsx`:
  ```ts
  test('history list shows date, amount, label; load more uses cursor', ...);
  test('empty state with Recharger button', ...);
  test('receipt shows amount, fee, energy, provider reference, locale dates (fr and en)', ...);
  test('tokens list shows status text and attempts as "Nouvelle tentative" when pending with attempts>0', ...);
  test('error and partial states', ...);
  ```
- [ ] **Step 2:** `npm test -- HistoryList TokenList`. Expected: fail.
- [ ] **Step 3: implement** `/portal/history` (`useInfiniteQuery` with `listTopUps` and a "Voir plus" button, not infinite scroll), `/portal/history/[id]` receipt (`ReceiptDetail`: amount, fee, energy in kWh if `energy_wh`, provider, masked payer number, created and confirmed times, label, link to ticket creation), `/portal/tokens` (`listTokens`: masked token code, energy, status, attempts, expiry). Plural count in the list heading. All amounts `exact`.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): portal history, receipts and tokens`.

### Task FE-04.6: Offline behaviour, service worker, shared phone and notification preferences

- [ ] **Step 1:** `persistence.test.ts`, `sw.test.ts`, `NotificationPrefs.test.tsx`, `e2e/offline.spec.ts`:
  ```ts
  test('persists only balance, last 20 history items, open tickets', ...);
  test('logout wipes IndexedDB; refresh-reuse sign-out wipes it too', ...);
  test('shared phone mode never writes', ...);
  test('sw.js never handles /api or /portal requests', () => {
    const handled = simulateFetches(['/api/v1/me','/fr/portal','/_next/static/a.js','/fr/help/x']);
    expect(handled).toEqual({'/api/v1/me':false,'/fr/portal':false,'/_next/static/a.js':true,'/fr/help/x':true});
  });
  test('NotificationPrefs saves via replaceNotificationPreferences', ...);
  ```
  Playwright: after one online visit, `context.setOffline(true)`; reload `/fr/portal`: banner "Vous êtes hors connexion. Dernière mise à jour : 14:02", cached balance visible, "Recharger" shows disabled Pay with `topup.offline.cannotPay`.
- [ ] **Step 2:** `npm test -- persistence sw NotificationPrefs`; `npm run e2e -- offline.spec.ts`. Expected: fail.
- [ ] **Step 3: implement.** `persistence.ts`: TanStack `persistQueryClient` with an IndexedDB persister, `maxAge` 7 days, a `dehydrateOptions.shouldDehydrateQuery` allow-list of query keys (`balance`, `meters`, `topups:first20`, `tickets:open`); wipe function called by `signOut`. `SharedPhoneToggle` on the login and profile pages stores `hub-shared-phone=1` in `sessionStorage` (so it ends with the tab) and disables persistence and remembered payer data. `public/sw.js` (hand-written, about 60 lines): precache `/fr/offline`, `/en/offline`, manifest and icons; cache-first for `/_next/static/*`; stale-while-revalidate for `/{locale}/help/*` GET HTML; for every other request do nothing (no `respondWith`). Register with `registerSw.ts` only on public and portal pages, scope `/`; an update shows "Mise à jour disponible. Recharger" and never reloads during a pending payment (check `sessionStorage hub-topup-intent`). `app/manifest.ts`: names fr and en, icons 192 and 512 in `public/icons`, `theme_color` from tokens, `lang: 'fr'`. Offline page (server component) explains in plain words and links to `/portal`. `useConnectivity` plus `OfflineBanner`. `NotificationPrefs`: `getNotificationPreferences` then `replaceNotificationPreferences` with a toggle per channel (fields from generated types).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): offline persistence, service worker, shared-phone mode and notification preferences`.

## Verification

From `frontend/`:
1. `npm run lint && npm run typecheck` exit 0 (the lint also forbids `fetch` outside `src/api`, which includes the service worker file only by explicit allow-list entry `public/sw.js`).
2. `npm test` passes.
3. `npm run i18n:check` exits 0 (`portal`, `topup` equal in both locales).
4. `npm run build && npm run size` exit 0: every `/[locale]/portal/**` route at most 130 KB first load.
5. `npm run e2e -- topup.spec.ts offline.spec.ts` pass in mock mode; with `make up` and backend slice 4 plus the provider stub, `E2E_MODE=stack npm run e2e -- topup.spec.ts` passes (top-up to credited).
6. `npm run e2e:a11y -- --grep portal` zero serious or critical violations in `/fr` and `/en`, at 360 px and 200 percent text size.
7. Lighthouse CI on `/fr/portal/topup` with the throttled profile: LCP at most 2.5 s, total blocking time at most 300 ms (recorded in `docs/plan/progress.md`).

## Demo script

1. Open `/fr/portal` (signed in). FR: "Votre crédit d'énergie" shows kWh and "Reste du portefeuille". EN: `/en/portal` "Your energy credit", "Wallet remainder".
2. Press "Recharger" / "Top up", pick 10 USD, provider M-Pesa, check the summary, press "Payer 10,00 $US" / "Pay $10.00" once (try double tap: one payment).
3. The status page reads "En attente de votre téléphone" / "Waiting for your phone" with "Ne payez pas une deuxième fois." Trigger the provider stub confirmation: the page updates to "Crédité" / "Credited" and a screen reader announces "Paiement confirmé" / "Payment confirmed."
4. Reload during the pending state: the same top-up resumes, no second payment.
5. Make the stub return a late confirmation: the row shows "Confirmé en retard" / "Confirmed late".
6. Switch the browser offline and reload `/fr/portal`: the offline banner and last-known values show; "Recharger" explains that a connection is needed.
7. Sign out: reopen the app offline; no balance is shown (storage wiped).

## Done criteria

- Requirements: FR-02 (initiate, status, history), FR-05 (credited state display), FR-16, NFR-02 (portal budget), NFR-10, NFR-11.
- Test cases from `docs/10-test-strategy.md` section 8: TC-FR-02-4 (portal top-up to credited balance), TC-FR-01-3 (unavailable or decommissioned meter refused through the UI), TC-FR-02-3 (validation errors shown), TC-NFR-10-2 (keyboard-only top-up at 200 percent zoom), TC-NFR-11-3 (locale-aware dates and numbers in receipts), TC-NFR-02-4 (portal first load budget).

## Plan notes for the tech lead

- **Balance source:** `Wallet` carries only money (`balance`, `pending_top_ups`); `docs/08` 3.1 and D-10 want energy credit in kWh as the headline. `Meter.credit_remaining_wh` exists, so the plan sums it over the customer's meters. A "days left" estimate has no API source and is not built.
- **Customer cannot read plans:** `getPlan` excludes `customer`, so amount limits cannot be shown in advance; limits are enforced by the API and surfaced as `out_of_range`. Presets are constants **[open]**.
- **Route prefix:** `/portal` per `docs/03` (docs/08 says `/app`).
- **Public help in the tab bar:** `docs/08` lists `/help` inside the portal sitemap; `docs/03` keeps help under `(public)`. Followed `docs/03`; the tab links across groups.
- **Persistence versus service worker:** `docs/08` 8.4 allows app-level IndexedDB persistence of a few personal fields; D-44 says no caching of authenticated responses by the service worker. Both are honoured; flag for the security author if IndexedDB persistence of balance is considered too much on shared phones (the shared-phone toggle is the mitigation).
- **Problem code names** for top-up errors were not verified exhaustively; the executor confirms them in `api/openapi.yaml`.
