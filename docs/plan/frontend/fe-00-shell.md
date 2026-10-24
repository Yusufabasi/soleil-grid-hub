# Frontend slice 00: Shell, design system, API client and guard rails

- **Goal:** a runnable `hub-web` (Next.js App Router, TypeScript strict) with the three route groups, nonce CSP middleware, `/fr` and `/en` routing, a server-rendered public landing page, the design tokens and base components, the money and date formatters, the generated API client and every CI guard that later slices rely on.
- **Depends on:** backend slice 0 (walking skeleton: `getLiveness`, `getReadiness`, `getMe`, compose stack with the `be_web` pool). No earlier frontend slice.
- **Requirements served:** FR-16 (shell), NFR-10 (base components), NFR-11 (formatters, pseudo-locale), NFR-02 (budget gate), NFR-06 (CSP, Server Actions, image optimiser, `NEXT_PUBLIC_` guard), NFR-14 (one-command checks).
- **Size:** L, 9 days.
- **Spikes or open items:** (1) **Bundle-budget spike (D-44, open):** measure the real first-load JS of a vertical slice against 130 KB (portal) and 250 KB (staff); Task FE-00.9. (2) **Nonce CSP rendering cost (D-48, open):** measure server render time and time to first byte of public pages when every page is dynamic; Task FE-00.9. Defaults until measured: budgets as stated; no HTML cache.

## Read first

- `docs/08-frontend-ux.md#2.1 One Next.js application, three route groups (ADR-0021, D-44)`
- `docs/08-frontend-ux.md#5.2 Design system and tokens`
- `docs/08-frontend-ux.md#5.7 Routing, error boundaries and role guards`
- `docs/08-frontend-ux.md#5.8 API client generated from api/openapi.yaml`
- `docs/08-frontend-ux.md#6. Internationalisation (NFR-11, FR-16)` (6.1 to 6.4)
- `docs/08-frontend-ux.md#8. Performance, offline, images and fonts` (8.1 to 8.3, 8.5)
- `docs/08-frontend-ux.md#9.1 Security notes`
- `docs/03-code-architecture.md#8. Frontend structure (Next.js, hub-web)` (the whole section; it fixes the folder layout)
- `docs/00-shared-context.md` section 7: D-44, D-46, D-48
- OperationIds used: `getLiveness`, `getReadiness` (probe only). All other operations arrive in later slices.

## Global constraints

- Server Actions are banned: no `'use server'` anywhere; middleware answers 404 to any request carrying a `Next-Action` header (D-46).
- No secret in any `NEXT_PUBLIC_*` variable; the only allowed names are `NEXT_PUBLIC_SITE_ORIGIN`, `NEXT_PUBLIC_API_BASE`, `NEXT_PUBLIC_DEFAULT_LOCALE` (D-44).
- Image optimiser off (`images.unoptimized: true`); no remote images; no `next/font/google`.
- Every response carries a per-request CSP nonce set in `middleware.ts`; no inline script or style without it (D-44, D-48); no `unsafe-inline`, no `unsafe-eval` in production.
- Access token only in memory (`src/api/auth-store.ts`); never in `localStorage`, `sessionStorage`, cookies or URLs.
- Budgets: portal first load at most 130 KB gzipped, staff app at most 250 KB (D-44), enforced by `npm run size`.
- next-intl for `/fr` and `/en`; French is the default and is written first; no hard-coded user-visible string.
- WCAG 2.2 AA (NFR-10): native elements first, visible focus ring, targets at least 44 px (portal 48 px), errors linked with `aria-describedby`.
- Use only the generated client; no handwritten API types; no `fetch` outside `src/api/`.
- Money is integer minor units plus currency; formatted only by `shared/format/money.ts`; never optimistic.
- Roles and navigation come from `api/permissions.yaml` through a generated file; hiding is a convenience, the API enforces.
- Do not edit frozen files (`api/*.yaml`, `db/*`, `docs/00-shared-context.md`, `docs/adr/*`); raise a change request instead.

## Review focus

