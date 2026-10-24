# Frontend slice 02: Registry (customers, sites, plans, meters)

- **Goal:** staff create, search, read and edit customers, sites, plans and meters in French and English, with optimistic-concurrency conflicts, duplicate-phone and duplicate-serial errors and role-limited actions; plan tariffs are proposed through approval.
- **Depends on:** backend slice 2 (registry); frontend slices fe-00 and fe-01.
- **Requirements served:** FR-01, FR-16, NFR-10, NFR-11.
- **Size:** M, 6 days.
- **Spikes or open items:** none. Default for the open question "who edits plans": `finance_officer` only, as in `api/permissions.yaml`.

## Read first

- `docs/08-frontend-ux.md#2.3 Staff app sitemap and navigation per role`
- `docs/08-frontend-ux.md#5.3 Forms and validation`
- `docs/08-frontend-ux.md#5.4 Data fetching and caching (TanStack Query)`
- `docs/08-frontend-ux.md#7.3 Screen-reader semantics for tables and live regions`
- `docs/08-frontend-ux.md#9.1 Security notes` (phone masking row)
- `docs/03-code-architecture.md#8. Frontend structure (Next.js, hub-web)` (Client state and caching paragraph)
- `docs/00-shared-context.md` section 7: D-08, D-11, D-14
- OperationIds: `listCustomers`, `createCustomer`, `getCustomer`, `updateCustomer`, `closeCustomer`, `listCustomerMeters`, `listSites`, `createSite`, `getSite`, `updateSite`, `listPlans`, `createPlan`, `getPlan`, `updatePlan`, `listPlanTariffs`, `createPlanTariff`, `listMeters`, `createMeter`, `getMeter`, `updateMeter`, `decommissionMeter`.

## Global constraints

- Server Actions banned; no secrets in `NEXT_PUBLIC_*`; image optimiser off; nonce CSP on every page.
- Access token in memory only; list pages are client components under a server `page.tsx`.
- Budgets: staff first load at most 250 KB; the registry routes add no heavy library.
- next-intl `/fr` and `/en`; French first; plural messages use ICU plurals.
- WCAG 2.2 AA: tables are native with caption and `aria-sort`; filters apply with an explicit "Appliquer" button; errors in a focused summary.
- Generated client only; types come from `schema.d.ts` (`components['schemas']['Customer']` etc.).
- Updates send `If-Match` with the entity `version` (ETag); a 409 or 412 shows the conflict dialog, never silently overwrites.
- Phone numbers are masked in lists (`+243 ** *** 111`); the full number appears only on the detail page for `support_agent` (the API decides; the UI shows what it receives).
- Money fields use `MoneyText` and `formatMoney`; plan limits are integer minor units.
- Navigation and button visibility use `usePermission(operationId)`; the API enforces.
- Filters live in URL search params; cursor pagination with Previous and Next links, no total count.

## Review focus

1. Two agents edit the same customer; the second silently overwrites: `CustomerForm.test.tsx` "stale version shows conflict dialog" and `e2e/registry.spec.ts` (Task FE-02.2).
2. A second customer with an existing phone number gives a vague error: `CustomerForm.test.tsx` "duplicate phone shows field error on phone" (Task FE-02.2).
3. A decommissioned meter still shows an enabled "assign" or "top-up" action: `MeterDetail.test.tsx` "decommissioned meter hides assign" (Task FE-02.4).
4. The filter bar reloads the table on every keystroke, spamming the API and screen reader: `CustomerList.test.tsx` "filters apply only on Appliquer" (Task FE-02.1).
5. A plan tariff proposal looks like it took effect immediately: `PlanDetail.test.tsx` "tariff proposal shows submitted for approval and no new current tariff" (Task FE-02.5).

## Files

