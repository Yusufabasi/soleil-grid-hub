# `hub-web` — the demo shell

The frontend for the Nuru application: a Next.js 15 App Router app that shows the backend of
`soleil-grid-hub` doing the hard part of a payments-to-energy platform correctly. It is deliberately
small — read-mostly screens, one write form, no i18n library, no generated API client, no SSE — and
`docs/plan/frontend/demo-shell-plan.md` is the document that says so, section by section, and why.
Read it before this file if you want the reasoning; this file is only how to run it.

## Running it

```bash
make up --profile stubs     # the stack, with the provider double
make seed                   # the published fixtures: 2 sites, 2 plans, 6 customers, 6 meters
make up --profile stubs WEB=real   # (optional) build this app into the stack's hub-web service
```

For frontend work, run it outside compose instead — the dev server talks to the same balancer:

```bash
cd frontend
npm install
npm run dev                 # http://localhost:3000
```

Both shapes are same-origin as far as the browser is concerned, so there is no CORS configuration
anywhere: `next dev` rewrites `/api/v1/*` to the balancer (`HUB_DEV_API_UPSTREAM`, default
`http://localhost:8080`) and behind the balancer the browser calls `/api/v1/*` directly and HAProxy
routes it. In the deployed shape that means the app runs on `:8080`; in `next dev` you open `:3000`.

There is nothing to copy into the environment. `src/lib/server-env.ts` reads the repository root
`.env` for the two server-only values the sandbox routes need, and `next.config.ts` reads it for the
dev upstream. The only browser-visible variables are the three allowed names in `.env.example`.

| command | what it does |
|---|---|
| `npm run dev` | dev server on `:3000`, with the rewrite to the balancer |
| `npm run build` | production build (standalone output, as `compose.web.yaml` expects) |
| `npm test` | unit tests (proof of work, TOTP, formatting, permissions, provider reference) |
| `npm run lint`, `npm run typecheck` | ESLint and `tsc --noEmit` |
| `npm run gen:api`, `npm run gen:permissions` | regenerate the checked-in contract types |

## Signing in

**Customer** (`/customer/login`): a number from the seeded list, a proof-of-work challenge solved in
the browser, then a code request — all real. The code itself is deliberately unreachable: the backend
stores a hash, logs nothing, and offers no route that returns it, and there is no SMS gateway here.
So the screen also offers **"Open a session as …"**, which signs in through
`POST /api/v1/dev/mint-token` — the route §3 of the plan adds, mounted only when `HUB_ENV` is `local`
or `test` and absent from the URLconf everywhere else (see `backend/src/hub/identity/api/urls.py`).

**Staff** (`/staff/login`): email and password from `db/seed/test_users.json`, then a six-digit TOTP
code from that file's published `totp_secret`. The password is prefilled; compute the code with
`oathtool --totp -b KRSXG5CTMVRXEZLU` or a phone authenticator. Signing in as each role in turn is the
quickest way to see the authorization matrix work: `finance_officer` is refused `listMeters` by the
API, `support_agent` is refused the ledger, and the screens say so in the contract's own words.

The access token lives in memory only (a React context) and the refresh token is the `httpOnly`
cookie the backend sets, scoped to `/api/v1/auth`. A hard refresh therefore signs you out; that is
the trade, and the 401 handler does one silent `POST /auth/refresh` and retries once.

## The two payment outcomes

Both happen on a top-up's detail screen (`/customer/topups/[id]`), which polls `getTopUp` every two
seconds while the status is unsettled. Customer: **Awa Diallo**, `+243810000101`, meter `SN-0001`.