1. A Server Action or `Next-Action` header slips through: `middleware.test.ts` "returns 404 for Next-Action" and `check:rules` grep (Task FE-00.2, FE-00.8).
2. The CSP blocks Next's own inline bootstrap, so the page is blank in production: `e2e/csp.spec.ts` "page hydrates with nonce CSP and no console CSP errors" (Task FE-00.3).
3. French strings missing or with wrong punctuation ship silently: `npm run i18n:check` (Task FE-00.3).
4. A CDF amount loses its centimes on a money screen: `money.test.ts` "keeps non-zero minor units" (Task FE-00.6).
5. The shared chunk grows until the portal blows its budget unnoticed: `npm run size` with per-route table (Task FE-00.8, FE-00.9).

## Files

All paths are under `frontend/` in the repository root.

- `frontend/package.json`: scripts `dev`, `build`, `start`, `lint`, `typecheck`, `test`, `gen:api`, `gen:permissions`, `i18n:check`, `check:rules`, `size`, `e2e`, `e2e:a11y`.
- `frontend/next.config.ts`: `output: 'standalone'`, `images.unoptimized`, `poweredByHeader: false`, `reactStrictMode`, `typedRoutes`, security headers except CSP.
- `frontend/tsconfig.json`, `frontend/.eslintrc.cjs` (or `eslint.config.mjs`), `frontend/vitest.config.ts`, `frontend/playwright.config.ts`, `frontend/.env.example`.
- `frontend/middleware.ts`: Next-Action 404, next-intl routing, nonce CSP.
- `frontend/src/i18n/routing.ts`, `request.ts`, `navigation.ts`: locales, message loading, locale-aware `Link`, `useRouter`, `redirect`.
- `frontend/src/i18n/messages/fr/common.json`, `.../en/common.json`, `public.json`, `errors.json` (both locales).
- `frontend/src/app/[locale]/layout.tsx`: `<html lang>`, provider, skip link, nonce use.
- `frontend/src/app/[locale]/(public)/page.tsx`: landing page (server component) with login choice links.
- `frontend/src/app/[locale]/(public)/help/[slug]/page.tsx`, `tariffs/page.tsx`, `status/page.tsx`: server pages using `src/api/server.ts` (empty-state placeholders until later slices).
- `frontend/src/app/[locale]/(portal)/portal/layout.tsx`, `loading.tsx`, `error.tsx`: server shell with a placeholder `<ClientAuthGate/>`.
- `frontend/src/app/[locale]/(staff)/staff/layout.tsx`, `loading.tsx`, `error.tsx`: same for staff.
- `frontend/src/app/error.tsx`, `global-error.tsx`, `not-found.tsx`.
- `frontend/src/api/generated/schema.d.ts` (generated, committed), `client.ts`, `auth-store.ts`, `problem.ts`, `server.ts`.
- `frontend/src/shared/ui/`: `Button.tsx`, `IconButton.tsx`, `Field.tsx`, `Dialog.tsx`, `DataTable.tsx`, `StatusBadge.tsx`, `LiveRegion.tsx`, `Skeleton.tsx`, `AiBadge.tsx`, `MoneyText.tsx`, `DateText.tsx`, `index.ts`.
- `frontend/src/shared/styles/tokens.css`, `themes.css`, `globals.css`.
- `frontend/src/shared/format/money.ts`, `dates.ts`, `index.ts`.
- `frontend/src/shared/lib/permissions.generated.ts` (generated), `permissions.ts` (`can`).
- `frontend/scripts/gen-permissions.mjs`, `check-rules.mjs`, `check-size.mjs`, `i18n-check.mjs`.
- Tests next to code (`*.test.ts(x)`) and `frontend/e2e/csp.spec.ts`, `frontend/e2e/public.spec.ts`, `frontend/e2e/support/mock-api.ts`.

## Interfaces

**Routes (no locale prefix shown; every URL is `/fr/...` or `/en/...`):** `/` (public landing), `/help/[slug]`, `/tariffs`, `/status`, `/portal/*` (placeholder), `/staff/*` (placeholder).

**Message namespaces:** `common` (nav, buttons, states), `public` (landing, help, tariffs, status), `errors` (keyed by problem `code`).

Keys created in this slice (French first, then English):