- `frontend/src/features/customers/keys.ts`, `hooks/useCustomers.ts`, `hooks/useCustomer.ts`, `hooks/useCustomerMutations.ts`.
- `frontend/src/features/customers/schemas/customer.ts` (zod form schema).
- `frontend/src/features/customers/components/CustomerList.tsx`, `CustomerFilters.tsx`, `CustomerForm.tsx`, `CustomerDetail.tsx`, `ConflictDialog.tsx`.
- `frontend/src/features/meters/keys.ts`, `hooks/useMeters.ts`, `hooks/useMeterMutations.ts`, `components/MeterList.tsx`, `MeterDetail.tsx`, `MeterForm.tsx`, `AssignMeterDialog.tsx`.
- `frontend/src/features/customers/components/SiteList.tsx`, `SiteForm.tsx`, `PlanList.tsx`, `PlanDetail.tsx`, `TariffForm.tsx` (sites and plans live under the `customers` feature, the backend module that owns them).
- `frontend/src/app/[locale]/(staff)/staff/customers/page.tsx`, `customers/new/page.tsx`, `customers/[id]/page.tsx`.
- `frontend/src/app/[locale]/(staff)/staff/sites/page.tsx`, `sites/[id]/page.tsx`, `plans/page.tsx`, `plans/[id]/page.tsx`, `meters/page.tsx`, `meters/[id]/page.tsx`.
- `frontend/src/shared/lib/pagination.ts` (`useCursorParams`), `frontend/src/shared/lib/mask.ts` (`maskPhone`).
- `frontend/src/i18n/messages/{fr,en}/registry.json`.
- Tests next to components; `frontend/e2e/registry.spec.ts`.

## Interfaces

**Routes (staff):** `/staff/customers`, `/staff/customers/new`, `/staff/customers/[id]`, `/staff/sites`, `/staff/sites/[id]`, `/staff/plans`, `/staff/plans/[id]`, `/staff/meters`, `/staff/meters/[id]`.

**Visibility from `permissions.yaml`:** customers create, edit, close: `support_agent`; read: support, finance, operations. Sites and meters create, edit, decommission: `operations_engineer`; read: support, finance, operations. Plans create, edit, tariff proposal: `finance_officer`; read: support, finance, operations.

**Namespace `registry` (key: fr / en):**