**Happy path.** Start a top-up, then press **"Simulate mobile-money PIN confirmation"** in the sandbox
panel. That posts to a Next.js route handler (`src/app/api/demo/complete-collection/route.ts`), which
computes the provider's own reference (`MP + sha256(top_up_id)[:10]`) and tells the provider double
the payer entered their PIN, over `POST {PROVIDER_STUB_BASE_URL}/control/collections/{ref}/complete`
with `STUB_CONTROL_TOKEN` — the token never reaches the browser. The double then signs a
`payment.confirmed` webhook and posts it through the real balancer to `hub-ingest-webhooks`: the
signature, the idempotency key and the ledger posting are all the real code. The screen's own polling
shows the state flip to `confirmed`, the wallet balance move, and `ledger_transaction_id` appear.

**Amount mismatch → suspense.** Press **"Simulate a provider amount mismatch"** instead. The same
route registers a `wrong_amount` scenario matched to this one collection's reference and completes
it, so the provider reports a hundred minor units more than the customer asked for. Watch the pair of
negatives: the top-up stays unconfirmed, `ledger_transaction_id` stays `null` and the wallet does not
move. Then sign in as `finance@example.test`, open **Ledger**, press **"Filter to suspense"** and read
the newest posting: the provider's own amount sits on `suspense.USD`, debited against
`provider_clearing.mpesa_cd.USD`. That is "every franc is accounted for even when the provider
disagrees with us".

> **One thing to know before you click.** The provider double in the compose stack settles any
> collection on its own after `STUB_AUTO_CONFIRM_SECONDS` (default 5) — handy when poking the API by
> hand, and it means the PIN button is a speed-up rather than a requirement. For the demo as scripted
> above, where the click is visibly the *cause*, add `STUB_AUTO_CONFIRM_SECONDS=0` to `.env` and
> recreate the double (`docker compose --env-file .env -f infra/docker/compose.yaml --profile stubs up
> -d provider-stub`). Then nothing confirms until the panel says so — and the mismatch scenario has
> all the time it needs.

The platform's own rate limits apply to the demo customer, because they apply to every customer:
ten top-ups per meter per 24 h and five per payer per hour (`HUB_TOPUP_MAX_PER_METER_24H`,
`HUB_TOPUP_MAX_PER_PAYER_1H`). A sixth top-up in an hour answers `429 topup_limit` with that
explanation; sign in as another seeded customer, or raise the variables in `.env` for a session.

## What this app deliberately does not do

- **No create or edit forms** for customers, sites, meters or plans: those operations are declared
  for later slices and are not routed in the backend yet, so the screens are read-only on purpose and
  say so where a button would be.
- **No staff-initiated top-up.** `createTopUp` is customer/integrator-only by the backend's own rule
  (D-14, "staff cannot move customer money"), so the customer profile shows the affordance disabled
  with the reason rather than hiding it.
- **No meter telemetry.** `status`, `last_seen_at` and `credit_remaining_wh` are columns of
  `hub.meters` — the registry's stored facts, not a live feed. Slice 08 is not built.
- **No SSE stream** (slice 09 is not built) — the top-up screen polls, as its own comments say.
- **No CSP** from this app: the edge already sets HSTS, nosniff, Referrer-Policy and
  Permissions-Policy, and the HTML CSP with nonces is a separate, larger job.
- **No approvals screen** — maker-checker is real and tested, but it is a second story, and §0 of the
  plan puts it out of scope.

## Where things live

```
src/app/                     landing, then one door per persona
  customer/login/            phone -> proof of work -> code (and the sandbox mint)
  customer/                  wallet, meters, top-up form, recent top-ups
  customer/topups/[id]/      live status, polling, the sandbox panel
  staff/                     nav shell: Customers | Meters & sites | Ledger
  staff/customers/[id]/      profile, plan and tariff, wallet, top-ups
  staff/meters/              the ops view across sites
  staff/ledger/              accounts with live balances -> transactions, line by line
  api/demo/                  the two sandbox proxies (server-only control token)
  web/health/                the health route the balancer's `be_web` pool probes
src/lib/                     api wrapper, types, session context, permissions, formatters,
                             proof of work, provider reference, server-only env
src/components/              ui primitives and the two refusal components
```