| Key | fr | en |
|---|---|---|
| `common.skipToContent` | Aller au contenu principal | Skip to main content |
| `common.state.loading` | Chargement en cours | Loading |
| `common.state.retry` | Réessayer | Try again |
| `common.state.offline` | Vous êtes hors connexion. Dernière mise à jour : {time} | You are offline. Last updated: {time} |
| `common.error.title` | Une erreur est survenue | Something went wrong |
| `common.error.requestId` | Référence à communiquer au support : {id} | Reference to give to support: {id} |
| `common.notFound.title` | Page introuvable | Page not found |
| `common.language.switch` | Changer de langue | Change language |
| `public.landing.title` | Soleil Grid : votre énergie solaire prépayée | Soleil Grid: your prepaid solar energy |
| `public.landing.customerLogin` | Espace client | Customer area |
| `public.landing.staffLogin` | Espace personnel | Staff area |
| `errors.unknown` | Une erreur inattendue s'est produite. Réessayez. | An unexpected error occurred. Please try again. |
| `errors.rate_limited` | Trop de tentatives. Réessayez dans {seconds} secondes. | Too many attempts. Try again in {seconds} seconds. |

**Components (`shared/ui`):** `Button({variant:'primary'|'secondary'|'danger', loading?, ...native})` (loading sets `aria-disabled`, keeps focus); `IconButton({label: string, icon})` (`label` required by type); `Field({id,label,hint?,error?,children})`; `Dialog({open,onClose,title,children,initialFocus?})` on native `<dialog>`; `DataTable<T>({caption, columns, rows, sort?, onSort?})`; `StatusBadge({tone:'ok'|'warn'|'bad'|'info'|'neutral', icon, children})`; `LiveRegion` (single mounted per shell, `announce(message, 'polite'|'assertive')`); `Skeleton({label})`; `AiBadge({kind:'suggestion'|'draft'|'sql'})`; `MoneyText({money, exact?})`; `DateText({iso, zone?, relative?})`.

**Formatters:** `formatMoney(m: Money, locale: 'fr'|'en', opts?: {exact?: boolean; showCode?: boolean}): string`; `formatDate(iso: string, locale, opts?: {zone?: string; withTime?: boolean}): string`.

**Client:** `api` (openapi-fetch client created by `createApiClient()`), `ApiError {code, status, requestId, fieldErrors, stepUp?, approvalId?}`, `getToken()`, `setToken(t)`, `clearToken()`, `subscribeSession(cb)` in `auth-store.ts`. Envelope rule: success bodies are `{ data: ... }` (lists add `meta: PageMeta`); the wrapper returns `data` and `meta`.

**Operations consumed:** `getLiveness`, `getReadiness` (e2e probe only).

## Tasks

### Task FE-00.1: Project scaffold and Next.js configuration

- [ ] **Step 1: failing test.** Create `frontend/src/config.test.ts`:
  ```ts
  import cfg from '../next.config';
  test('hardened config', () => {
    expect(cfg.images?.unoptimized).toBe(true);
    expect(cfg.output).toBe('standalone');
    expect(cfg.poweredByHeader).toBe(false);
    expect(cfg.typedRoutes).toBe(true);
    expect(JSON.stringify(cfg)).not.toContain('serverActions');
  });
  ```
- [ ] **Step 2:** `cd frontend && npm test -- src/config.test.ts`. Expected: fails (no config file).
- [ ] **Step 3: implement.** Create the project with Next.js (latest stable 15.x, App Router), React, TypeScript strict, ESLint (`next/core-web-vitals`, `jsx-a11y`, `eslint-plugin-boundaries`), Prettier, Vitest with jsdom and Testing Library, Playwright, `openapi-typescript`, `openapi-fetch`, `next-intl`, `@tanstack/react-query`, `zod`, `react-hook-form`. Write `next.config.ts` as listed under Files; `headers()` adds `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`, `Permissions-Policy` (camera, microphone, geolocation off; `publickey-credentials-get` and `-create` self). Add `.env.example` listing exactly `NEXT_PUBLIC_SITE_ORIGIN`, `NEXT_PUBLIC_API_BASE=/api/v1`, `NEXT_PUBLIC_DEFAULT_LOCALE=fr`, `HUB_API_INTERNAL_URL`, `HUB_CSP_REPORT_URI`. Add `package.json` scripts named under Files.
- [ ] **Step 4:** run the test until it passes; run `npm run typecheck`.
- [ ] **Step 5: commit** `feat(web): scaffold hub-web with hardened Next.js config`.