| Key | fr | en |
|---|---|---|
| `registry.customers.title` | Clients | Customers |
| `registry.customers.new` | Nouveau client | New customer |
| `registry.customers.count` | {count, plural, =0 {Aucun client} one {# client} other {# clients}} | {count, plural, =0 {No customers} one {# customer} other {# customers}} |
| `registry.customers.search` | Rechercher par nom, numéro ou téléphone | Search by name, number or phone |
| `registry.customers.apply` | Appliquer | Apply |
| `registry.customers.empty` | Aucun client ne correspond à ces critères. | No customer matches these criteria. |
| `registry.customer.fullName` | Nom complet | Full name |
| `registry.customer.phone` | Téléphone | Phone |
| `registry.customer.status.active` | Actif | Active |
| `registry.customer.status.suspended` | Suspendu | Suspended |
| `registry.customer.status.closed` | Clôturé | Closed |
| `registry.customer.status.erased` | Effacé | Erased |
| `registry.customer.duplicatePhone` | Ce numéro est déjà utilisé par un autre client. | This number is already used by another customer. |
| `registry.conflict.title` | Ce dossier a été modifié par quelqu'un d'autre | Someone else changed this record |
| `registry.conflict.reload` | Recharger la dernière version | Load the latest version |
| `registry.meter.status.provisioned` | Prêt à installer | Provisioned |
| `registry.meter.status.active` | Actif | Active |
| `registry.meter.status.offline` | Hors ligne | Offline |
| `registry.meter.status.disconnected` | Déconnecté | Disconnected |
| `registry.meter.status.decommissioned` | Retiré du service | Decommissioned |
| `registry.meter.duplicateSerial` | Un compteur avec ce numéro de série existe déjà. | A meter with this serial number already exists. |
| `registry.meter.alreadyAssigned` | Ce compteur est déjà attribué à un autre client. | This meter is already assigned to another customer. |
| `registry.tariff.submitted` | Proposition envoyée pour approbation. Le tarif actuel reste inchangé. | Proposal sent for approval. The current tariff is unchanged. |
| `registry.site.health.operational` | En service | Operational |
| `registry.site.health.degraded` | Dégradé | Degraded |
| `registry.site.health.offline` | Hors ligne | Offline |

**Hooks:** `useCustomers(filters: {q?: string; status?: string; site_id?: string; cursor?: string})` returns `{data, meta, isLoading, error}`; `useCustomer(id)`; `useUpdateCustomer(id)` sends `If-Match: version`; `useCreateCustomer()` generates one `Idempotency-Key` per intent if the operation declares it; `useMeters(filters)`; `useUpdateMeter(id)`; `useDecommissionMeter(id)`; `usePlans()`; `useProposeTariff(planId)`. Query keys: `customerKeys.list(filters)`, `customerKeys.detail(id)`, same pattern for `meterKeys`, `siteKeys`, `planKeys`. Stale times: registry 60 s (`docs/03`).

**Components:** `CustomerList`, `CustomerFilters`, `CustomerForm({mode:'create'|'edit', customer?})`, `ConflictDialog({current, onReload})`, `MeterDetail`, `AssignMeterDialog({meter})`, `PlanDetail`, `TariffForm`.

**Operations consumed:** the 21 operationIds under Read first.

## Tasks

### Task FE-02.1: Customer list with filters and cursor pagination

- [ ] **Step 1: failing test** `frontend/src/features/customers/components/CustomerList.test.tsx`:
  ```ts
  test('renders table with caption and masked phone', async () => {
    mockListCustomers([C1, C2]);
    render(<CustomerList/>);
    expect(await screen.findByRole('table',{name:'Clients'})).toBeVisible();
    expect(screen.getByText('+243 ** *** 111')).toBeVisible();
    expect(screen.getByRole('link',{name:'Amani Mbuyi'})).toHaveAttribute('href','/fr/staff/customers/'+C1.id);
  });
  test('filters apply only on Appliquer', async () => {
    await user.type(screen.getByLabelText('Rechercher par nom, numéro ou téléphone'),'Amani');
    expect(listCalls).toBe(1);
    await user.click(screen.getByRole('button',{name:'Appliquer'}));
    await waitFor(()=>expect(listCalls).toBe(2)); expect(lastQuery('q')).toBe('Amani');
  });
  test('empty state', async () => { mockListCustomers([]); render(<CustomerList/>); expect(await screen.findByText('Aucun client ne correspond à ces critères.')).toBeVisible(); });
  test('error state keeps retry and request id', ...);
  test('next link carries cursor', ...);
  ```
- [ ] **Step 2:** `cd frontend && npm test -- CustomerList`. Expected: fail (module missing).
- [ ] **Step 3: implement.** `CustomerList` is a client component reading filters from URL params (`useCursorParams`), calling `useCustomers`. Renders `DataTable` (columns: customer number, full name as a link, masked phone, status as `StatusBadge` with text, site name, updated date via `DateText`); caption includes the count message; Previous and Next are links built from `meta.next_cursor` (a stack of previous cursors kept in `sessionStorage` key `hub-cursor:<route>`, non-sensitive). States: loading (8 skeleton rows), empty (message plus "Nouveau client" button only when `usePermission('createCustomer')`), error (alert with Retry and request id), partial (rows shown, site names unresolved show "Site inconnu"), offline (banner and Apply disabled). `maskPhone('+243810000111')` returns `+243 ** *** 111`.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): customer list with filters and cursor pagination`.

### Task FE-02.2: Customer create, edit and conflict handling (TC-FR-01-4)

- [ ] **Step 1:** `CustomerForm.test.tsx`:
  ```ts
  test('creates customer and navigates to detail', ...);
  test('duplicate phone shows field error on phone', async () => {
    server.use(post409Or422('/customers',{code:'duplicate_phone', errors:[{field:'/phone_e164',code:'duplicate',message:'x'}]}));
    await fillAndSubmit(); expect(screen.getByRole('alert')).toHaveTextContent('Ce numéro est déjà utilisé par un autre client.');
    expect(screen.getByLabelText('Téléphone')).toHaveAccessibleDescription(/déjà utilisé/); expect(screen.getByLabelText('Téléphone')).toBeInvalid();
  });
  test('stale version shows conflict dialog', async () => {
    server.use(put412('/customers/'+C1.id,{code:'version_conflict'}));
    await editNameAndSave(); expect(screen.getByRole('dialog',{name:'Ce dossier a été modifié par quelqu\'un d\'autre'})).toBeVisible();
    await user.click(screen.getByRole('button',{name:'Recharger la dernière version'})); expect(refetchCalls).toBe(1);
  });
  test('sends If-Match with version', ...);
  test('validation: phone must be E.164', ...);
  ```
  `e2e/registry.spec.ts` "two agents edit the same customer" (second context saves after first) asserts the dialog in the second.
- [ ] **Step 2:** `npm test -- CustomerForm`. Expected: fail.
- [ ] **Step 3: implement** `CustomerForm` with react-hook-form and zod schema in `schemas/customer.ts` (full_name 2 to 120 chars, `phone_e164` regex `^\+[1-9][0-9]{7,14}$`, optional email, locale fr/en, `site_id` and `plan_id` from `useSites` and `usePlans` selects). Submit errors map by JSON pointer from `ApiError.fieldErrors` to fields; code `duplicate_phone` or a 409 on `/phone_e164` sets the phone error text from `registry.customer.duplicatePhone`. Edit mode sends `If-Match` from `customer.version`; 409 or 412 opens `ConflictDialog` (does not close the form, keeps typed values, offers reload). Status change only between `active` and `suspended`; "Clôturer le client" calls `closeCustomer` after a confirmation dialog. All buttons hidden unless `usePermission` allows.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): customer create and edit with conflict dialog`.

### Task FE-02.3: Customer detail with wallet-free summary and meters

- [ ] **Step 1:** `CustomerDetail.test.tsx`:
  ```ts
  test('shows profile, status badge text and meters list', ...);
  test('operations engineer sees pseudonymous view without phone', async () => {
    renderAs('operations_engineer'); expect(screen.queryByText(/\+243/)).toBeNull();
  });
  test('erased customer shows read-only notice', () => expect(screen.getByText('Ce dossier a été effacé.')).toBeVisible());
  test('meters error does not hide profile (partial)', ...);
  ```
- [ ] **Step 2:** `npm test -- CustomerDetail`. Expected: fail.
- [ ] **Step 3: implement** `CustomerDetail` using `useCustomer(id)` and `useCustomerMeters(id)` (`listCustomerMeters`); sections: identity, status, assigned meters table (links to meter pages), actions (Edit, Close) by permission. Finance sees no wallet here (wallet is in fe-03). Render only fields present in the response (the API already withholds fields by role). `status === 'erased'` disables every action and shows `registry.customer.erasedNotice` (fr "Ce dossier a été effacé.", en "This record has been erased."). Partial state: meters query failing shows an inline alert with Retry while the profile stays.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): customer detail with meters`.

### Task FE-02.4: Sites and meters (assign, decommission)

- [ ] **Step 1:** `MeterDetail.test.tsx`, `MeterForm.test.tsx`, `SiteList.test.tsx`:
  ```ts
  test('duplicate serial shows field error', ...);       // registry.meter.duplicateSerial
  test('assigning a meter owned by another customer shows alreadyAssigned', ...);
  test('decommissioned meter hides assign and shows read-only badge', () => {
    expect(screen.queryByRole('button',{name:'Attribuer'})).toBeNull(); expect(screen.getByText('Retiré du service')).toBeVisible();
  });
  test('decommission needs typed serial confirmation', async () => { await user.click(btn('Retirer du service')); expect(confirmBtn()).toBeDisabled(); await user.type(input(),'SGM-0003912'); expect(confirmBtn()).toBeEnabled(); });
  test('site health shown as text plus icon', ...);
  ```
- [ ] **Step 2:** `npm test -- features/meters features/customers/components/SiteList`. Expected: fail.
- [ ] **Step 3: implement** `MeterList` (filters site, status, customer), `MeterForm` (serial, site, hardware model, firmware), `MeterDetail` (shows `credit_remaining_wh` formatted as kWh with one decimal via `format.number`, `last_seen_at` with `DateText` relative plus absolute, status badge), `AssignMeterDialog` (customer search select calling `listCustomers`, `updateMeter({customer_id})` with `If-Match`; 409 `meter_already_assigned` shows `registry.meter.alreadyAssigned`), decommission through `decommissionMeter` after typed serial confirmation. `SiteList`, `SiteForm` (code, name, IANA timezone select, optional coordinates and capacity), site `health` badge (read only, computed). All mutations hidden unless `usePermission` allows (`createMeter`, `updateMeter`, `decommissionMeter`, `createSite`, `updateSite` are operations engineer only).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): sites and meters registry screens`.

