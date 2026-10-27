# Demo frontend plan (Nuru application)

**Status:** plan, not yet built. Hand this file to the implementing agent as-is; the author
(Claude, in the owner's chat session) will review the result against this document afterward.

**Purpose.** This is not `fe-00-shell.md` and the rest of `docs/plan/frontend/fe-*.md`. Those
documents plan the production frontend across eighteen slices (i18n with `next-intl`, bundle-size
budget gates, Lighthouse CI, Playwright, a generated API client, WebAuthn, the SSE dashboard
stream, support/AI/reconciliation screens...). That scope is correct for shipping a product and
wrong for what this is: a demo built to show a job application (Nuru) that the backend — slices
00–04 plus the T1 provider stub, all real, tested and committed — does the hard part of a
payments-to-energy platform correctly. Every simplification below is deliberate and stated next to
the thing it simplifies, so a reviewer (or an interviewer) can see the line was drawn on purpose,
not from ignorance of the fuller plan.

**Non-goals, explicit.** No i18n library (`next-intl` et al.) — one locale, English UI copy. No
generated API client (`openapi-typescript`) — a dozen hand-written interfaces. No Playwright/
Lighthouse CI/bundle budgets. No SSE dashboard stream (slice 09 isn't built) — plain polling. No
meter telemetry (slice 08 isn't built) — the ops view reads the registry's own `status` and
`last_seen_at` fields, which are real columns, not live telemetry. No write forms for
customers/sites/meters — those `create*`/`update*` operations aren't routed yet in the backend
(only the read operations are; see "What's actually callable" below), so the UI doesn't pretend
otherwise. No approvals/maker-checker screen — real and tested (slice 01), but it's a second,
separate story from the payments-to-ledger one this demo tells; leave it for a follow-up if there's
time after the core demo is solid.

## 1. What already exists and is true right now

Checked directly against the code, not against planning docs, on 2026-10-28:

- **Backend** (`backend/src/hub/`): identity (staff login + TOTP/WebAuthn MFA, customer OTP with
  a proof-of-work challenge, RBAC, maker-checker approvals), customers/sites/meters/plans registry
  (read endpoints routed; see below), a double-entry ledger, and payments — top-up initiation,
  signed webhook ingestion, exactly-once event recording, late-credit handling, mismatch routing to
  a suspense account, provider reversals, and sweepers. 737 backend tests passing as of slice 04's
  close-out (`docs/plan/progress.md`).
- **`tools/provider_stub/`** (slice T1, just landed): a real mobile-money provider double. It
  signs its own webhooks, is driven by a token-guarded control API (`/control/scenarios`,
  `/control/collections/{reference}/complete`, `/control/reset`, `/control/deliveries`), and the
  slice 04 end-to-end test (`backend/tests/e2e/test_topup_with_stub.py`) exercises it through the
  real HAProxy balancer — that file is the single most reliable reference for exact call shapes;
  it is cited throughout this plan by line.
- **The stack** (`infra/docker/compose.yaml`): one HAProxy balancer on `:8080` splitting traffic by
  path to five pools (`be_api`, `be_webhooks`, `be_telemetry`, `be_stream`, `be_web`); `hub-web` is
  an nginx stub until `make up WEB=real` swaps in a `frontend/` build. The web pool gets **no** CSP
  header from the edge — "the HTML CSP with nonces is hub-web's job" — which this plan's `next.js`
  app does not take on (another stated simplification: no CSP, since there's no inline-script risk
  in a read-mostly demo and the edge already sets HSTS/nosniff/Referrer-Policy/Permissions-Policy).
- **`db/seed/test_users.json`**: one site (Lubumbashi, USD), one plan, five staff accounts (one per
  role, with published TOTP secrets so a demo script can compute live codes), and one customer
  (Amina Kalonji, `+243810000123`). Loaded by `make seed`.

## 2. What's actually callable today (grounded in `api/openapi.yaml` + the routed URLs)

Only the operations below are wired in `backend/src/hub/*/api/urls.py`. Anything in the OpenAPI
spec not in this list is declared for a later slice and must not be used by the demo frontend.

| Area | Operations | Notes |
|---|---|---|
| Auth (staff) | `staffLogin`, `staffMfaVerify`, `getMe` | Two-step: password → `mfa_token`, then TOTP code. Access token in the body (10 min), refresh token as an `httpOnly` cookie. |
| Auth (customer) | `getProofChallenge`, `requestCustomerOtp`, `verifyCustomerOtp` | Proof-of-work gate in front of the OTP request (`backend/src/hub/identity/application/proof.py`) — hashcash, solvable client-side. The OTP code itself is **never** retrievable over HTTP or in logs by design (stored as `code_hash`, logged code-free) — see §3 for how the demo handles this. |
| Registry | `listCustomers`, `getCustomer`, `listSites`, `getSite`, `listPlans`, `getPlan`, `listPlanTariffs`, `listMeters`, `getMeter`, `listCustomerMeters` | Read-only. No create/update/decommission routes exist yet. |
| Wallet | `getCustomerWallet` | `{customer_id, balance: {amount_minor, currency}}`. |
| Payments | `createTopUp`, `listTopUps`, `getTopUp`, `cancelTopUp` | **Only `customer` or `integrator` principals may call `createTopUp` — no staff role can** (by design, D-14: staff cannot move customer money). This is a real RBAC rule, not a gap, and the demo should say so out loud. |
| Ledger | `listLedgerAccounts`, `getLedgerAccount`, `listLedgerTransactions` (`?account_id=`, `?kind=`), `getLedgerTransaction` | Double-entry, `type` includes `customer_wallet`, `provider_clearing`, `revenue_energy`, `fees`, `suspense`, `chargeback_receivable`. |

Key schemas the frontend renders (field names as in `api/openapi.yaml`, already read in full):
`TokenPair` (`access_token`, `expires_in`, `principal{roles, site_ids, locale, ...}`), `Customer`,
`Meter` (`status: provisioned|active|offline|disconnected|decommissioned`, `last_seen_at`,
`credit_remaining_wh`), `Wallet`, `TopUp` (`status: initiated|pending|pending_unknown|confirmed|
failed|expired|cancelled|reversed`), `LedgerAccount`, `LedgerTransaction` (`lines[]` with
`account_id`, `direction`, `amount`).

## 3. The one small, necessary backend addition

The customer OTP flow is real, tested and correctly refuses to leak the code anywhere (not in
logs, not in the DB in plaintext — `code_hash` only). That is good security and it is also a dead
end for a live demo: there's no SMS gateway in this environment and there must not be a way to
read a live code. `backend/tests/e2e/test_topup_with_stub.py` solves exactly this problem for its
own purposes by minting a token directly, in-process, bypassing the OTP hop entirely
(`backend/tests/e2e/test_topup_with_stub.py:181-213`, the `MINT_SCRIPT` run via `docker compose
exec`). The frontend needs the same bypass reachable over HTTP, gated so it can never exist outside
a local/test run.

**Add:** `backend/src/hub/identity/api/dev_views.py`

```python
"""Dev-only token mint, mounted nowhere outside HUB_ENV in {local, test} (see urls.py).

Mirrors backend/tests/e2e/test_topup_with_stub.py's MINT_SCRIPT: there is no SMS gateway in this
environment, customer_otp.py correctly never lets a code be read back over HTTP or in a log, and a
demo still needs to sign a customer in. This is that hop, reachable over HTTP instead of a
docker exec, and it does not exist unless HUB_ENV is local or test.
"""
from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from rest_framework.views import APIView
from rest_framework.response import Response

from hub.identity.contracts import Principal
from hub.identity import wiring


class DevMintTokenView(APIView):
    authentication_classes = []
    permission_classes = []

    def post(self, request):
        kind = request.data.get("kind")
        subject = request.data.get("subject")
        now = datetime.now(UTC)
        session = uuid4()
        if kind == "staff":
            user_id = wiring.users().find_active_by_email(subject)  # or equivalent lookup
            principal = wiring.users().build_principal(
                user_id, session_id=session, auth_time=now, acr="mfa"
            )
            token = wiring.tokens().issue(
                principal, session_id=session, auth_time=now, acr="mfa", amr=("pwd", "totp")
            )
        elif kind == "customer":
            principal = Principal(
                subject_id=UUID(subject), kind="customer", customer_id=UUID(subject),
                session_id=session, auth_time=now, acr="pwd", locale="fr",
            )
            token = wiring.tokens().issue(
                principal, session_id=session, auth_time=now, acr="pwd", amr=("otp",)
            )
        else:
            return Response({"error": "kind must be staff or customer"}, status=400)
        return Response({"access_token": token})
```

(Check the exact lookup call against `backend/src/hub/identity/wiring.py` and the real
`staff_token`/`customer_token` helpers at `backend/tests/e2e/test_topup_with_stub.py:181-213` —
reuse that logic verbatim rather than re-deriving it; the test file is the source of truth.)

**Mount it conditionally** in `backend/src/hub/identity/api/urls.py`:

```python
from django.conf import settings
...
if settings.HUB_ENV in ("local", "test"):
    from hub.identity.api.dev_views import DevMintTokenView
    urlpatterns.append(path("dev/mint-token", DevMintTokenView.as_view(), name="devMintToken"))
```

`HUB_ENV` defaults to `"production"` (`backend/src/hub/settings/base.py:45`); this route is
genuinely absent from the URLconf anywhere else, the same guarantee the codebase already uses for
other locals-only behaviour (`backend/src/hub/settings/base.py:82`).

**In the demo narrative**, say this plainly: "the real OTP flow — phone number, a proof-of-work
challenge solved in the browser, a one-time code — is fully implemented and tested; there's no
telecom SMS gateway in this sandbox, so the demo signs in the same way the project's own
end-to-end test does, through a route that provably cannot exist outside `local`/`test`." That is
a more credible thing to say to an interviewer than silently faking it, and it's true.

**Do not add:** a way to read the real OTP code, a staff-initiated top-up, or any other
backend surface beyond this one route. If this route turns out to need more than ~40 lines, stop
and use the fallback instead: narrate the OTP screen without completing it, and sign in the
customer session with a cookie set by a one-line `psql`/Django-shell command run live. The HTTP
route is strictly nicer for the UI, not required for the demo to work.

## 4. Frontend: stack, layout, repo conformance

**Stack:** Next.js 15 (App Router), TypeScript strict, Tailwind. No `next-intl`, no generated API
client, no component library runtime dependency — hand-rolled `Button`/`Card`/`Badge`/`Table` in
`src/components/ui/`, or copy-pasted shadcn/ui components if that's faster (copied code, not an
added dependency tree, so it doesn't conflict with "don't over-engineer").

**Repo conformance (not optional — `infra/docker/compose.web.yaml` and the HAProxy config already
assume this contract):**
- `next.config.ts`: `output: 'standalone'`, port 3000.
- A `GET /web/health` route returning 200 (App Router: `src/app/web/health/route.ts`).
- Only these `NEXT_PUBLIC_*` vars, ever: `NEXT_PUBLIC_API_BASE`, `NEXT_PUBLIC_SITE_ORIGIN`,
  `NEXT_PUBLIC_DEFAULT_LOCALE` (`docs/plan/frontend/fe-00-shell.md:25` — this one rule from the
  fuller plan is worth keeping because it's a real secret-leak guard, not scope). Nothing with
  `SECRET`/`KEY`/`TOKEN`/`PASSWORD` in a `NEXT_PUBLIC_` name, ever — the dev mint route's response
  is a bearer token and must only ever be read server-side or held in page memory, never written
  to a `NEXT_PUBLIC_` var or `localStorage`.

**Dev-time networking, to avoid touching backend CORS at all:** add a `next.config.ts` rewrite
forwarding `/api/v1/:path*` to `http://localhost:8080/api/v1/:path*` (the HAProxy balancer) during
`npm run dev`. The browser then only ever talks to `localhost:3000`, same-origin, and
`NEXT_PUBLIC_API_BASE=/api/v1` (a relative path) works identically in dev and in the real
`make up WEB=real` deployment, where the browser talks to `:8080` directly and the balancer routes
`/api/*` itself. No CORS configuration anywhere, in either mode.

**Auth/session handling:**
- Access token: in memory only (a React context), never `localStorage`/`sessionStorage` — it's a
  bearer token good for 10 minutes; losing it on a hard refresh is an acceptable, correct trade for
  not handing an XSS a usable credential. Mention this choice in the demo close-out; it's a real
  security decision, not an oversight.
- Refresh token: the `httpOnly` cookie the backend already sets on `staffMfaVerify`/
  `verifyCustomerOtp`; the browser carries it automatically. A silent refresh on a 401 (`POST
  /auth/refresh`, retried once) is the only token-lifecycle code needed — no proactive refresh
  timer, no over-engineering.
- One fetch wrapper, `src/lib/api.ts`: reads `NEXT_PUBLIC_API_BASE`, attaches `Authorization:
  Bearer <token>` from context, unwraps the `{data: ...}` envelope, and on a non-2xx surfaces the
  RFC 7807 `problem+json` body's `detail` (see any `$ref: '#/components/responses/BadRequest'` in
  `api/openapi.yaml` for the shape) as a toast/inline error.
- A dozen hand-written TypeScript interfaces in `src/lib/types.ts` for the schemas listed in §2 —
  copy the field names and nullability exactly as the OpenAPI spec states them.

## 5. Information architecture

Two personas, two login doors, meeting on the ledger — that's the story.

```
/                         landing: "I'm a customer" / "I'm staff"
/customer/login           phone entry -> PoW solve -> code request -> code entry -> session
/customer                 wallet balance, meter(s), "top up" form, recent top-ups
/customer/topups/[id]     live status card (polls getTopUp every ~2s while pending), with the
                          sandbox panel described in §6
/staff/login              email + password -> TOTP code -> session
/staff                    nav shell: Customers | Meters & sites | Ledger
/staff/customers          list (listCustomers)
/staff/customers/[id]     profile + wallet (getCustomerWallet) + their meters + their top-ups
/staff/meters             table across sites: serial, site, status badge, last_seen_at,
                          credit_remaining_wh — the "ops view"
/staff/ledger             accounts list with live balances (listLedgerAccounts), click through to
                          listLedgerTransactions?account_id=... with the line-level debit/credit
                          breakdown (getLedgerTransaction) — this is the screen that proves the
                          double-entry claim; make sure the suspense account's balance and the
                          mismatch transaction from §6 are visible here
```

No `/staff` route calls `createTopUp` — the UI must not offer a "top up this customer" button to
a staff user; that operation is customer/integrator-only by the backend's own RBAC rule (§2), and
the UI should say so (a disabled affordance with a one-line tooltip reads better in a demo than a
missing feature — "staff cannot move customer money by design").

## 6. The demo's centerpiece: two payment outcomes, live

Grounded directly in `backend/tests/e2e/test_topup_with_stub.py`'s own helpers — reuse these call
shapes verbatim, they are proven against the real stack:

**Happy path** (mirrors `test_initiate_then_confirmed_via_stub`, lines 578-630):
1. Customer session calls `POST /api/v1/top-ups` with `{meter_id, amount: {amount_minor,
   currency: "USD"}, provider: "mpesa_cd", payer_msisdn: <their own phone>}` and an
   `Idempotency-Key` header → `202`, `status: "pending"`.
2. The UI's sandbox panel, on the pending top-up's detail screen, has a button: **"Simulate
   mobile-money PIN confirmation (sandbox)."** It posts to a Next.js server route (not called
   directly from the browser — see below), which:
   a. computes `provider_reference = "MP" + sha256(top_up_id).hexdigest()[:10].upper()`
      (`tools/provider_stub/src/provider_stub/state.py:41-48` — this is a pure function of the
      top-up's own id, no new backend endpoint needed to learn it);
   b. calls `POST {PROVIDER_STUB_BASE_URL}/control/collections/{provider_reference}/complete`
      with `{"outcome": "successful"}` and `Authorization: Bearer <STUB_CONTROL_TOKEN>`.
3. The stub asynchronously delivers its signed webhook through the real balancer to
   `hub-ingest-webhooks`; the UI's poll on `getTopUp` sees `status: "confirmed"` within a couple of
   seconds, and `getCustomerWallet`'s balance updates. That's the live moment to narrate.

**Edge case — mismatch to suspense, never a silent double-credit** (mirrors
`test_stub_wrong_amount_goes_to_suspense`, lines 677-701, the single best line in the whole
project to say out loud in an interview: *"the observable from the customer's side is the pair of
negatives: no confirmation and no money"*):
1. Before initiating, the same sandbox panel offers **"Simulate a provider amount mismatch
   (sandbox)."** This calls another Next.js server route that first registers a scenario —
   `POST {STUB}/control/scenarios` with `{"mode": "wrong_amount", "params": {"amount_minor":
   <intended + 100>}, "match": {"provider": "mpesa_cd", "amount_minor": <intended>}}` — then lets
   the user initiate normally and completes the collection the same way as the happy path.
2. The UI shows the top-up staying non-`confirmed` (`ledger_transaction_id` stays `null`, wallet
   balance unchanged) — exactly what the backend test asserts. Switch to `/staff/ledger`, filter by
   the `suspense` account, and show the posting landed there instead of vanishing or double-paying.
   That's the proof that exactly-once isn't just "no duplicates," it's "every franc is accounted
   for even when the provider disagrees with us."

**Why a Next.js server route and not a direct browser call to the stub:** `STUB_CONTROL_TOKEN` is
a secret (it's in `.env`, required by `docker compose`); it must never reach client-side JS. Two
small server routes, `src/app/api/demo/complete-collection/route.ts` and
`src/app/api/demo/add-scenario/route.ts`, read `STUB_CONTROL_TOKEN` and `PROVIDER_STUB_BASE_URL`
from server-only env vars (default `http://localhost:9100` for `npm run dev`, or
`http://provider-stub:9100` when the frontend itself runs inside the compose network) and proxy
the one call each needs. This is the only demo-specific backend-adjacent code in the whole plan,
and it's two thin proxy routes, not a feature.

## 7. Demo data

`make seed` already gives one customer and one site. For a staff "Customers" list that doesn't
look like a single-row toy, use the **already-built, already-tested CSV import**
(`feat(customers): all-or-nothing CSV import for customers and meters`) rather than writing any new
backend code: prepare a small CSV (4-6 customers, a couple of meters each, varied `status` values
so the ops view's badges have something to show) and run the existing import path/command once
before recording the demo. Zero new code; just data.

## 8. Explicit build order for the implementing agent

1. Backend: the dev-mint route (§3), smallest possible, behind `HUB_ENV`.
2. `frontend/` scaffold: Next.js + TS + Tailwind, `next.config.ts` (standalone, dev rewrite),
   `/web/health`, `.env.example` with exactly the three `NEXT_PUBLIC_*` vars plus
   `STUB_CONTROL_TOKEN`/`PROVIDER_STUB_BASE_URL` (server-only).
3. `src/lib/api.ts`, `src/lib/types.ts`, auth context + login screens for both personas (staff
   two-step; customer phone/PoW/OTP, with the dev-mint fallback wired behind the "code" step for
   when there's no SMS leg to complete — see §3's narrative note).
4. Customer dashboard: wallet, meters, top-up form, top-up detail with polling.
5. The two sandbox server routes (§6) and the sandbox panel UI.
6. Staff shell + Customers list/detail, Meters ops view, Ledger accounts + transactions.
7. Demo CSV import (§7), then a full dry run of both scenarios end to end against `make up
   --profile stubs` (provider-stub is on the `stubs` compose profile) or `docker compose ... up
   provider-stub` explicitly, plus `make seed`.

## 9. Review checklist (for the author's pass after the build)

- [ ] No `NEXT_PUBLIC_*` var beyond the three allowed names; no secret anywhere in client bundles
      (`grep -r STUB_CONTROL_TOKEN frontend/.next` after a build should find nothing, or only in
      server-only chunks).
- [ ] Access token never touches `localStorage`/`sessionStorage`.
- [ ] No staff-facing UI affordance calls `createTopUp`.
- [ ] The dev-mint route 404s (route doesn't exist) when `HUB_ENV` isn't `local`/`test` — verify by
      reading the urls.py guard, not by trusting a comment.
- [ ] Both demo scenarios (§6) run clean against the real stack at least once, not just against a
      mocked frontend.
- [ ] `/web/health` returns 200; `make up WEB=real` brings the real frontend up behind the balancer
      without changes to `compose.web.yaml` or `haproxy.cfg`.