### Task FE-00.2: Middleware (Next-Action, locale, nonce CSP)

- [ ] **Step 1:** `frontend/middleware.test.ts` with `NextRequest` objects:
  ```ts
  test('404 for Next-Action', async () => {
    const res = await middleware(req('/fr', { 'next-action': 'abc' }));
    expect(res.status).toBe(404);
  });
  test('fresh nonce per request', async () => {
    const a = csp(await middleware(req('/fr'))); const b = csp(await middleware(req('/fr')));
    expect(a.nonce).not.toEqual(b.nonce);
    expect(a.header).toContain("script-src 'self' 'nonce-" + a.nonce + "' 'strict-dynamic'");
    expect(a.header).not.toContain('unsafe-inline');
    expect(a.header).toContain("frame-ancestors 'none'");
  });
  test('redirects / to /fr by default', async () => {
    const res = await middleware(req('/')); expect(res.headers.get('location')).toMatch(/\/fr$/);
  });
  ```
- [ ] **Step 2:** `npm test -- middleware.test.ts`. Expected: fails (module missing).
- [ ] **Step 3: implement** `middleware.ts` per `docs/03` section 8 "Middleware": (1) Next-Action header gives `new NextResponse(null,{status:404})`; (2) call the next-intl middleware (locales `fr`, `en`; default `fr`; `localePrefix: 'always'`; preference from cookie `hub_locale`, then `Accept-Language`); (3) generate 16 random bytes, base64 as nonce, set request header `x-nonce`, set `Content-Security-Policy` exactly as docs/08 section 9.1 (CSP row) with `report-uri` from `process.env.HUB_CSP_REPORT_URI` when set. Matcher excludes `/_next/static`, `/_next/image`, files with extensions. Signature: `middleware(req: NextRequest): Promise<NextResponse>`.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): nonce CSP, locale routing and Server Action block in middleware`.

### Task FE-00.3: Locale layout, public pages, error and loading boundaries

- [ ] **Step 1:** write `frontend/e2e/public.spec.ts` (Playwright, mock mode) and `frontend/scripts/i18n-check.test.ts`:
  ```ts
  test('landing renders in fr and en with axe clean', async ({ page }) => {
    for (const loc of ['fr','en']) {
      await page.goto(`/${loc}`);
      await expect(page.locator('html')).toHaveAttribute('lang', loc);
      await expect(page.getByRole('heading', {level:1})).toBeVisible();
      const r = await new AxeBuilder({ page }).withTags(['wcag2a','wcag2aa','wcag22aa']).analyze();
      expect(r.violations.filter(v=>['serious','critical'].includes(v.impact!))).toEqual([]);
    }
  });
  test('unknown route shows localised not-found', async ({page}) => {
    await page.goto('/fr/nope'); await expect(page.getByRole('heading',{name:'Page introuvable'})).toBeFocused();
  });
  ```
  `i18n-check.test.ts`: fixture with a missing key in `fr` makes `i18nCheck()` return a non-empty `missing` list.
- [ ] **Step 2:** `npm run build && npm run e2e -- public.spec.ts`; `npm test -- scripts`. Expected: fail (pages absent).
- [ ] **Step 3: implement** all `app/[locale]` files in Files. `[locale]/layout.tsx` is a server component: validates the locale (unknown gives `notFound()`), sets `<html lang>`, reads the nonce with `headers().get('x-nonce')`, mounts `NextIntlClientProvider` with only namespaces `common` and `public`, renders a skip link first and `<LiveRegion/>`. `(public)/page.tsx` renders `<h1>`, two links (customer, staff) with `Link` from `src/i18n/navigation.ts`. Public data pages call `src/api/server.ts` `publicFetch(path, {revalidate: 300})` and render empty state when the API has no data. `error.tsx` (client) shows title, request id, a `Button` calling `reset()`, moves focus to its heading in `useEffect`. `loading.tsx` renders `<Skeleton label=.../>`. `not-found.tsx` focuses its `<h1>` (via a tiny client component). `scripts/i18n-check.mjs` compares key sets and ICU argument names between `fr` and `en` for every namespace file and exits 1 on any difference.
- [ ] **Step 4:** run until pass; also `npm run i18n:check` returns exit 0.
- [ ] **Step 5: commit** `feat(web): locale layout, public landing and error boundaries`.

### Task FE-00.4: Tokens, themes and form primitives

- [ ] **Step 1:** `frontend/src/shared/styles/tokens.contrast.test.ts` parses `tokens.css` and asserts WCAG ratios:
  ```ts
  test.each([
    ['--text','--bg',15],['--text-muted','--bg',7],['--primary','--bg',6],
    ['--success','--bg',5.5],['--warning','--bg',5.5],['--danger','--bg',5.5],
    ['--border-control','--bg',3],['--focus-ring','--bg',3],
  ])('%s on %s >= %s in light and dark', (fg,bg,min) => {
    for (const theme of ['light','dark']) expect(ratio(tok(theme,fg), tok(theme,bg))).toBeGreaterThanOrEqual(min);
  });
  ```
  And `Field.test.tsx`: label wired (`getByLabelText('Montant')`), error text linked (`toHaveAccessibleDescription('Entrez un montant valide')`), `aria-invalid` set.
- [ ] **Step 2:** `npm test -- src/shared`. Expected: fails.
- [ ] **Step 3: implement** `tokens.css` with the exact values of docs/08 section 5.2 as CSS custom properties for `:root[data-theme=light]` and `[data-theme=dark]`, plus `@media (forced-colors: active)` rules using system colours; theme chosen from cookie `hub_theme` (server reads it in the locale layout and writes `data-theme`), default by `prefers-color-scheme`. `Button`, `IconButton`, `Field` per Interfaces. Minimum control height 44 px (48 px inside `.portal` wrapper class). Focus ring 2 px with 2 px offset on `:focus-visible`; stylelint rule bans `outline: none` without replacement.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): design tokens, themes, Button, IconButton and Field`.