### Task FE-02.5: Plans and tariff proposals

- [ ] **Step 1:** `PlanDetail.test.tsx`:
  ```ts
  test('tariff proposal shows submitted for approval and no new current tariff', async () => {
    server.use(http.post('*/api/v1/plans/'+P.id+'/tariffs', () => HttpResponse.json({data:APPROVAL},{status:202})));
    await proposeTariff(); expect(screen.getByRole('status')).toHaveTextContent('Proposition envoyée pour approbation. Le tarif actuel reste inchangé.');
    expect(screen.getByTestId('current-tariff')).toHaveTextContent(P.current_tariff_display);
  });
  test('min and max shown with MoneyText and CDF exactness', ...);
  test('non-finance roles see read-only plan', ...);
  test('step-up replay for tariff proposal', ...);   // uses withStepUp from FE-01
  ```
  `e2e/registry.spec.ts` adds "finance officer proposes tariff and sees pending notice".
- [ ] **Step 2:** `npm test -- PlanDetail`; `npm run e2e -- registry.spec.ts`. Expected: fail.
- [ ] **Step 3: implement** `PlanList`, `PlanDetail` (current tariff, tariff history from `listPlanTariffs` as a table with `effective_from`), `TariffForm` (fields as in the generated `TariffCreate`; read the exact field names from `schema.d.ts`; show the maximum allowed change per revision if the API returns it in a validation error), submit through `withStepUp` then `handleApprovalResult` from FE-01.4. Plan create and update only for `finance_officer`.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): plans and tariff proposals through approval`.

## Verification

From `frontend/`:
1. `npm run lint && npm run typecheck` exit 0.
2. `npm test` passes (registry suites included).
3. `npm run i18n:check` exits 0 (`registry` keys equal in both locales).
4. `npm run build && npm run size` exit 0 (staff at most 250 KB per route).
5. `npm run e2e -- registry.spec.ts` passes (mock mode); with `make up` and backend slice 2, `E2E_MODE=stack npm run e2e -- registry.spec.ts` passes.
6. `npm run e2e:a11y -- --grep registry` zero serious or critical violations for `/fr/staff/customers`, `/staff/meters`, `/staff/plans` in both locales.

## Demo script

1. Sign in as a support agent. Open `/fr/staff/customers`: the table caption reads "Clients"; EN `/en/staff/customers`: "Customers".
2. Press "Nouveau client" / "New customer", enter name, `+243810000111`, site and plan, save: you land on the detail page.
3. Create a second customer with the same phone: FR "Ce numéro est déjà utilisé par un autre client." EN "This number is already used by another customer."
4. Open the same customer in a second browser, save a change in each: the second shows "Ce dossier a été modifié par quelqu'un d'autre" / "Someone else changed this record" and the reload button.
5. Sign in as an operations engineer: create a meter, assign it to a customer; try to assign it to another: "Ce compteur est déjà attribué à un autre client." / "This meter is already assigned to another customer."
6. Decommission a meter by typing its serial: it shows "Retiré du service" / "Decommissioned" and the assign button disappears.
7. Sign in as finance: propose a tariff on a plan: "Proposition envoyée pour approbation. Le tarif actuel reste inchangé." / "Proposal sent for approval. The current tariff is unchanged."

## Done criteria

- Requirements: FR-01 (registry screens), FR-16, NFR-10, NFR-11.
- Test cases from `docs/10-test-strategy.md` section 8 (UI contribution): TC-FR-01-1 (create and read back), TC-FR-01-2 (duplicate serial and concurrent assignment errors shown), TC-FR-01-4 (duplicate phone and stale version).

## Plan notes for the tech lead

- **Tariff proposals need `finance_lead`:** `createPlanTariff` carries `x-requires-attribute: finance_lead` in `api/openapi.yaml`; the tariff form shows only to a finance officer with that attribute (the generated `can()` does this, fe-00). Plan creation and update have no attribute requirement.
- **Folder ownership:** `docs/03` has no `sites` or `plans` feature folder; they sit in `features/customers` because the `customers` module owns them. Moving them to their own folders later is a rename only.
- **Tariff form fields:** `TariffCreate` fields were not read in detail; the executor takes them from the generated types. The maximum change per revision (D-08) is not a documented API field; the UI shows whatever validation error text the API returns.
- **TC-FR-01-3** (decommissioned meter refused for top-up via UI) needs the portal top-up screen and is covered in fe-04.
- **Customer wallet and balance** are read by finance in fe-03, not here.