### Task FE-00.5: Dialog, DataTable, StatusBadge, LiveRegion, Skeleton, AiBadge

- [ ] **Step 1:** component tests with `vitest-axe`:
  ```ts
  test('Dialog traps focus and returns it', async () => {
    render(<Harness/>); await user.click(screen.getByRole('button',{name:'Ouvrir'}));
    expect(screen.getByRole('dialog')).toBeVisible();
    await user.keyboard('{Escape}'); expect(screen.getByRole('button',{name:'Ouvrir'})).toHaveFocus();
  });
  test('DataTable has caption, scoped headers, aria-sort', async () => {
    render(<DataTable caption="Demandes ouvertes" .../>);
    expect(screen.getByRole('table',{name:'Demandes ouvertes'})).toBeVisible();
    expect(screen.getAllByRole('columnheader')[0]).toHaveAttribute('aria-sort','ascending');
  });
  test('LiveRegion announces once', ...); test('AiBadge text and aria-label', () => expect(screen.getByLabelText('Généré par IA')).toBeVisible());
  test('StatusBadge never relies on colour alone', () => expect(screen.getByText('Échec')).toBeVisible());
  ```
  Each also `expect(await axe(container)).toHaveNoViolations()`.
- [ ] **Step 2:** run `npm test -- src/shared/ui`. Expected: fail.
- [ ] **Step 3: implement.** `Dialog` wraps `<dialog>` with `showModal()`, closes on Escape, restores focus to the invoker. `DataTable` is a native table: `<caption>` required, sortable header is a `<button>` inside `<th scope="col" aria-sort>`, wide tables inside `role="region" tabIndex={0} aria-label`. `LiveRegion` exposes `useAnnounce()`; regions exist at first render (polite status and assertive alert). `Skeleton` is `aria-hidden` plus one visually hidden label and `aria-busy` parent; no shimmer under `prefers-reduced-motion`. `AiBadge` shows icon plus text (`ai.badge.suggestion`, `.draft`, `.sql` in `common`): fr "Suggestion de l'IA", "Rédigé par l'IA", "SQL écrit par l'IA".
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): accessible Dialog, DataTable, LiveRegion and badges`.

### Task FE-00.6: Money and date formatters (TC-NFR-11-1, TC-NFR-11-3)

- [ ] **Step 1:** `frontend/src/shared/format/money.test.ts`:
  ```ts
  const n = (s:string)=>s.replace(/[  ]/g,' ');
  test('USD fr and en', () => {
    expect(n(formatMoney({amount_minor:1250,currency:'USD'},'fr'))).toBe('12,50 $US');
    expect(formatMoney({amount_minor:1250,currency:'USD'},'en')).toBe('$12.50');
  });
  test('CDF whole francs only when no minor units and over 100 FC', () => {
    expect(n(formatMoney({amount_minor:2800000,currency:'CDF'},'fr'))).toMatch(/^28 000 (FC|CDF)$/);
    expect(n(formatMoney({amount_minor:2800050,currency:'CDF'},'fr'))).toMatch(/^28 000,50 (FC|CDF)$/);
  });
  test('exact keeps decimals', () => expect(n(formatMoney({amount_minor:2800000,currency:'CDF'},'fr',{exact:true}))).toMatch(/28 000,00/));
  test('keeps non-zero minor units', ...);
  test('rejects non-integer', () => expect(()=>formatMoney({amount_minor:1.5,currency:'USD'},'fr')).toThrow());
  ```
  `dates.test.ts`: `formatDate('2026-10-12T12:05:00Z','fr',{zone:'Africa/Lubumbashi',withTime:true})` matches `/^12 oct\. 2026,? 14:05$/` after space normalisation; same instant in `en` contains `Oct 12, 2026`.
- [ ] **Step 2:** run `npm test -- src/shared/format`. Expected: fail.
- [ ] **Step 3: implement** per Interfaces using `Intl.NumberFormat` and `Intl.DateTimeFormat` only; fraction digits read from `resolvedOptions()`; throw `RangeError` unless `Number.isSafeInteger(amount_minor)`; `showCode` replaces the symbol with the ISO code; `exact` or any money-moving context shows full fraction digits. `MoneyText` and `DateText` call these (client or server).
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): locale-aware money and date formatting`.

### Task FE-00.7: Generated API client, problem parser, token store, permissions generator

- [ ] **Step 1:** `frontend/src/api/client.test.ts` using MSW:
  ```ts
  test('attaches bearer, request id, locale and unwraps data', async () => {
    setToken('t1'); server.use(http.get('*/api/v1/me', ({request}) => {
      expect(request.headers.get('authorization')).toBe('Bearer t1');
      expect(request.headers.get('x-request-id')).toMatch(/^[0-9a-f-]{36}$/);
      expect(request.headers.get('accept-language')).toBe('fr');
      return HttpResponse.json({data:{id:'u1',type:'customer',display_name:'A',roles:['customer'],locale:'fr'}});
    }));
    const r = await api.GET('/me'); expect(r.data?.id).toBe('u1');
  });
  test('parses problem+json', async () => { /* 422 -> ApiError.code 'validation_error', fieldErrors[0].field '/amount/amount_minor' */ });
  test('only refresh and logout send credentials', ...);
  ```
  `permissions.test.ts`: `can({roles:['administrator']},'listTickets')` is false; `can({roles:['support_agent']},'approveDraft')` is true; `can({roles:['administrator']},'askTheData')` is false.
- [ ] **Step 2:** `npm run gen:api && npm test -- src/api src/shared/lib`. Expected: fails until implemented.
- [ ] **Step 3: implement.** `gen:api` runs `openapi-typescript ../api/openapi.yaml -o src/api/generated/schema.d.ts`; CI drift check compares git diff. `client.ts`: `createClient<paths>({baseUrl:'/api/v1', credentials:'omit'})` plus middleware adding `Authorization`, `X-Request-Id` (UUID), `Accept-Language` (active locale from `document.documentElement.lang`), `Idempotency-Key` when a call passes `idempotencyKey`. Responses: non-2xx parsed by `problem.ts` into `ApiError`; a 401 with `step_up_required` throws `ApiError` with `stepUp` (replay is added in FE-01). `server.ts` exports `publicFetch(path, {revalidate})` using `HUB_API_INTERNAL_URL`, no credentials, imported only from server components. `scripts/gen-permissions.mjs` reads `../api/permissions.yaml` and, for attributes, the `x-requires-attribute` entries of `../api/openapi.yaml` (for example `createRefund` requires `finance_lead`, `createDataExport` requires `dp_admin`), and writes `permissions.generated.ts` (`OPERATION_ROLES: Record<OperationId, Role[]>` and `OPERATION_ATTRIBUTES: Partial<Record<OperationId, Attribute>>`); `permissions.ts` exports `can(principal, operationId)` (role match and, when the operation has a required attribute, the principal's `attributes` must include it) and `canAny(principal, ids)`. Add to the test: `can({roles:['finance_officer'],attributes:[]},'createRefund')` is false; with `attributes:['finance_lead']` it is true.
- [ ] **Step 4:** run until pass.
- [ ] **Step 5: commit** `feat(web): generated API client, problem parser, token store and permissions`.

### Task FE-00.8: Lint rules, CI guards and size check

- [ ] **Step 1:** `frontend/scripts/check-rules.test.ts` runs `checkRules(fixtureDir)` on fixtures:
  ```ts
  test.each([
    ['use-server.tsx', /use server/], ['public-env.ts', /NEXT_PUBLIC_API_SECRET/],
    ['server-imports-client.tsx', /api\/client/], ['raw-fetch.ts', /fetch\(/],
    ['inline-style.tsx', /dangerouslySetInnerHTML/],
  ])('%s is rejected', (f, re) => expect(checkRules(`fixtures/${f}`).join()).toMatch(re));
  ```
  `check-size.test.ts`: given a fake `next build` route table with the portal route at 131 KB, `checkSize()` returns failure naming the route; at 129 KB passes.
- [ ] **Step 2:** `npm test -- scripts`. Expected: fail.
- [ ] **Step 3: implement.** `check-rules.mjs` fails on: `"use server"`; `NEXT_PUBLIC_` names other than the three allowed or containing SECRET, KEY, TOKEN, PASSWORD; any import of `api/client` or `auth-store` or `features/*/hooks` from a file without `'use client'` under `src/app`; `fetch(` outside `src/api/`; `dangerouslySetInnerHTML` outside `src/shared/ui/SafeMarkdown.tsx`; remote `src` in `next/image`. Add ESLint custom rules `no-use-server` and `server-client-boundary` wrapping the same checks. `check-size.mjs` parses the "First Load JS" column of `next build` output: portal routes (`/[locale]/portal/**`) at most 130 KB, staff routes (`/[locale]/staff/**`) at most 250 KB, public routes at most 90 KB, any single route at most 400 KB total; prints a table and exits 1 on breach. `npm run lint` runs ESLint, stylelint and `check:rules`.
- [ ] **Step 4:** run until pass; `npm run lint` clean.
- [ ] **Step 5: commit** `chore(web): rules guard, size budget and lint wiring`.

### Task FE-00.9: Spikes (bundle budget and nonce render cost)

- [ ] **Step 1: failing check.** Add `frontend/e2e/perf-smoke.spec.ts` asserting from `npm run size` JSON output: `portalFirstLoadKb <= 130` and `staffFirstLoadKb <= 250`, and a Lighthouse CI config `frontend/lighthouserc.json` (throttling: 1.6 Mbps down, 750 kbps up, 150 ms RTT, 4x CPU slowdown; URLs `/fr`, `/fr/portal`, `/fr/staff`) with assertions `largest-contentful-paint <= 2500`, `total-blocking-time <= 300`, `cumulative-layout-shift <= 0.05`.
- [ ] **Step 2:** `npm run build && npm run size -- --json && npx lhci autorun`. Expected: either pass (record numbers) or fail (record by how much).
- [ ] **Step 3: execute the spike.** Build a throwaway vertical slice using only files from FE-00 (portal placeholder page with `QueryClientProvider`, `NextIntlClientProvider`, one `Button`, one `Field`; staff placeholder with `DataTable`). Record in `docs/plan/progress.md` under "Spikes": first-load KB per route group, shared chunk size, TTFB and server render milliseconds of `/fr` over 200 requests (use `autocannon` against `next start`), with and without nonce CSP. Decision rule: if portal first load is at most 130 KB and staff at most 250 KB, keep budgets and mark D-44 spike closed in `progress.md`; if over, open `docs/plan/change-requests/CR-NNN-bundle-budget.md` proposing the smallest change (drop `react-hook-form` from the portal first-load route, split `zod`, or raise budget to the measured value plus 10 percent) and continue with the budgets unchanged. If median server render of `/fr` exceeds 50 ms at 200 requests per second on the reference runner, open a change request for hash-based CSP on static public pages (D-48 option); do not implement it.
- [ ] **Step 4:** rerun until the recorded numbers are committed in `progress.md`.
- [ ] **Step 5: commit** `chore(web): bundle budget and nonce CSP spikes recorded`.

## Verification

Run from `frontend/` unless stated.

1. `npm run lint` exits 0 (ESLint, stylelint, `check:rules`).
2. `npm run typecheck` exits 0.
3. `npm test` passes (all Vitest suites; at least 40 tests across tokens, ui, format, api, middleware, scripts).
4. `npm run i18n:check` exits 0.
5. `npm run gen:api && git diff --exit-code src/api/generated` shows no diff; `npm run gen:permissions && git diff --exit-code src/shared/lib/permissions.generated.ts` no diff.
6. `npm run build && npm run size` exits 0; the printed table shows portal at most 130 KB, staff at most 250 KB, public at most 90 KB.
7. `npm run e2e -- public.spec.ts csp.spec.ts` passes; `npm run e2e:a11y` reports zero serious or critical violations for `/fr` and `/en`.
8. From the repository root: `make up` then `curl -sI http://localhost/fr | grep -i content-security-policy` shows a `nonce-` value that differs on two requests.

## Demo script

1. Run `make up`; open `/fr`. FR: the page title reads "Soleil Grid : votre énergie solaire prépayée", `<html lang="fr">`. EN: open `/en`, title "Soleil Grid: your prepaid solar energy".
2. Tab once: the "Aller au contenu principal" link appears with a visible ring. EN: "Skip to main content".
3. Open `/fr/nope`. FR: "Page introuvable" heading has focus. EN: `/en/nope` shows "Page not found".
4. View page source twice: the `nonce` attribute on script tags differs per load; the browser console shows no CSP errors.
5. Switch the browser to forced-colors or dark mode: tokens switch, focus ring stays visible.
6. Run `curl -X POST -H 'Next-Action: x' http://localhost/fr -i`: status 404.

## Done criteria

- Requirements: FR-16 (shell), NFR-10 (base components), NFR-11 (formatters), NFR-02 (budget gate), NFR-06 (CSP, actions, optimiser, secrets guard).
- Test cases (all from `docs/10-test-strategy.md` section 8): TC-NFR-11-1, TC-NFR-11-2, TC-NFR-11-3, TC-FR-16-1 (shell routes), TC-NFR-02-4, TC-NFR-06-11, TC-NFR-06-12, TC-NFR-06-13.
- Spike results recorded in `docs/plan/progress.md`.

## Plan notes for the tech lead

- **Route prefix and folders:** `docs/03` section 8 names the portal segment `/portal`, the feature folders after backend modules and `src/api/` for the client; `docs/08` section 2.1 and 5.1 say `/app`, feature folders by capability and `shared/api`. Followed `docs/03` (higher precedence) in every frontend slice file; docs/08 should be aligned.
- **No Next.js route handler:** `docs/08` plans `/api/csp-report` in Next.js; `docs/03` keeps `app/api` empty. Followed `docs/03`: `report-uri` comes from `HUB_CSP_REPORT_URI` (a backend or ops endpoint, not defined in `api/openapi.yaml`; reports are dropped silently if unset).
- **Client state library:** `docs/08` uses zustand for stream and theme state; `docs/03` says no global store library. Followed `docs/03`: React context plus `useSyncExternalStore` where needed; theme is a cookie.
- **Response envelope:** `api/openapi.yaml` wraps success bodies in `{ data }`; docs/03 does not mention it. The client wrapper unwraps it (Task FE-00.7).
- **Locale default on unsupported locale:** `TC-FR-16-3` says fall back to English; `docs/03` and D-44 make French the default. Followed French default for URLs and English only as the missing-key fallback for staff; flag for QA.
- **Webhook-event screens:** no document asks for a staff screen over `listWebhookEvents`; none is planned.
