# 08. Frontend, UX and Accessibility Design

Owner: UX and accessibility architect. Status: design phase, nothing built. Follows `00-shared-context.md` (FR-01..FR-20, NFR-09..NFR-11, invariants 1 to 5). All numbers are assumptions to validate with real devices and users unless marked measured. Contrast ratios in section 5 are design targets that a CI script recomputes from the token file; if the script disagrees with this document, the script wins and the token changes.

---

## 1. Users, contexts and design principles

### 1.1 Users and contexts

| User | Device and context | Connectivity | Language | What they need most | Risk if the UI fails |
|---|---|---|---|---|---|
| Customer (household, small shop) | Low-end Android (2 GB RAM, Android Go, Chrome or Samsung Internet, 360x640 px), outdoors, bright sun, one hand | 3G or 2G, drops for minutes, data bought by the megabyte | French first, EN available; some low literacy | Top up fast, see balance, know whether money arrived | Pays twice, believes money is lost, calls the support desk |
| Support agent | Office laptop, 1366x768, often two tabs open, 6 to 8 hours a day | Stable but office link can degrade | FR and EN mixed per ticket | Work a queue quickly, trust or reject AI help | Sends a wrong reply, misses an urgent ticket |
| Finance officer | Desktop, wide tables, keyboard heavy | Stable | FR mostly | Match provider statements to ledger, clear exceptions | Misposts money, cannot prove what happened |
| Operations engineer | Control-room wall display plus laptop; long sessions; glances from a distance | Stable, SSE stays open for hours | FR/EN | See sites, revenue today and alerts at a glance, know if data is stale | Acts on stale data believing it live |
| Administrator | Desktop | Stable | FR/EN | Roles, API keys, audit, KB, AI spend | Over-grants access, leaks a key |

Assistive technology is expected for every group: TalkBack on Android (customers), NVDA and JAWS on Windows (staff), VoiceOver on iOS and macOS, browser zoom to 200 to 400 percent, Windows high-contrast mode, switch and voice control. Customers may also use large system font sizes, so the portal must tolerate 200 percent text scaling on a 360 px screen.

### 1.2 Design principles that follow

1. **Low bandwidth first.** The customer portal ships as little JavaScript as possible, no web fonts beyond one subset, no raster hero images, and renders usable content before data loads (skeletons are text-shaped, not spinners only). Budgets in section 8.
2. **Few steps to top up.** From opening the portal with a valid session to a confirmed provider prompt is at most three actions: choose amount (preset chips preselect the last used), confirm the summary, approve on the phone. Phone number and provider are remembered (WCAG 3.3.7 Redundant Entry).
3. **Money states are explicit and never ambiguous.** Every payment shows exactly one label from the mapping in section 3.1, derived from the canonical `top_ups.status` and `meter_tokens.status` (D-01; labels are a UI mapping, the enums come from the schema). Each state has an icon, a text label, a color and a plain sentence. Color is never the only carrier. The UI never shows "Success" because the browser got a 200; success is shown only when the server says `confirmed` (webhook-driven, FR-02/FR-03).
4. **Never optimistic with money.** Balances and payment states change only after the server says so (section 5.5).
5. **Plain, short, translated language.** Reading level about grade 6 in French. No jargon on customer screens ("ledger", "reconciliation", "idempotency" never appear).
6. **Humans decide, AI assists.** AI output is always labelled, never sent automatically, and always reviewable (invariant 2, NFR-08). Section 4.
7. **Honest about staleness and offline.** Any data that may be old shows its age. Anything that cannot work offline says so.
8. **Operable by everyone.** WCAG 2.2 AA is a release gate, not a polish pass (NFR-10).

---

## 2. Information architecture

### 2.1 One Next.js application, three route groups (ADR-0021, D-44)

The frontend is the `hub-web` deployable: one Next.js (App Router, TypeScript strict, Node standalone output) project in the repository folder `web/`, served behind the same balancer and the same site as the API (pool `be_web` for `/` and `/_next/*`; everything under `/api/` goes to the Django pools). Same site matters because the refresh cookie (`Path=/api/v1/auth`, SameSite=Strict, D-17) must be sent by the browser to the API without any cross-site setup. There is one host name, no `app.` and `staff.` split. The Next.js server holds no secrets, never reads the database and does not proxy authenticated API calls (no backend-for-frontend in v1).

URL shape: every page lives under a locale prefix `/fr` or `/en` (section 6). After the locale there are three areas, each a route group with its own layout so the customer bundle never contains staff code:

| Route group (folder) | URL prefix | Audience | Auth |
|---|---|---|---|
| `(public)` | `/{locale}/...` (home, help, tariffs, status, login shell) | Anyone | none |
| `(portal)` | `/{locale}/app/...` | Customers | Phone one-time code |
| `(staff)` | `/{locale}/staff/...` | Support, finance, operations, administrators | Company identity provider or local account with MFA (D-16) |

The sitemaps in 2.2 and 2.3 list paths relative to the area prefix, so `/top-up` means `/fr/app/top-up` and `/tickets` means `/en/staff/tickets`. A lint rule (`eslint-plugin-boundaries`) forbids `(portal)` importing from `(staff)` and the reverse, and forbids features importing each other except through their `index.ts`. Shared code lives in `src/shared`.

**Server component or client component, route by route (D-44).** Rule: a page that needs no access token is a server component and is rendered on the server; every authenticated page is a thin server `page.tsx` that renders only a client component, because the access token lives in browser memory (D-17) and the Next.js server never sees it. Interactive widgets everywhere are client components marked with `'use client'` at the smallest possible leaf.

| Route (after locale) | Kind | Why |
|---|---|---|
| `/` (public home), `/help`, `/help/[slug]` (public KB articles), `/tariffs`, `/status` | Server component, rendered on each request (the CSP nonce forces dynamic rendering); its data is cached with `fetch` `revalidate` (help 300 s, status 30 s, tariffs 300 s) | No personal data, no token; HTML-first for slow phones and search engines |
| `/login`, `/login/code` (customer), `/staff/login` | Server component shell containing a small client form island | The shell and copy render fast; the form needs state and calls the API from the browser |
| `/app/*` (home, top-up, top-up/[paymentId], history, history/[id], support, support/new, support/[ticketId], account) | Server `page.tsx` that renders one client component per page | Authenticated, token in memory; the server can only render a skeleton and the translated static text |
| `/staff/*` (all) | Server `page.tsx` that renders one client component per page | Same reason; dashboards also keep an SSE stream open |
| `/staff/me/security`, `/staff/approvals/*` | Client components | WebAuthn needs browser APIs; approvals need step-up |
| `error.tsx`, `global-error.tsx` | Client components (required by Next.js) | Error boundaries must be client components |
| `loading.tsx` | Server component | Static skeleton shown while a route segment streams |
| `/api/csp-report` | Route handler (server) | Receives CSP violation reports, forwards to observability, no personal data |
| `middleware.ts` | Edge/Node middleware | Locale redirect and the CSP nonce (section 9.1) |

Server Actions are disabled (D-46): `next.config` sets `experimental.serverActions` off and a lint rule bans `'use server'`; all writes are client calls to the API.

### 2.2 Customer portal sitemap

```
/                      Home: balance, "Top up" primary button, last 3 transactions, alerts
/top-up                Amount, provider, confirm, status
/top-up/:paymentId     Payment status (polls, money states)
/history               Transactions list (payments, credits), filter by month
/history/:id           One transaction detail and receipt
/support               My tickets, "New ticket"
/support/new           New ticket form
/support/:ticketId     Ticket thread
/account               Profile, language, notification preferences, my data (export/erase request)
/login  /login/code    Phone number, one-time code
/help                  Public help pages (public group, server rendered, not under /app; linked from the tab bar)
```

Navigation: bottom tab bar with four items (Home, Top up, History, Help/Support), 56 px high, each target at least 48x48 CSS px, icon plus text label always visible. Account is reached from a header icon button with a text label "Account" shown from 360 px up.

### 2.3 Staff app sitemap and navigation per role

```
/dashboard                      Operations dashboard (FR-13)
/sites  /sites/:id              Site list and detail (FR-01)
/customers  /customers/:id      Customer and meter registry (FR-01)
/tickets                        Ticket queue with triage panel (FR-08, FR-09)
/tickets/:id                    Ticket thread, summary, AI draft (FR-10, FR-11)
/tickets/:id/draft/:draftId     AI draft review (FR-10)
/ask                            Ask the data (FR-12) and history
/reconciliation                 Statements and exceptions queue (FR-14)
/reconciliation/:exceptionId    Exception detail and resolution
/payments/:id                   Payment and ledger postings (read), (FR-02, FR-04, FR-05)
/incidents  /incidents/:id      Incidents, link up to 100 tickets (FR-08, FR-07)
/approvals                      Pending approvals (maker-checker) and my proposals (D-15)
/approvals/:id                  Approval review: exact payload and hash status
/accounting-periods             Period list and lock confirmation (FR-04, FR-14), finance lead
/kb  /kb/:id/edit               Knowledge base list and editor (FR-17)
/audit                          Audit log viewer (FR-15), streams filtered by role
/admin/users  /admin/roles      Users and roles (FR-15), changes go through approvals
/admin/api-keys                 Integrator and gateway keys, scopes, limits (FR-18)
/admin/ai-spend                 AI cost and latency per feature (NFR-09, NFR-13), admin only
/admin/privacy                  Export and erasure requests, blocking reasons (FR-20), dp_admin
/notifications                  Outbox status, failed sends (FR-19)
/me                             Profile, language, sessions
/me/security                    MFA: TOTP, WebAuthn keys, backup codes, recovery
```

Navigation is a left rail on desktop (collapsing to a labelled menu button under 900 px). Items are filtered by the user's role and permissions (D-13, D-14); a hidden item is also blocked by a route guard (section 5.7). The single source for what each role can do is `api/permissions.yaml` (D-14); until it exists, the matrix in `07-security-threat-model.md` section 4.6 is the source. The nav table below is generated from that file at build time (a test fails if the hand-written table in this document and the file differ, so this table is an illustration). Roles are `support_agent`, `finance_officer`, `operations_engineer`, `administrator`. `kb:edit`, `kb:publish`, `export:bulk` are permissions and `finance_lead` and `dp_admin` are user attributes, not roles (D-13). The administrator is a platform administrator: no tickets, no money, no customer content, no ask-the-data and no dashboards.

| Nav item | Support | Finance | Operations | Admin |
|---|---|---|---|---|
| Dashboard | tickets widgets only (own sites) | revenue read | full | none |
| Sites, Customers | customers read (masked phone, reveal audited, own sites) | customers read as needed for payments | sites and meters; customers as pseudonymous ids | none (platform configuration only) |
| Tickets, Incidents | full (own sites) | none | technical tickets read; incident linking | none |
| Ask the data | support views | finance views | ops views | none (entry absent) |
| Reconciliation, Payments, Accounting periods | none | propose; lead resolves and locks periods | none | none |
| Approvals | see items they may check (never their own proposals) | maker and checker (finance lead for adjustments) | configuration changes | users, roles, keys, KB publish, erasure (second administrator) |
| KB | draft (`kb:edit`) | none | none | draft and publish after a second approver (`kb:publish`) |
| Audit | none | money streams | system streams | all streams, read only |
| Admin (users, keys, spend) | none | none | none | full, each change via approval |
| Privacy (export, erasure) | none | none | none | `dp_admin` attribute only |
| Notifications | none | none | full | none |

### 2.4 Screen to requirement map

| Screen | FR served |
|---|---|
| Portal home, balance, history | FR-01, FR-04 |
| Top-up and payment status | FR-02, FR-03 (status only), FR-05 (credited state) |
| Portal and staff tickets | FR-08, FR-11 |
| Triage panel | FR-09 |
| AI draft review | FR-10, FR-16 |
| Ask the data | FR-12 |
| Operations dashboard | FR-06 (via aggregates), FR-07, FR-13 |
| Sites, customers | FR-01 |
| Reconciliation queue | FR-14, FR-04 |
| KB editor | FR-17, FR-10 (citations source) |
| Audit viewer, roles, approvals, step-up, MFA enrolment | FR-15 |
| API key admin | FR-18 |
| Incident linking | FR-07, FR-08 |
| Accounting-period lock | FR-04, FR-14 |
| Export and erasure flows (customer request, staff approval) | FR-20 |
| Notification outbox view, preferences | FR-19 |
| Account: export and erase | FR-20 |
| Language switcher everywhere | FR-16 |

---

## 3. Key screens, wireframes and states

State vocabulary used in every table: **Loading** (skeleton with `aria-busy`), **Empty** (nothing yet, with a next action), **Error** (request failed, with Retry and a correlation id), **Partial** (some parts loaded or some parts failed), **Offline** (browser offline or API unreachable).

### 3.1 Customer: balance, top-up, history

Home (360 px wide):

```
+------------------------------------+
| Soleil            [Account] [FR|EN]|
+------------------------------------+
| Your energy credit                 |
| 42,3 kWh           ~ 3 days left   |
| Wallet remainder: 0,12 $           |
| Updated 14:02                      |
|                                    |
| [        Top up        ]  (56 px)  |
+------------------------------------+
| Recent                             |
| (ok) Credited      +10,00 $  12 Oct|
| (..) Pending       +5,00 $   Today |
| (x)  Failed        5,00 $    10 Oct|
|                  [See all history] |
+------------------------------------+
| [Home][Top up][History][Help]      |
+------------------------------------+
```

Top-up flow:

```
Step 1 Amount            Step 2 Confirm             Step 3 Status
Phone: 0812 345 678      You will pay               (..) Waiting for your phone
Provider: (o) M-Pesa     10,00 $ to Soleil Grid     Approve the request on
          ( ) Airtel     Fee: 0,00 $                0812 345 678.
Amount:  [5$][10$][20$]  You get: about 40 kWh      Do not pay again.
  or [______] $          [ Pay 10,00 $ ]  [Back]    This page updates by itself.
[ Continue ]                                        [Check now]   [Cancel]
```

**What the customer sees as balance (D-10, C-37).** Energy credit in kWh is the headline figure (the value that matters for the meter). The wallet remainder (money not yet converted, for example the part below one watt-hour, since energy is rounded down, D-08) is a secondary line and is usually zero after issuance; the label explains "Money kept for your next top-up". Money is never shown as a standalone "balance" without the kWh figure. Amounts always show non-zero minor units on money-moving screens (top-up summary, receipt, wallet remainder); CDF is stored at two decimals per ISO 4217 and shown in whole francs only for amounts above 100 FC on summary views such as the history list (for example `28 000 FC`), while the top-up summary and receipt show exact amounts.

**Status labels (canonical status to label).** The top-up status and token status combine into one customer label:

| `top_ups.status` (and token) | Customer label | Sentence |
|---|---|---|
| `initiated` | Starting | "We are contacting your mobile-money provider." |
| `pending` | Waiting for your phone | "Approve the request on your phone. Do not pay again." |
| `pending_unknown` | Needs review | "We are checking this payment. If money left your account you will not lose it. Do not pay again." |
| `confirmed`, token `pending` or `sent` | Paid, adding energy | "Payment received. Energy is being added to your meter." |
| `confirmed`, token `acknowledged` | Credited | "Energy added to your meter." |
| `confirmed`, token `failed`, `unknown` | Paid, energy delayed | "Payment received. We are retrying delivery to your meter. You do not need to pay again." |
| `failed` | Failed | "The payment was refused. Nothing was charged." |
| `expired` | Expired | "The request timed out. If money left your account, it will still be credited." (because late confirmations are credited, D-05) |
| `cancelled` | Cancelled | "You cancelled this request." Same late-credit sentence as Expired. |
| `reversed` | Reversed | "The provider returned this payment. Contact support if this is a surprise." with a ticket button |
| any, with `late = true` | flag "Confirmed late" shown beside Credited or Paid | "This payment arrived after the request timed out. It has been credited." |

The status never reads "Success" before the server reports `confirmed`. A `pending_unknown` or `reversed` row offers "Open a ticket" prefilled with the payment id (FR-08).

Rules: the Pay button is disabled after one press and shows "Sending" with `aria-disabled`; the client sends an `Idempotency-Key` (UUIDv7 generated when step 2 opens, kept in `sessionStorage`) so a double tap or a reload cannot create two payments (invariant 3 at the edge). The status page polls `GET /payments/{id}` every 3 s for 2 minutes with backoff, then every 15 s; it uses a live region (section 7.5). Currency is chosen from the account; amount presets come from the plan.

| State | Balance | Top-up | History |
|---|---|---|---|
| Loading | Skeleton card, text "Loading balance" in a visually hidden node | Plan data skeleton; form already usable with last used values | 5 skeleton rows |
| Empty | "No balance yet. Top up to switch your meter on." with Top up button | n/a | "No payments yet." with Top up button |
| Error | "We cannot show your balance right now." Retry. Last known balance shown with its time if cached | Server error mapped to a sentence (provider down, amount out of range); payment never shown as failed unless server says so | Retry and correlation id |
| Partial | Balance shown, "days left" hidden with note "Estimate unavailable" | Provider list partly failed: unavailable provider disabled with reason | First page shown, "Could not load older items", Retry |
| Offline | Last cached balance with "Offline. Last updated 14:02" banner | Top up button disabled with explanation "You need a connection to pay"; no payment is ever queued offline | Cached last 20 items, read only |

A "Needs review" payment (`pending_unknown`, maps to reconciliation) shows the sentence in the table above, "We will message you", and the ticket button.

### 3.2 Support ticket thread (customer and agent view)

```
+----------------------------------------------------+
| Ticket #4821  "Meter off after top-up"   [Open]    |
| Opened 12 Oct   Category: Meter                    |
+----------------------------------------------------+
| You, 12 Oct 09:14                                  |
|  My meter is off since yesterday.                  |
| Support (Aïcha), 12 Oct 10:02                      |
|  Bonjour, we have sent the credit again...         |
+----------------------------------------------------+
| Your reply  [__________________________] [Send]    |
| [Attach file] Up to 3 files, 5 MB each (jpg, png, pdf) |
| 0 / 5 000 characters                               |
+----------------------------------------------------+
```

The customer never sees a per-message AI label because AI text only appears after human approval and is then the agent's message (the agent is the author of record; the audit log keeps AI provenance, FR-15). Customers are told once, in the portal terms and on the new-ticket page, a sentence of the form "Our staff may use AI tools to prepare replies. A person always reviews a reply before it is sent." The exact wording is pending legal review (D-35, open); the string lives in the `portal` namespace under `terms.ai_disclosure` in FR and EN so it can change without code. Limits (D-30): message body 5,000 characters with a live counter (announced at 4,500 and 5,000), at most 3 attachments of 5 MB each in jpg, png or pdf; violations are field errors naming the limit. Attachments show "Scanning" until the server scan finishes; a failed scan shows "File rejected" and the message can still be sent without it.

| State | Behaviour |
|---|---|
| Loading | Thread skeleton; composer enabled |
| Empty (list) | "You have no tickets. Need help? Start a ticket." |
| Error | Retry; composer keeps typed text (stored in memory and `sessionStorage` draft) |
| Partial | Messages loaded, attachments failed: placeholder "Photo unavailable. Retry" |
| Offline | Thread cached read-only; Send disabled; typed reply kept and "Send when online" button appears (safe: tickets are not money, and `Idempotency-Key` prevents duplicates) |

### 3.3 Staff: ticket queue with AI triage panel

```
+--------------------------------------------------------------------------+
| Tickets   Filter: [Open v][All categories v][Urgency v][Language v] [Mine]|
+----------------------------------+---------------------------------------+
| Queue (table, sortable)          | Triage panel (AI suggestion)          |
| # | Subject | Urg | Cat | Age    |  [AI] Suggested by AI, not confirmed  |
| 4823 Billing double | High | ...  |  Category: Billing      conf. Medium  |
| 4822 No power | High | Meter | 3h |  Urgency:  High                       |
| 4821 Hello | Low | Other | 1d    |  Language: French                     |
|                                  |  Personal data found: phone, email    |
|                                  |  Applied automatically (suggestion)   |
|                                  |  [Override...]   [Looks right]        |
|                                  |  Summary (AI): 2 sentences...         |
+----------------------------------+---------------------------------------+
```

The queue is a real `<table>` with sortable column buttons, `aria-sort`, a caption "Open tickets, 142 results", and pagination (not infinite scroll). Selecting a row opens the panel as a region beside the table on wide screens and as a page on narrow screens; the row link is the ticket subject (a real link) so keyboard and screen-reader users never depend on row click.

The triage panel shows the suggested category, urgency, language and personal-data flags with a confidence label (High, Medium, Low, in words) and the "AI suggestion" tag. Per D-32, triage is applied automatically as a visibly marked suggestion: the queue badge reads "Category: Billing (AI suggestion)" with the AI tag until a person confirms or overrides it. One click on "Override" opens an inline form (selects limited to the canonical vocabulary: categories `billing_payment`, `no_power`, `meter_fault`, `tariff_question`, `account_change`, `complaint`, `safety_hazard`, `other`; urgency low, normal, high, critical; language fr, en, sw, ln, other; personal-data flags phone, email, national id, payment reference, address, health). Saving records an override (who, old value, new value) used for the recall gate and evaluation; "Looks right" records a confirmation and removes the suggestion marking. Low-confidence results go to a distinct "Needs a look" queue filter. A `critical` urgency or `needs_human_now` result shows a banner "Paging on-call is waiting for confirmation" when staff confirmation is required above the paging threshold (D-32), with Confirm and Do not page buttons. Tickets are never auto-closed or auto-replied.

| State | Queue | Triage panel |
|---|---|---|
| Loading | 8 skeleton rows | Panel skeleton "AI triage in progress" with `role="status"` |
| Empty | "No open tickets. Well done." plus filters reset | "Select a ticket" |
| Error | Retry, correlation id; incident linking available from the row menu | "AI triage unavailable. You can triage by hand." Fields are plain selects |
| Partial | Rows shown; some triage badges say "Pending triage" | Category present, language missing: unavailable field says "Not detected" |
| Offline | Banner "Offline. List may be out of date", actions disabled | Same |

### 3.4 AI draft review screen (FR-10, invariant 2)

```
+-------------------------------------------------------------------------+
| Ticket #4823  Review reply draft                         Draft v2       |
+-----------------------------------------+-------------------------------+
| [AI] WRITTEN BY AI. NOT SENT.           | What the customer will see    |
| The customer sees nothing until you     | (preview, FR)                 |
| approve.                                |  Bonjour Marie,               |
|                                         |  Nous avons vérifié votre     |
| Language: [Français v]                  |  paiement du 12 octobre ...   |
| Reply text (editable)                   |  Cordialement,                |
| +-------------------------------------+ |  Aïcha, Soleil Grid           |
| | Bonjour Marie, ...                  | |                               |
| | ...                          [1][2] | | Channel: SMS + portal         |
| +-------------------------------------+ | Length: 3 SMS segments        |
|                                         |                               |
| Sources used (citations)                | Edited by you: 2 changes      |
| [1] KB: "Double payment refunds" v3     |                               |
|     "Refunds are processed within..."   |                               |
| [2] KB: "Meter reset steps" v7          |                               |
|                                         |                               |
| Checks: (!) Mentions a refund amount    |                               |
|     not found in sources. Verify.       |                               |
|                                         |                               |
| [Approve and send]  [Save edits] [Reject...] [Regenerate]               |
+-------------------------------------------------------------------------+
```

Rules:

- The label "Written by AI. Not sent." is a persistent banner, text plus icon, not color alone, at the top of the draft pane. Once an agent edits, the label becomes "AI draft, edited by you" but never disappears while status is draft. On send, the message becomes the agent's message.
- Citations are numbered markers inside the text mapped to a "Sources used" list with KB article title, version and the quoted passage; each opens the KB article version in a side drawer (focus managed, section 7.3). If the draft has a claim sentence with no citation, the server returns it in `unsupported_claims` and the UI shows a warning list and highlights the sentence with an underline pattern (not color only).
- Approve and send is a primary button disabled until the agent has focused the editor or pressed "I have read this draft" (a checkbox) for drafts that contain a money amount, refund or personal data flag. This is a deliberate friction point, not a dark pattern: the checkbox states the specific risk ("I checked the amount against the payment").
- Approve opens a confirmation dialog: "Send this reply to Marie by SMS and portal?" with the exact final text; the button inside is "Send reply". There is no keyboard shortcut for approve (shortcuts exist for navigation only) to prevent accidental sends.
- **Money-affecting drafts (D-35).** When the server marks a draft `money_affecting` (it mentions a refund, credit, amount or payment outcome), the panel shows a persistent notice "Finance co-approval required" in place of the single Approve button. The agent's button becomes "Request finance approval"; it creates an approval (section 3.11) for a finance officer, shows the status "Waiting for finance" with the approver's name once assigned, and the message is sent only when both approvals exist. The agent cannot approve the money part themselves. Any text changed at approval time triggers the server lints again; failures are listed before the dialog closes ("Contains a promise of refund not in a source").
- **Undo window (recall).** After sending, the backend holds the message for 30 seconds (outbox `available_at` is set 30 s ahead, D-35, FR-19). The UI shows a toast-like status bar (not auto-dismissing before the window ends) "Reply scheduled. Undo (28 s)" with a real Undo button; it is announced once via `role="status"` ("Reply scheduled. You can undo for 30 seconds."), not every second. Countdown text updates visually every second but is `aria-hidden`; the remaining time is announced at 10 s and 0 s. After the window the status becomes "Sent at 10:42" and Undo is gone. Undo calls `POST /drafts/{id}/recall`, valid only while the outbox row is unsent (message status `recalled`); if the server answers that the window has passed, the bar says "Already sent". The window is 30 seconds for every reply; it is not configurable per ticket in v1 (the earlier urgent-ticket zero-delay option is not adopted, see section 10).
- Reject opens a dialog asking for a reason from a short list (Wrong facts, Wrong tone, Missing info, Other) used as feedback to prompt tuning; a free-text field is optional.
- Regenerate respects the per-ticket budget (NFR-13, D-34); when exhausted the button is disabled with "AI limit reached for this ticket. Write your reply by hand." Generation states from the API (`generating`, `ready`, `failed`, `refused`, `superseded`) map to the loading, draft, error, refusal and "Newer draft available" displays; stored message statuses (`draft`, `approved`, `sent`, `rejected`, `recalled`) drive the status chip. Approval needs permission `ticket.ai_approve` and the button is absent without it.
- Two agents: the draft carries a version; approve sends `If-Match` with the version; a conflict shows "This draft changed. Reload".

| State | Behaviour |
|---|---|
| Loading | "Drafting reply..." status with elapsed seconds, Cancel; editor empty and usable so agent can start by hand |
| Empty | "No draft yet" with "Draft with AI" and "Write by hand" |
| Error | "The AI could not draft a reply (code AI-502, id abc). Write by hand or retry." Editor available |
| Partial | Draft exists but citations failed to resolve: citation list shows "Source unavailable"; Approve disabled until the agent confirms they checked facts |
| Refusal | "The AI declined to write this reply." Reason category only; no model text is shown as if it were a draft |
| Offline | Edits saved locally; Approve and Regenerate disabled |

### 3.5 Ask the data (FR-12)

```
+-------------------------------------------------------------------------+
| Ask the data               Scope: Finance (read-only, pseudonymous)     |
| Question [ How much revenue did site Kasai make last week? ] [Ask]      |
| Examples: [Revenue by site this month] [Top 10 failed payments]         |
+-------------------------------------------------------------------------+
| [AI] SQL written by AI. Runs read-only, limited to your role.           |
| Query (read-only, copyable)                                             |
|  SELECT site_name, SUM(amount_minor)/100.0 AS revenue_usd               |
|  FROM v_finance_payments WHERE ...                                      |
| Interpretation: "Revenue for site Kasai, 12 to 18 Oct, USD"             |
| 7 rows.  Data as of 14:05.                      [Export CSV] [Copy SQL] |
+-------------------------------------------------------------------------+
| Result table (caption, sortable, max 1000 rows)                         |
| Day | Revenue USD                                                       |
+-------------------------------------------------------------------------+
| History: today (6)  [Re-run] [Reopen]  ...                              |
+-------------------------------------------------------------------------+
```

The SQL is always shown before and with the result, in a monospace block with `aria-label="Generated query"`, never hidden behind a toggle. The interpretation sentence restates the question in plain words so a non-SQL user can catch misreadings. Model name, tokens and cost are never shown on this screen (the administrator, the only role that sees cost, has no access to ask-the-data, D-14, D-24; cost lives on `/admin/ai-spend`). The explanation describes the SQL and its assumptions only; query rows are never sent to the model (D-31). Support and operations views are aggregates; finance rows are pseudonymous, so the result table never shows names or phone digits. The route and the nav entry do not exist for the administrator.

Refusal and role limits:

```
(i) You cannot ask about this with your role (Support).
    Customer payment data is available to Finance. 
    You can ask about tickets and meters. [See what I can ask]
```

The message states the limit without disclosing what exists in the forbidden data (no table names or row counts of out-of-scope data), and offers allowed alternatives. A distinct message covers unsafe SQL rejected by the validator ("The question needs data I may not read or write to. No query was run."). Because invariant 5 is enforced server side by role-scoped views, the UI never relies on hiding things.

| State | Behaviour |
|---|---|
| Loading | Steps announced in a status region: "Writing query", "Running query", with Cancel (cancels request; ask-the-data target under 8 s p95, NFR-02) |
| Empty (history) | "Your questions will appear here." Examples shown |
| Error | "Could not run this question (timeout after 8 s)." SQL shown if produced; Retry, Rephrase |
| Partial | Result truncated at 1000 rows: "Showing 1000 of at least 4,213 rows. Narrow your question or export." |
| Zero rows | "The query ran and found no rows." plus SQL (not an error) |
| Offline | History readable from cache, Ask disabled |

History keeps question, SQL, time, row count per user for 90 days, visible only to the author (admins via the audit log).

### 3.6 Operations dashboard (FR-13)

```
+--------------------------------------------------------------------------+
| Operations   (live) Updated 14:05:12  [pause updates]   Site: [All v]     |
+------------------+-------------------+-----------------------------------+
| Revenue today    | Sites online      | Active alerts            [View all]|
| 18 420 $         | 41 of 44          | (!) 3 Critical  (i) 9 Warning     |
| +6% vs last Tue  | 3 degraded        |                                   |
+------------------+-------------------+-----------------------------------+
| Sites (table)  Name | Status | Meters online | Revenue today | Last data  |
| Kasai   | Online (ok)    | 812/820 | 2 140 $ | 14:04                      |
| Ndolo   | Degraded (!)   | 300/410 | 910 $   | 13:41 (stale)              |
+--------------------------------------------------------------------------+
| Alerts feed (newest first, FR-07)                                        |
| 14:02 Critical Meter offline cluster, Ndolo  [Acknowledge] [Open]        |
+--------------------------------------------------------------------------+
```

Live updates come over SSE: the client calls `POST /dashboard/stream-tickets` and opens `GET /dashboard/stream?ticket=...` (D-18); details in 5.6. The dashboard is shown to operations (full), finance (revenue cards only) and support (ticket widgets only); the administrator has no dashboard. Update rules:

- Status icons carry text ("Online", "Degraded", "Offline") plus shape; color is secondary.
- A "Live" indicator shows three states: Live (SSE connected, last event under 30 s), Reconnecting (with retry count), Stale (no event for 60 s or disconnected). In Stale, the header turns into a persistent banner "Data may be out of date. Last update 14:01:08" and each card shows "as of" time. Per-row `Last data` older than 30 minutes is flagged "(stale)" in text.
- Updates do not steal focus or reflow the row under the cursor. A table row that updates mutates cell text in place; sorting does not re-run until the user asks ("3 changes. Apply sort") to avoid rows jumping.
- Screen-reader announcements: only critical alert arrivals and stale transitions are announced (section 7.5). Metric changes are not announced; users read them on demand. A "Pause updates" button (WCAG 2.2.2) freezes rendering and buffers events.
- Wall-display mode `?display=wall`: large type (min 24 px body), the same semantics, no interactive hover-only information, auto-reload on new build id.

| State | Behaviour |
|---|---|
| Loading | Card and table skeletons; SSE connects after first snapshot |
| Empty | "No sites configured." link to Sites (admin) |
| Error | Snapshot fails: error card with Retry, SSE not started |
| Partial | Revenue card failed but sites loaded: card shows "Unavailable", others live |
| Offline/Stale | Banner, values greyed with explicit timestamps, reconnect attempts shown |

### 3.7 Reconciliation exceptions queue (FR-14)

```
+--------------------------------------------------------------------------+
| Reconciliation   Statement: 2026-10-23 Provider M-Pesa   Matched 99.2%    |
+--------------------------------------------------------------------------+
| Exceptions (31 open)  Type: [All v] Age: [All v] Assigned: [Me v]         |
| ID  | Type              | Provider ref | Amount  | Age | Assignee | Action |
| 771 | In statement only | MP123...     | 12,00 $ | 2d  | -        | Open   |
| 772 | Amount mismatch   | MP124...     | 5,00/5,50| 1d | Chantal  | Open   |
+--------------------------------------------------------------------------+
Detail: provider line | ledger posting | difference | timeline
Resolve: [Create adjusting posting...] [Mark as provider error] [Escalate]
```

Item kinds shown (schema vocabulary): statement only, ledger only, amount mismatch, duplicate in statement, currency mismatch; statuses open, pending approval, resolved, written off. Every money-posting resolution (and manual adjustment, refund, write-off) is a proposal that opens an approval (D-15): the finance officer (maker) fills a reason (mandatory resolution note), sees the debit and credit lines with explicit direction and the sum zero ("Debits 5,50 $ = Credits 5,50 $") and presses "Submit for approval"; the item moves to `pending_approval` with "Waiting for finance lead". Nothing is posted until the checker approves in section 3.11. `match_to_top_up` shows the system-obtained provider evidence (status pull or statement fetched by a job) next to the uploaded file, and cannot be submitted on an uploaded file alone. If the approver is linked to the customer (`staff_customer_link`) the server refuses and the UI says "You cannot approve this: you are linked to this customer". An `accounting_period_locked` error (`period_locked`) renders "This accounting period is closed. Ask the finance lead to reopen it" without partial state. Postings are never optimistic. Other server errors are shown in simple words with the correlation id. The threshold is zero in v1, so there is no "direct below threshold" path.

Bulk statement upload: size and row caps are shown before choosing a file; cells beginning with `=`, `+`, `-` or `@` are shown as text (formula neutralisation) and exports prefix them with an apostrophe.

States: Loading skeleton; Empty "No open exceptions for this statement" with a positive but plain message; Error retry; Partial "Statement still importing, 80 percent" (progress element); Offline read-only.

### 3.8 KB editor (FR-17)

Two-pane editor: left a form (title, language EN or FR, tags, body in Markdown with a plain textarea and a preview tab, not a rich-text widget, for accessibility and predictability); right the version history list with diff view. Support agents with `kb:edit` write and save drafts. Publishing needs `kb:publish` (held by administrators) and a second approver (D-36): the publisher presses "Request publication", which creates an approval (3.11) showing the exact article text and its hash; the approver must be a different person. The article records `published_by` and `approved_by`, shown in the version list. The forbidden-promise lint results appear before submission, including amounts that appear in retrieved passages. "KB editor" is a permission, so the nav entry appears for anyone holding `kb:edit`, whatever their role. Actions: Save draft, Request publication (needs `kb:publish`). Every published version is immutable; AI drafts cite `article_id@version`. A banner warns when editing an article that is cited by recent drafts. A translation panel shows EN and FR side by side and flags "FR out of date" if EN has a newer version. Unsaved changes: `beforeunload` guard and a visible "Unsaved changes" status. States: Loading, Empty (no articles, Create), Error (save failed, content preserved), Partial (history failed), Offline (editing local, Save disabled).

### 3.9 Audit log viewer (FR-15)

A filterable accessible table: time (UTC and local), actor, role, action, entity, outcome, correlation id; filters for actor, action family (auth, payment, ledger, AI prompt/output, admin, export), date range; expandable row detail showing the JSON payload (AI prompt and output stored with redactions, NFR-08). Read-only, no edit or delete controls exist at all. Streams are role-filtered by the server: finance sees money streams, operations system streams, the administrator all streams (read only). A "Chain verified" indicator shows the latest verifier job result and the daily anchor date (D-22); a failed verification shows a prominent alert. Export to CSV is itself an audited action and needs step-up (3.12). Pagination is cursor based with "Previous" and "Next" links. States: Loading, Empty ("No events match"), Error, Partial (older partitions archived: "Events older than 400 days are in the archive, request access"), Offline.

### 3.10 Role and API key admin (FR-15, FR-18)

Users table with role select per user, last login, MFA status (TOTP, WebAuthn); role matrix page as a table (rows permissions, columns roles, generated from `api/permissions.yaml`) with text "Allowed" or "Not allowed" plus icons. Role grants, user creation and MFA reset do not take effect on save: they create an approval (3.11) and a step-up prompt (3.12) first. Granting `finance_officer` or `administrator` needs a second administrator; the administrator role page states that administrators have no money, ticket, customer-content or ask-the-data access. The last administrator cannot be demoted (message explains).

API keys: list (name, kind `gateway` or `integrator`, prefix in the form `sgh_<env>_<keyid>_...` with the secret never shown, scopes, source address range, rate limit, last used, expiry, status). Gateway keys are bound to a set of sites and require a source address range; the two kinds have disjoint scopes, so the scope checklist changes with the kind. Maximum life 180 days (expiry select stops at 180). Create key: name, kind, scopes, rate limit (tier names from the API tier table), expiry, then step-up and an approval by another administrator. After approval the creator sees the secret exactly once in a modal with a Copy button and a required acknowledgment checkbox "I stored this key", then never retrievable (only the HMAC form exists, FR-18). Revoke requires typing the key name and step-up. States: Loading, Empty ("No API keys. Create one for an integrator."), Error, Partial (usage stats missing), Offline read-only.

### 3.11 Maker-checker: pending approvals and review (D-15)

```
+----------------------------------------------------------------------+
| Approvals     [Waiting for me (3)] [My proposals (2)] [History]       |
+----------------------------------------------------------------------+
| Type              | Proposed by | Age | Subject               | Open |
| Adjustment 5,50 $ | Chantal     | 2h  | Customer c-91f (pseud.)| Open |
| Role grant        | Ibrahim     | 1d  | finance_officer -> Eli | Open |
| KB publication    | Marie       | 3d  | "Double payment refunds"| Open |
+----------------------------------------------------------------------+

Approval #912  Adjustment                        Status: waiting
Proposed by Chantal (finance officer) 10:12      Reason: duplicate fee
EXACT PROPOSED PAYLOAD (read-only, what will be executed)
  debit  fees               5,50 USD
  credit customer_wallet    5,50 USD     sum = 0
  payload hash: 9f3a...c1   Hash check: (ok) matches what Chantal submitted
[ Approve ... ]  [ Reject... ]     (disabled for the maker, with the reason)
```

Rules: the page lists only approval types the viewer may check (from permissions). The payload view is generated from the stored proposal, not from the maker's form, and is shown as structured rows plus the raw canonical JSON in a disclosure; the hash is displayed and the status "Hash check" is one of: matches (the payload shown hashes to the stored hash), mismatch (blocks Approve; "This proposal changed after it was submitted. Reject it and ask for a new one"), or unavailable. If the viewer is the maker the Approve and Reject-as-checker buttons are disabled with visible text "You proposed this. Another person must approve it." (user and session are both compared server side; the UI is a courtesy). The viewer may withdraw their own proposal. Approve opens the step-up prompt if the last authentication is older than 5 minutes, then a confirmation dialog repeating type and amount. Reject needs a reason. Conflict-of-interest and four-eyes server errors are mapped to plain sentences. Approvals expire after a configured period and then show "Expired".

| State | Behaviour |
|---|---|
| Loading | Skeleton rows; list region `aria-busy` |
| Empty | "Nothing is waiting for you." |
| Error | Retry, correlation id; Approve disabled if the payload could not load |
| Partial | List loaded but one payload failed: row shows "Details unavailable" and cannot be opened for approval |
| Offline | Read only |

Approval types, their subject and approver, as shown: reconciliation resolution, manual adjustment or refund and write-off (finance lead); tariff change (operations configuration approver); role grant, user creation, MFA reset, API-key creation, KB publication (second administrator or second person as the matrix says); erasure approval and data export (a second approver with the data-protection role). Approval of money-affecting reply drafts (finance co-approval, D-35) uses the same list.

### 3.12 Step-up prompt (D-16)

A modal dialog "Confirm it is you" appears when an action needs authentication within the last 5 minutes: approvals, role grants, API-key creation, data export and erasure. It offers the strongest enrolled factor first (WebAuthn security key, mandatory for finance officers and administrators before go-live; otherwise TOTP code) and a link to use a backup code. The dialog states the action it protects ("To approve adjustment #912"), keeps the user on the page, and on success replays the action once; failure shows an error summary and a retry; three failures sign out. TOTP and backup-code fields allow paste (SC 3.3.8) and use `autocomplete="one-time-code"`. WebAuthn has a visible fallback button so no flow depends on a single gesture. The prompt traps focus like any dialog and returns focus to the invoker.

### 3.13 Security settings: WebAuthn, TOTP, backup codes, reset (`/me/security`)

| Flow | Design |
|---|---|
| Enrol WebAuthn | Name the key, press "Register key", browser prompt; success announced; at least two keys recommended with a banner until a second key or backup codes exist; finance officers and administrators see "Required before go-live" until enrolled |
| Enrol TOTP | QR code plus the manual key as text (accessible alternative), a first-code confirmation field; local accounts only (company identity provider users see "Managed by your company login") |
| Backup codes | Ten one-time codes generated after enrolment, shown once with Copy and Download buttons and a required acknowledgment; regenerate needs step-up and invalidates the old set; remaining count is shown |
| Locked out | The login page has "I lost my second factor"; it explains options: use a backup code, or ask an administrator. No self-service reset without proof |
| Administrator reset of someone's MFA | In user admin: "Reset MFA" creates an approval that a second administrator must check (D-16); the target user receives a notification and must re-enrol on next login; sessions are revoked |
| Sessions list | Active sessions with device and last use, "Sign out other sessions" |

### 3.14 Privacy requests: export and erasure (FR-20, D-29, D-21)

Customer side (`/account`): "Download my data" and "Delete my account" are separate buttons. Each begins with a second proof of identity (a fresh one-time code to the registered number; the response never confirms whether the number exists). The erasure page first lists blocking reasons returned by the server, each with a plain explanation and a next step:

| Blocking reason | Message shown | Next step |
|---|---|---|
| Wallet is not zero | "You still have 0,40 $ in your wallet. Use it or ask for a refund first." | Top up use, or ticket |
| Tokens not yet acknowledged | "Energy is still being delivered to your meter. Try again later." | Wait |
| Open dispute or legal hold | "We cannot delete your account while a request is open." (no detail on holds) | Ticket |

If there are no blockers, the customer confirms and sees the cooling-off notice: "Your request will be reviewed. We wait 7 days before deleting anything, and we will send a message to your current contact so you can cancel." A request status page shows Requested, Cooling-off until a date, Under review, Completed or Cancelled, and a "Cancel my request" button available until completion. The completion text explains that financial records are kept as required by law with personal details removed (pseudonymised), not a promise of total deletion. Export shows "Preparing" and then a link valid for a short period; the link opens only in a signed-in session.

Staff side (`/admin/privacy`, visible only to the `dp_admin` attribute): queue of export and erasure requests with status, blocking reasons, cooling-off end date and who approved. Approving an erasure needs two distinct approvers (the data-protection administrator and a second approver) through the approvals screen and step-up; the approve button is disabled until the cooling-off period has ended, with the date as text. A hold flag can be set with a reason. No customer content is displayed here beyond a pseudonymous id.

### 3.15 Incident linking (FR-07, FR-08)

From the ticket queue (checkbox column, with a visible "Select row" label) or a ticket page, staff with ticket access choose "Link to incident". The dialog offers an existing incident (search) or "New incident" (title, site, severity). Up to 100 tickets can be linked in one action; the counter "37 of 100 selected" is announced politely as it changes and the 101st checkbox is disabled with the explanation. The result summarises "37 linked, 2 already linked, 0 failed" (partial success is listed, not hidden). The incident page shows linked tickets (cap 100 per incident), site, related alerts and a single status message that staff may use for customer replies; the draft system still needs per-ticket approval, no mass auto-send. Operations see technical tickets read-only plus the incident page.

### 3.16 Accounting-period lock (FR-04, FR-14)

Finance lead only (`finance_lead` attribute) on `/accounting-periods`: a table of periods with status Open or Locked. "Lock period" opens a confirmation dialog stating the effect ("No posting dated in October 2026 can be created or changed after locking. Corrections go into the next open period."), the number of open reconciliation exceptions in the period (locking with open exceptions needs an explicit checkbox "I accept N unresolved exceptions"), a step-up prompt, and an approval from another finance lead or administrator as permissions define. Reopening is a separate approval with a mandatory reason. Attempts to post into a locked period elsewhere surface the `period_locked` message from 3.7.

---

## 4. AI UX rules

| Rule | Detail |
|---|---|
| Always labelled | Every AI-produced element (triage, summary, draft, SQL, interpretation) carries an "AI" tag with icon and text, and an `aria-label` of "AI generated". The tag is part of the component (`<AiBadge/>`), not left to feature teams. |
| Never auto-send | There is no code path that submits a draft without a human pressing Approve in a confirmation dialog (invariant 2). No timer, no "auto-approve low risk", no keyboard default. Frontend tests assert the absence (section 9.3). |
| Uncertainty shown in words | Confidence is displayed as "High / Medium / Low confidence" with a hint on hover and focus. Low confidence adds "Check carefully". Numbers like 0.73 are not shown to non-admins because they imply false precision. |
| Citations | Numbered markers and a sources list with article title, version and quoted passage. A sentence with no citation that states a fact is listed under "Not backed by a source". Citations point to immutable KB versions (FR-17). |
| Refusals | A refusal (model `refusal` stop reason, or policy block) shows a neutral message: "The AI declined this request." No partial text is shown as a draft. Staff can proceed by hand. The event is logged. |
| Failures | Plain text with what failed, what still works, what to do ("Write by hand"), correlation id. No raw provider errors. Timeouts offer Retry once; budget exhaustion shows the limit and who to ask (NFR-13). |
| Cost and latency | Visible only to the Administrator, and only on `/admin/ai-spend` (per feature, per day, dollars with token-equivalents, D-34). The administrator cannot open tickets, drafts or ask-the-data (D-14), so no per-item cost disclosure exists on those screens. Cost fields are omitted by the API for every other role and absent from their DOM (not hidden by CSS). Agents see only the budget-exhausted message, not amounts. |
| Triage is auto-applied (D-32) | Suggestions are applied at once but always marked "AI suggestion" until a person confirms or overrides; override is one click and recorded. Drafts and anything customer-facing remain never auto-sent. |
| Progress | Long operations show a status with elapsed time and a Cancel. No fake typing animation; text appears when complete (avoids reading half-sentences, simpler for screen readers). |
| Personal data | When triage flags personal data, the panel says so and reminds that data is redacted before reaching the model (NFR-07). Drafts show customer name placeholders resolved server side after generation. |
| Languages (FR-16) | Output language follows the ticket language (triage detects it) and the agent can switch the draft language with a select, then Regenerate; the UI shows the language of the draft with `lang` attribute on the text so screen readers pronounce correctly. UI chrome language is the agent's own preference; they are independent. KB articles missing in the draft language show a "Source is in English" note on the citation. |
| No anthropomorphism | The AI is never given a name or avatar, no first-person chat voice in staff UI, no apologies. |
| Feedback | One-click "Helpful / Not helpful" on triage and summary, stored for evaluation, keyboard operable. |
| Summaries (FR-11) | Shown in a collapsible region above the thread labelled "AI summary", generated on demand or on thread change, with a "Generated 10:40, thread has 2 newer messages" staleness note. |

---

## 5. Component architecture

### 5.1 Folder structure (feature folders)

```
web/
  next.config.ts   middleware.ts   tsconfig.json (strict)
  messages/        fr/*.json  en/*.json                 (next-intl message files)
  src/
    app/
      [locale]/
        layout.tsx                 (html lang, NextIntlClientProvider, skip link)
        (public)/   page.tsx  help/  tariffs/  status/  login/
        (portal)/app/   layout.tsx  page.tsx  top-up/  history/  support/  account/
                        error.tsx  loading.tsx  (each route folder may add its own)
        (staff)/staff/  layout.tsx  dashboard/  tickets/  ask/  approvals/ ...
                        error.tsx  loading.tsx
        not-found.tsx
      api/csp-report/route.ts
      global-error.tsx
    features/                      (client components, hooks, schemas; no routes)
      top-up/   components/ hooks/ schema.ts index.ts
      balance/ history/ support-customer/
      tickets/ triage/ ai-draft/ ask-data/ incidents/
      dashboard/ sites/ customers/
      reconciliation/ approvals/ accounting-periods/ kb/ audit/
      admin-users/ admin-api-keys/ ai-spend/ privacy/ notifications/ security-settings/
    shared/
      ui/       (design system: Button, Dialog, Table, Field, Toast, AiBadge...)
      api/      generated/schema.d.ts   client.ts   queryKeys.ts
      auth/     token-store.ts  session.ts  guards.tsx  step-up.tsx
      i18n/     routing.ts  request.ts  navigation.ts
      lib/      money.ts  dates.ts  sse.ts  errors.ts
      styles/   tokens.css  themes.css
    test/       msw handlers, fixtures, a11y helpers
  e2e/          playwright specs
```

Rules: `src/app` contains only routing files (`page.tsx`, `layout.tsx`, `loading.tsx`, `error.tsx`) and keeps them thin: a `page.tsx` imports one component from a feature's `index.ts` and renders it. A feature exports only its `index.ts` public surface; features import from `shared`, never from each other except via `index.ts`. Server state lives in TanStack Query, UI state in component state, and a tiny `zustand` store only for cross-cutting UI (theme, stream connection status). No global Redux. Server components never import from `features/*` internals that use browser APIs; any file using hooks, `window` or the token store starts with `'use client'`. The TanStack Query provider and the token store are mounted once in a client `Providers` component in each authenticated area's `layout.tsx`, not in the root layout, so public pages ship neither.

### 5.2 Design system and tokens

Tokens as CSS custom properties (so theme switch costs no JS) in `tokens.css`, exposed to TypeScript through a generated type. Spacing scale 4 px; type scale base 16 px (portal 18 px on screens under 400 px width for sunlight readability), line-height 1.5; radius 8 px; minimum control height 44 px (portal 48 px).

Themes: light, dark, and `forced-colors` support (use system colors, borders not just background). Theme follows `prefers-color-scheme`, with a manual override saved in a non-sensitive first-party cookie `hub_theme` (so a server component can write the correct `data-theme` attribute into the HTML and avoid a flash, with no inline script needed under the strict CSP).

| Token | Light | Dark | Contrast target |
|---|---|---|---|
| `--bg` | #FFFFFF | #10151D | base |
| `--text` | #1A1F2B | #E8ECF2 | at least 15:1 on `--bg` in both |
| `--text-muted` | #4A5263 | #A9B2C3 | at least 7:1 (exceeds 4.5:1 AA) |
| `--primary` (buttons, links) | #0B5CAD | #7DB7F5 | at least 6:1 as text on `--bg`; button label on primary at least 4.5:1 |
| `--success` | #1B6E3C | #7FD39A | at least 5.5:1 text on bg |
| `--warning` | #8A5300 | #F2B561 | at least 5.5:1 text on bg |
| `--danger` | #B3261E | #FF9C94 | at least 5.5:1 text on bg |
| `--border-control` | #6B7385 | #8B95A8 | at least 3:1 against bg (SC 1.4.11) |
| `--focus-ring` | #0B5CAD + 2 px white offset | #FFFFFF + 2 px dark offset | at least 3:1 against both adjacent colors (SC 2.4.11, 1.4.11) |

A Vitest test (`tokens.contrast.test.ts`) computes the relative luminance ratios from the actual token values for every foreground/background pairing used by components and fails below the targets above. Large text (24 px or 19 px bold) may use 3:1 but the system does not rely on that exception.

Core components and their accessibility contract: `Button` (native `<button>`, loading state sets `aria-disabled` and keeps focus), `IconButton` (requires `label` prop, TypeScript enforces), `Field` (label, hint, error wired by `aria-describedby`, `aria-invalid`), `Dialog` (native `<dialog>` with focus return), `DataTable` (native table, caption required, sortable header buttons), `StatusBadge` (icon + text + color), `MoneyText`, `DateText`, `AiBadge`, `LiveRegion`, `Toast` (never the sole carrier of an error), `Skeleton` (`aria-hidden` plus a single status text).

### 5.3 Forms and validation

React Hook Form with Zod schemas. Zod schemas for request bodies are derived from the OpenAPI types where possible (via `openapi-zod-client` or a hand-kept schema checked against generated types by a type test) so client and server agree; server errors (RFC 9457 problem JSON with `errors[]` carrying a JSON pointer) are mapped back to fields. Validation runs on blur and on submit (not on every keystroke, which is hostile to screen-reader users); after a failed submit, focus moves to an error summary at the top listing each error as a link to its field (SC 3.3.1, 3.3.3). Amounts are entered as text with `inputmode="decimal"`, parsed with the locale-aware parser (accepts "12,50" and "12.50"), converted to integer minor units immediately, and never held as floating point. Autocomplete attributes (`tel`, `one-time-code` with `autocomplete="one-time-code"`) are set (SC 1.3.5).

### 5.4 Data fetching and caching (TanStack Query)

| Concern | Decision |
|---|---|
| Client | Generated typed client (5.8) wrapped by `queryOptions` factories per feature, so keys and fetchers live together |
| Keys | `['balance', customerId]`, `['tickets', filters]`, etc., built in `queryKeys.ts` |
| Stale times | Balance 0 (always refetch on mount and focus, money), history 30 s, ticket queue 15 s, KB list 5 min, sites 60 s, dashboard snapshot fed by SSE with staleTime Infinity while connected |
| Retry | 2 retries with exponential backoff for GET on network errors and 5xx; none for 4xx; mutations never auto-retry except those carrying an Idempotency-Key and marked safe (ticket reply) |
| Provider | `QueryClientProvider` lives in a client `Providers` component inside the `(portal)` and `(staff)` layouts only. One `QueryClient` per browser tab (created in `useState`, never module-level, so nothing is shared between users on a server render). Queries run only in client components; there is no server prefetch or dehydration of authenticated data (the server has no token). |
| Persistence | Portal only, app code and not the service worker (8.4): `persistQueryClient` to IndexedDB for balance, last 20 history items and open tickets only; `maxAge` 7 days; wiped on logout and on refresh-reuse sign-out; disabled in "Shared phone" mode; staff app persists nothing |
| Pagination | Cursor based, `useInfiniteQuery` only on portal history; staff tables use page links |
| Invalidations | After a payment mutation, invalidate `balance`, `history` and poll the payment; the portal has no live stream: it polls `GET /payments/{id}` (3 s, backing off) and refetches balance and history when the status reaches a final value; the staff dashboard alone uses SSE |
| Request dedup and cancel | Query keys dedupe; `AbortSignal` passed to fetch for ask-the-data and drafting cancel |

### 5.5 Optimistic updates policy

| Allowed (reversible, low consequence) | Forbidden (never optimistic) |
|---|---|
| Mark ticket assigned to me, change ticket status in queue, acknowledge alert, toggle notification preference, theme and language, mark KB draft saved status | Balance, top-up status, payment state, any ledger or reconciliation action, token issuance state, role or API key changes, sending a reply, approve or reject draft, erasure requests |

Optimistic changes roll back with a visible message on failure. Money screens render only server-confirmed values and show a pending indicator while a mutation is in flight.

### 5.6 SSE handling and reconnect

A `useEventStream` hook wraps the native `EventSource`. Because `EventSource` cannot set headers (D-18), before each connection the hook calls `POST /dashboard/stream-tickets` (normal bearer authentication) and opens `GET /dashboard/stream?ticket=<ticket>`. The ticket is single use, valid 30 seconds and bound to the caller's IP, so it is requested immediately before connecting and a failed or reused ticket is simply replaced by a new one; the balancer log format strips the query string. A stream lives at most 10 minutes: when the server closes it at the limit the hook reconnects with a new ticket without showing "Reconnecting" if the gap is under 2 seconds. The server re-checks role and token version on every heartbeat, so a revoked session ends the stream; the hook then shows the sign-in screen rather than retrying forever. Behaviours: exponential backoff 1 s to 30 s with jitter on errors; `Last-Event-ID` resume; on reconnect, fetch a fresh snapshot then apply events with `id` greater than the snapshot's `as_of_event_id` (avoids gaps and double-apply); connection status goes to the zustand store which drives the Live / Reconnecting / Stale indicator; tab hidden for over 5 minutes closes the stream and reopens on visibility; max one stream per tab, and `BroadcastChannel` lets one tab own the connection and share events to others (reduces server load toward 200 concurrent staff). Events are validated against Zod schemas; unknown event types are ignored, malformed ones are reported to telemetry.

### 5.7 Routing, error boundaries and role guards

Routing is the Next.js App Router with the file conventions of 5.1; navigation uses the locale-aware `Link`, `useRouter` and `redirect` exported from `src/shared/i18n/navigation.ts` (next-intl), never `next/link` directly, so the locale prefix is never lost. Code splitting is per route segment by default; heavy widgets use `next/dynamic` with `ssr: false` (section 8.2).

Guards are client components in `shared/auth/guards.tsx`, mounted in the `(portal)` and `(staff)` layouts. They run after the token is recovered (5.9):

- `RequireAuth`: while the session is being recovered it renders the area's skeleton (not the page); no session means `redirect` to the locale's login page with `?next=` (relative paths only, validated against an allow-list pattern to avoid open redirects).
- `RequireRole` and `RequirePermission` (`permission="kb:edit"`): render a "No access" page with an explanation and a link home (not a redirect loop).
- `RequireMfa` for staff: a session whose MFA is not complete is sent to the security page.
- The route table `shared/auth/routes.ts` declares required role or permission per route in one place and generates both the nav and the guard props, from `api/permissions.yaml` (5.1 test). Guards are conveniences only; the API enforces authorization (invariant 5), and the Next.js server cannot enforce them because it never sees the token.

Error and loading conventions (Next.js file conventions, one rule each):

| File | Rule |
|---|---|
| `loading.tsx` | Present in every route folder that fetches; a server component rendering the skeleton with `aria-busy="true"` and one visually hidden "Loading" text; shown by Suspense while the client bundle for the segment loads |
| `error.tsx` | Present in `(portal)/app` and `(staff)/staff` roots and in any route with its own failure mode; a client component with a heading "Something went wrong", a Retry button calling `reset()`, the correlation id when known, and focus moved to the heading on mount; it reports to the observability endpoint (route, correlation id, no personal data) |
| `global-error.tsx` | Last resort for layout failures: a bare page with reload link, text in both languages (it cannot use providers) |
| `not-found.tsx` | Localised "Page not found" with links home, `<h1>` focused |
| Widget level | A failing dashboard card or the AI panel uses a small `WidgetBoundary` (React error boundary component) so it degrades alone with "Unavailable" and Retry |

Route changes move focus to the page `<h1>` and update the document title through the metadata API (`generateMetadata` per page with a translated title, SC 2.4.2), and a route announcer (the one built into Next.js plus our `LiveRegion` for the page title) satisfies SC 4.1.3.

Suspense and streaming (safe use): public pages may stream below-the-fold server components (for example the help article list) inside `<Suspense>` with a skeleton fallback, because their data comes from public caches. Authenticated pages use Suspense only for lazy client chunks (`next/dynamic`), never to stream user data from the server, since the server has no user data.

### 5.8 API client generated from `api/openapi.yaml`

`openapi-typescript` generates types and `openapi-fetch` provides the typed client; `npm run api:gen` regenerates on spec change and CI fails if `web/src/shared/api/generated` differs from the committed output (drift check). A thin wrapper adds: base path `/api/v1`, `Authorization` header (access token from memory), `Accept-Language`, `X-Request-Id` (client-generated UUIDv7, correlated in logs, NFR-09), `Idempotency-Key` helper for unsafe methods (7-day window for top-ups, 24 hours elsewhere, D-12), one-time refresh-and-retry on 401 (single-flight to avoid refresh stampede; the refresh call sends only the HttpOnly cookie, `credentials: 'include'`, to `/api/v1/auth`, and receives only a new access token in the body), step-up replay (a `step_up_required` problem opens the step-up dialog, section 3.12, then repeats the request once), and problem-JSON error normalisation into a typed `ApiError` with `code`, `message_key` (an i18n key supplied by the server so error text is translatable) and `correlationId`. The client is created in one place (`shared/api/client.ts`) with `createClient<paths>({ baseUrl: '/api/v1' })` from `openapi-fetch` and middleware that attaches the headers above; the exact call shape is `client.GET('/payments/{id}', { params: { path: { id } } })`. The cookie endpoints (`POST /auth/refresh`, `POST /auth/logout`) are the only calls made with `credentials: 'include'`, and each sends `X-Hub-CSRF: 1` (D-46); the browser sets `Origin` itself. Every other call uses `credentials: 'omit'` and the bearer header. Mock Service Worker handlers are generated from the same spec for tests.

### 5.9 Access token in memory and recovery after reload (D-17, D-44)

`shared/auth/token-store.ts` holds the access token in a module variable (not React state, not storage), with `getToken()`, `setToken()`, `clear()` and a subscription for the session state (`recovering`, `signedIn`, `signedOut`). On any full page load of an authenticated area, the token is gone, so the area's `Providers` component runs recovery before rendering the page: `POST /api/v1/auth/refresh` with the cookie and `X-Hub-CSRF: 1`; success returns a new access token in the body (the cookie is rotated by the server); a 401 means signed out and the guard redirects to login. Recovery is single-flight (one in-flight promise shared by all callers, including the 401-retry path of the API client). Because refresh tokens rotate with reuse detection and no grace window, two tabs refreshing at once could look like reuse: a `BroadcastChannel('hub-auth')` plus the Web Locks API (`navigator.locks.request('hub-refresh', ...)`) serialises refresh across tabs, and the tab that refreshed shares the new access token with the others over the channel (never written to storage). A scheduled refresh runs 60 seconds before the 10-minute expiry only while the user is active; idle limits (D-17) end the session rather than extend it. Logout and refresh-reuse calls clear the store, the query cache and IndexedDB persistence in every tab. Step-up (3.12) is a `step_up_required` problem handled in the client wrapper, not a separate token.

---

## 6. Internationalisation (NFR-11, FR-16)

### 6.1 next-intl structure and locale routing

- Library: `next-intl` for the App Router. Locales: `fr` (default) and `en`, declared once in `shared/i18n/routing.ts` with `defineRouting({ locales: ['fr','en'], defaultLocale: 'fr', localePrefix: 'always' })`. Every URL has a prefix, `/fr/...` or `/en/...`; the middleware redirects `/` to the best locale (order: `hub_locale` cookie holding the saved preference, `Accept-Language`, default `fr`). The language switcher writes the cookie and links to the same page in the other locale (`hreflang` alternates are emitted on public pages; SC 3.1.1: `<html lang>` is set from the route param in `[locale]/layout.tsx`).
- Messages: one JSON file per namespace per locale, `messages/fr/topup.json` and `messages/en/topup.json`; namespaces `common`, `portal`, `topup`, `tickets`, `ai`, `dashboard`, `recon`, `approvals`, `privacy`, `admin`, `errors`, `public`. A layout loads only the namespaces its area needs (`request.ts` merges them), so the portal never ships staff messages. Server components use `getTranslations('ns')`; client components get messages through `NextIntlClientProvider` given only that area's namespaces. French is the fallback for the portal and English for staff if a key is missing; a CI check fails on any missing key, so fallback is only a safety net.
- Keys are semantic, never the English sentence: `topup.confirm.title`. Messages use ICU syntax, named arguments, no string concatenation; rich text uses `t.rich('key', { b: (chunks) => <strong>{chunks}</strong> })`.
- Mixed-language content (AI drafts in another language, KB quotes) carries its own `lang` attribute (SC 3.1.2).
- Server-supplied error `message_key` values resolve in the `errors` namespace; unknown keys show a generic message plus code.
- Plurals use ICU plural syntax and CLDR rules, for example `{count, plural, =0 {Aucune demande ouverte} one {# demande ouverte} other {# demandes ouvertes}}` (French treats 0 and 1 as `one`; `=0` is written explicitly where a distinct zero reads better; CLDR also has a French `many` category for large round numbers, which the lint requires to be present for the keys that use it).

### 6.2 String extraction and workflow

A script (`npm run i18n:check`, using a small TypeScript AST scan of `useTranslations` and `getTranslations` calls plus `next-intl`'s typed message keys via a global `IntlMessages` type generated from `messages/en`) fails when code references a missing key or a key is unused, so extraction is by type checking and a unit test rather than a separate extractor. Developers add English strings first; French is translated by a professional translator via the translation platform (a Git-synced tool such as Weblate), reviewed by a French-speaking staff member and, for customer-facing strings, by a field team member in the DRC for local vocabulary (for example "recharge" vs "rechargement"). A glossary fixes terms: "top-up = recharge", "meter = compteur", "token = code d'énergie" (never "jeton"), "balance = solde", "ticket = demande d'assistance" on the portal. Status: translation memory shared with the backend notifications (FR-19) so SMS and UI wording agree.

Translation freshness: PRs cannot merge with untranslated keys on `main`; a feature branch can, and the pseudo-locale and the French file receive the English text prefixed `[EN]` visibly so gaps are never silent in staging.

### 6.3 French typographic rules and formatting

| Topic | Rule |
|---|---|
| Spacing | Narrow no-break space (U+202F) before `;` `!` `?` and `:` takes a no-break space (U+00A0); non-breaking space inside guillemets: « texte » |
| Quotes | Guillemets « » in French, curly quotes in English |
| Apostrophe | Typographic ’ in French strings, enforced by a lint rule on locale JSON |
| Capitalisation | Sentence case for headings and buttons; no Title Case in French; days and months lowercase |
| Numbers | Group separator narrow no-break space, decimal comma: 1 234,50 (from `Intl.NumberFormat`, never hand-built) |
| Money | `Intl.NumberFormat(locale, {style:'currency', currency})` on minor units divided by the currency's fraction digits (USD 2, CDF 2 as per ISO 4217; the code reads `resolvedOptions().maximumFractionDigits` rather than assuming). French shows `12,50 $US`; a UI setting "show currency code" displays "12,50 USD" because "$" is ambiguous for CDF users. CDF is stored at two decimals per ISO 4217 (accepted C-06); amounts above 100 FC on summary views are shown in whole francs (`28 000 FC`), amounts of 100 FC or less and every money-moving screen (top-up summary, receipt, adjustment and posting dialogs, wallet remainder) show the exact two decimals, so a non-zero minor unit is never hidden. Rounding for display is rounding half to even only when no minor unit is non-zero, otherwise the screen switches to exact display |
| Dates and times | `Intl.DateTimeFormat`; French `12 oct. 2026, 14:05`; time zone shown for staff as the site's zone (Africa/Lubumbashi or Africa/Kinshasa) with UTC in the audit view; relative times ("il y a 2 h") via `Intl.RelativeTimeFormat` with absolute time in a `title`/visible alternative |
| Plurals | ICU plural messages (6.1); zero in French is singular, covered by tests |
| Formatting API | In components use next-intl's `useFormatter()` (`format.number`, `format.dateTime`, `format.relativeTime`, `format.list`), which wrap `Intl` with the active locale and a fixed time zone; server and client therefore render identical text (no hydration mismatch). Custom money formatting still goes through `shared/lib/money.ts`, which calls the same `Intl` with the locale argument |
| Lists | `format.list` (`Intl.ListFormat`) |
| Sorting | `Intl.Collator('fr')` for accents |
| Currency in tables | Right aligned, tabular figures, currency in column header not repeated per cell when single-currency |

Money formatting lives only in `shared/lib/money.ts`, which takes `{amountMinor: bigint-safe string or number within safe range, currency}`; no component calls `toFixed`. Amounts above the JS safe integer range are impossible for this domain but the API sends minor units as integers and the type test asserts `Number.isSafeInteger`.

### 6.4 Text expansion and layout

French text is roughly 15 to 30 percent longer than English; short UI labels can expand 40 percent. Rules: no fixed-width buttons or tabs; flex and grid with wrapping; buttons grow in height; navigation labels may wrap to two lines at 360 px; no truncation of critical text (amounts, states, button labels); tables get horizontal scroll containers with focusable region if overflow. A pseudo-locale (`xx-pseudo`, English with accents, 40 percent padding such as `[!! Ťöp úp !!]`) runs in Storybook and visual tests to catch clipping before translation arrives.

### 6.5 Testing translations

CI checks: key parity EN to FR; no empty values; ICU argument parity (the same `{names}` and plural/select structure in both files, checked by parsing with `@formatjs/icu-messageformat-parser`); plural forms complete; French punctuation lint; pseudo-locale visual snapshot; a Playwright run of the top-up and ticket flows in FR at 360 px with 200 percent text; axe runs in both languages (labels and `lang` correct). Manual: a French speaker reviews every release's changed strings in context in the staging build.

---

## 7. Accessibility plan to WCAG 2.2 AA (NFR-10)

### 7.1 Checklist mapped to success criteria

| SC | Requirement | How this design meets it | Verified by |
|---|---|---|---|
| 1.1.1 Non-text content | Alt text and labels | Icons decorative `aria-hidden` with adjacent text; icon-only buttons require `label`; charts have a data table alternative | ESLint jsx-a11y, axe |
| 1.3.1 Info and relationships | Semantics | Native table, list, heading, landmark elements; one `<h1>` per route | axe, SR smoke |
| 1.3.2 Meaningful sequence | DOM order matches visual | No CSS reordering of focusable content | Playwright tab-order test |
| 1.3.4 Orientation | No lock | Portal works in both orientations | manual |
| 1.3.5 Identify input purpose | Autocomplete | `tel`, `one-time-code`, `name`, `email` | axe |
| 1.4.1 Use of color | Not sole carrier | Status = icon + text + color | design review, grayscale snapshot test |
| 1.4.3 Contrast minimum | 4.5:1 text | Tokens in 5.2, computed test | token test, axe |
| 1.4.4 Resize text | 200 percent | rem units, tested at 360 px with 200 percent | Playwright |
| 1.4.10 Reflow | 320 CSS px, 400 percent zoom | No two-dimensional scroll except data tables in focusable scroll regions | Playwright viewport 320 |
| 1.4.11 Non-text contrast | 3:1 | Control borders, focus ring, icons | token test |
| 1.4.12 Text spacing | Overrides do not break | No fixed heights; tested with spacing bookmarklet CSS | Playwright injected CSS |
| 1.4.13 Content on hover or focus | Dismissable, hoverable, persistent | Tooltips via a single component with Escape and hover persistence | component tests |
| 2.1.1, 2.1.2 Keyboard, no trap | All functions | Dialogs use native `<dialog>` with Escape; focus returns | Playwright keyboard flows |
| 2.1.4 Character key shortcuts | None single-key by default | Navigation shortcuts use modifier chords and can be disabled | review |
| 2.2.1 Timing adjustable | Session timeout warning | Staff idle limit 30 min, absolute 8 h; customer idle limit 5 min, absolute 12 h (D-17). A warning dialog appears 2 minutes before the staff idle limit and 60 seconds before the customer idle limit with "Stay signed in" (extend by activity or button, at least ten times the default is not possible under the security limit, so the exception for security applies and the warning gives the option to extend). Absolute limits show a notice 5 minutes ahead and preserve typed drafts locally. The 30-second recall window is not a timeout on the user. | Playwright with fake timers |
| 2.2.2 Pause, stop, hide | Moving content | Dashboard Pause updates; no carousels, no auto-playing media | review |
| 2.3.1 Three flashes | None | No flashing; alert indicators do not blink | review |
| 2.4.1 Bypass blocks | Skip link | "Skip to main content" first focusable, plus landmarks | axe |
| 2.4.2 Page titled | Per route | `document.title` updated per route and language | Playwright |
| 2.4.3 Focus order | Logical | Focus managed on route change and dialogs | Playwright |
| 2.4.4, 2.4.6 Link purpose, headings and labels | Descriptive | No "click here"; "See all history" with context | review, lint |
| 2.4.7 Focus visible and 2.4.11 Focus not obscured (min) | Visible focus not hidden | `scroll-padding` equal to sticky header and bottom tab bar heights; sticky elements never cover the focused element; ring 2 px with offset | Playwright check that focused element's bounding box is not covered |
| 2.4.13 Focus appearance (AAA, adopted as target) | 2 px ring at 3:1 | Same ring | token test |
| 2.5.1 Pointer gestures | Single-pointer alternative | No swipe or drag-only features; reorder not offered | review |
| 2.5.2 Pointer cancellation | Up-event activation | Native buttons | review |
| 2.5.3 Label in name | Visible text in accessible name | `aria-label` never differs from visible text beginning | lint rule, axe |
| 2.5.7 Dragging movements (2.2) | Alternative | No drag interactions | review |
| 2.5.8 Target size minimum (2.2) | 24x24 CSS px | Policy: 44 px on staff, 48 px on portal (section 7.4) | Playwright size assertions |
| 3.1.1, 3.1.2 Language of page and parts | `lang` | Set by i18n, AI text and KB quotes carry own `lang` | axe |
| 3.2.1, 3.2.2 On focus, on input | No surprise | Selects and filters apply on explicit Apply, except sort buttons | review |
| 3.2.3, 3.2.4 Consistent navigation and identification | Same order | One shell per app, one component per concept | review |
| 3.2.6 Consistent help (2.2) | Help in same place | Help link and "Contact support" in the same footer/tab position across portal pages | review |
| 3.3.1, 3.3.2, 3.3.3 Error identification, labels, suggestion | Forms | Section 7.6 | Playwright, axe |
| 3.3.4 Error prevention (legal, financial) | Review, confirm | Top-up summary step, draft approve dialog, posting confirmation, key revocation typed confirm | Playwright |
| 3.3.7 Redundant entry (2.2) | Do not ask twice | Phone, provider remembered; ticket form prefilled with payment id; returning to step 1 keeps values | Playwright |
| 3.3.8 Accessible authentication minimum (2.2) | No cognitive test | One-time code field allows paste and autofill (`autocomplete="one-time-code"`); no CAPTCHA puzzle; password fields allow paste and password managers; TOTP field allows paste | Playwright |
| 4.1.2 Name, role, value | Custom widgets | Native elements first; ARIA only per APG patterns | axe, SR |
| 4.1.3 Status messages | Live regions | Section 7.5 | SR smoke |

### 7.2 Keyboard and focus design

- Tab order follows DOM order; no positive `tabindex`. Skip link first. Landmarks: `header`, `nav` (labelled, distinct for primary and tab bar), `main`, `aside` for triage panel (labelled), `footer`.
- On route change, focus moves to `<h1>` (with `tabindex="-1"`) and a polite announcement of the new title is made through the route announcer region.
- Dialogs: native `<dialog>` with `showModal()`, focus to the first field or, for destructive confirmations, to the Cancel button; Escape closes; focus returns to the invoker; inert background.
- Drawer (citation or KB article): modal behaviour on narrow screens, non-modal complementary region on wide screens with a close button first in order and focus moved into it on open.
- Tables: not an ARIA grid. Rows are not interactive; each row has a real link and buttons. This keeps Tab navigation predictable and screen-reader table commands working.
- Toasts: never contain the only way to proceed, stay at least 8 seconds, pausable on hover and focus, and are reachable (a "Notifications" region keeps history for the session).
- Visible focus ring token (5.2) on every interactive element; `:focus-visible` used, never `outline: none` without replacement (stylelint rule).
- Shortcut help (`?`) exists for staff; shortcuts navigate only (`g d` dashboard, `g t` tickets); none triggers a destructive or sending action.

### 7.3 Screen-reader semantics for tables and live regions

Tables: `<caption>` describing content and count; `<th scope="col">` and `scope="row"` where the first column is an identifier; sortable headers contain a `<button>` and `aria-sort` on the `<th>`; numeric columns right aligned with `<td>` text including units; status cells contain text, not icon only; empty cells say "No value" visually hidden or a dash with `aria-label`; row actions have a label naming the row ("Open ticket 4823"); wide tables sit in a `role="region"` with `tabindex="0"` and `aria-label` so keyboard users can scroll; no layout tables.

Live regions (single mounted `LiveRegion` component at the shell level, regions exist at page load because newly inserted regions are unreliable):

| Event | Region | Politeness | Content |
|---|---|---|---|
| Payment state change (portal) | status | polite | "Payment confirmed. 10,00 $ added." / "Payment failed. Nothing was charged." Failure uses `role="alert"` (assertive) because the user must act |
| Form error summary | alert on summary receiving focus | focus based, no extra live | list of errors |
| Dashboard critical alert | status | assertive only for Critical; polite for Warning batched every 30 s ("2 new warnings") | "Critical: meter offline cluster, Ndolo" |
| SSE state | status | polite | "Live updates paused. Data may be out of date." / "Live updates restored." (debounced to avoid flapping spam; at most one per 30 s) |
| AI status | status | polite | "AI is drafting a reply." then "Draft ready for review." or "AI could not draft a reply. Write by hand." |
| Ask the data | status | polite | "Query written. Running." / "7 rows returned." |
| Undo window | status | polite | announced once at start and at 10 s left and at expiry |
| Loading skeleton | `aria-busy="true"` on region | n/a | one visually hidden "Loading" text |

Metric tiles on the dashboard are not live regions (would be constant noise); each tile is a labelled group ("Revenue today: 18 420 dollars, updated 14:05"), and a "Read summary" button announces a single sentence on demand.

### 7.4 Target sizes

Portal: all interactive elements at least 48x48 CSS px including padding, 8 px minimum spacing; bottom tab items 48 px high by one quarter width; amount chips 48 px. Staff: at least 44x44 for primary controls and 32x32 minimum for dense table icon buttons (above the 24 px SC 2.5.8 floor) with 8 px spacing; inline text links in paragraphs are exempt by the SC but table row links get padded hit areas. Playwright asserts bounding boxes of all `button, a, input, select, [role=button]` outside inline text at least 24x24 across both apps and 44x44 for portal ones.

### 7.5 Motion, forms and errors

Motion: `prefers-reduced-motion` removes transitions longer than 100 ms and all skeleton shimmer (static placeholder instead). Status changes use no animation that conveys meaning. No parallax, no autoplay.

Forms and error identification: every input has a visible `<label>` (placeholder never replaces labels); hints linked by `aria-describedby`; required fields marked in text ("required") and `required`; errors appear inline beneath the field in text with an icon, linked by `aria-describedby` plus `aria-invalid="true"`, and also in an error summary (heading "2 problems to fix") that takes focus after a failed submit. Messages say what is wrong and how to fix it ("Enter an amount between 1,00 $ and 500,00 $"). Server-side failures that are not field specific (provider unavailable) are a summary-level `role="alert"`. Time-limited codes (one-time code) show the expiry in text and allow requesting a new code without losing the typed phone number. Confirmation steps for money and sending described above (SC 3.3.4).

Accessible data tables for results (ask-the-data): the result table is built from arbitrary columns, so headers come from the query's column aliases (the AI prompt requires readable aliases; the UI falls back to humanised names), column types drive alignment and formatting, and a table caption uses the question text. Over 100 rows, pagination with 50 or 100 per page; "Skip table" link before it.

### 7.6 Test plan

| Layer | Tooling | What | Gate |
|---|---|---|---|
| Static | `eslint-plugin-jsx-a11y` (strict), stylelint for focus and motion rules, TypeScript requiring `label` on `IconButton` | every PR | blocks merge |
| Component | Vitest + Testing Library (queries by role), `vitest-axe` for each design-system component in Storybook stories (`@storybook/nextjs`), in light, dark, FR and pseudo-locale | every PR | blocks merge |
| Page-level automated | `@axe-core/playwright` on every route in the public, portal and staff groups, states loading, empty, error, populated, in FR and EN, light and dark, at 320 and 1280 px | every PR (fast subset), nightly full | zero serious or critical violations; baseline file for known minor ones with expiry dates |
| Keyboard flows | Playwright scripts that use only the keyboard: log in (OTP), top up end to end, create ticket, reply, triage and approve a draft, ask the data, acknowledge an alert, resolve a reconciliation exception, create an API key; assertions on focus location after each step, no trap, visible focus, focus not obscured by sticky bars | every PR for the main five flows; nightly rest | blocks merge |
| Layout stress | Playwright with 200 percent zoom, 320 px, text-spacing CSS, forced-colors emulation, reduced motion, pseudo-locale | nightly | blocks release |
| Screen-reader smoke | Manual script, 20 minutes per release candidate: NVDA + Firefox (staff flows), VoiceOver + Safari (macOS and iOS), TalkBack + Chrome on a real low-end Android (portal flows incl. top-up status announcement). Checklist includes: headings and landmarks list sensible, table navigation, live region announcements heard once, dialogs trap and return focus, AI label read, error summary read | each release candidate | blocks release |
| Expert and user testing | Third-party WCAG audit before first production release; usability sessions with five customers (including two with low vision or low literacy) and three agents using assistive tech | before launch, then yearly | findings triaged, AA failures block |
| Contrast | token contrast test (5.2) | every PR | blocks merge |
| Monitoring | A published accessibility statement and a feedback channel in the footer; defects tracked with label `a11y` and a 30-day fix target for AA failures | continuous | review monthly |

Automated tools catch roughly a third of issues; the plan counts on manual keyboard and screen-reader testing for the rest and says so in the release checklist.

---

## 8. Performance, offline, images and fonts

### 8.1 Budgets

Reference device: Android Go phone class (Snapdragon 4-series, 2 GB RAM, Chrome), network profile "Slow 3G / Fast 3G" (1.6 Mbps down, 750 kbps up, 150 ms RTT, Lighthouse "slow 4G" is too generous). **What server rendering buys and does not buy.** For public pages (home, help, tariffs, status, login shell) the server sends finished HTML, so text paints before any JavaScript runs; on a 3G phone the LCP is the HTML plus CSS, and the page is readable even if the script bundle is slow or never loads. For authenticated pages it buys little: the server has no token (D-44), so it sends the layout shell, the translated static text and skeletons, and the data appears only after the client bundle loads, recovers the token (5.9) and fetches. The portal's first load therefore includes the Next.js and React runtime (about 90 to 100 KB gzipped for the framework baseline, an assumption to be measured) plus portal code, which is why the portal budget is 130 KB (D-44) rather than the 90 KB assumed for a Vite build with Preact. Skeletons are server rendered so the user sees structure at once, but we do not claim fast time-to-data for authenticated screens; the measures that help are small bundles, one API call for the home screen and cached last-known values (8.4).

**Budgets and how they are measured.** Budgets are CI gates and are assumptions until the early spike (a vertical slice: public home, login shell, portal home and top-up page, staff dashboard shell, built with `next build` and tested on the reference device profile) confirms them **[open: spike result]**. Measurement uses three tools together: (1) the `next build` route table ("First Load JS" per route, which Next.js reports gzipped) parsed by a script that fails the build over budget; (2) `size-limit` on the build output for per-chunk and shared-chunk limits; (3) Lighthouse CI on the throttled profile below for LCP, INP proxy (Total Blocking Time), CLS and transfer size, on the public home, `/fr/app` and `/fr/app/top-up` (with a stubbed authenticated session) and `/fr/staff/dashboard`.

| Metric | Customer portal | Staff app |
|---|---|---|
| First Load JS (gzip, `next build` figure for the route, shared chunks included) | at most 130 KB (D-44) | at most 250 KB for shell and dashboard route |
| Public pages first load JS | at most 90 KB (server rendered, little hydration) | n/a |
| Total JS any route (gzip) | at most 170 KB | at most 400 KB (AI draft and KB editor routes lazy) |
| CSS (gzip) | at most 15 KB | at most 30 KB |
| LCP (p75, simulated 3G, reference device) | at most 2.5 s | at most 2.5 s on office network, 4 s on 3G |
| INP (p75) | at most 200 ms | at most 200 ms |
| CLS | at most 0.05 | at most 0.1 |
| Time to usable top-up form | at most 3.5 s on 3G | n/a |
| Fonts | at most 30 KB, one family | system font stack |
| API payload for home | at most 6 KB gzip | dashboard snapshot at most 40 KB |

Real-user monitoring (web-vitals library sending to the observability endpoint, sampled, no personal data) tracks LCP, INP, CLS per route, device class and connection type; regression alerts feed NFR-09.

### 8.2 Code splitting and dependency discipline

Code splitting is per route segment (App Router default); the `(public)`, `(portal)` and `(staff)` groups share only `src/shared` code that each uses. Heavy widgets (Markdown preview, SQL highlighting, charts, diff view, QR code for TOTP, WebAuthn helpers) are loaded with `next/dynamic` and `ssr: false` on their routes only. The TanStack Query provider is not in the root layout, so public pages ship no query code. The portal avoids a UI kit: it uses the in-house design system with only the needed components (tree-shakeable, no CSS-in-JS runtime; CSS modules plus tokens). `@next/bundle-analyzer` runs in CI on demand; a swap to Preact is not planned (C7 resolved by the new budget, pending the spike). Charts use a lightweight SVG library or hand-built sparklines with a table fallback. Dependency additions over 10 KB gzip need a note in the PR and a `size-limit` entry. `next.config` also sets `compress: true`, `poweredByHeader: false`, `images.unoptimized: true`, and `output: 'standalone'`. Client navigation prefetching is off for authenticated links (`prefetch={false}`) to save a 3G user's data; public links keep default prefetch. React performance practices: avoid unnecessary context re-renders, memoise table rows only where profiling shows need, virtualise only tables over 500 rows (staff tables are paginated at 50, so rarely), debounce filter inputs, use `useDeferredValue` for large result tables, `startTransition` for route navigations.

### 8.3 Caching headers

| Asset | Header |
|---|---|
| `/_next/static/*` (hashed chunks, CSS, fonts via `next/font`) | `public, max-age=31536000, immutable` (Next.js default), Brotli at the balancer |
| Public pages (`/fr`, `/en/help/...`, tariffs, status) | `Cache-Control: no-cache` (revalidate each time): a per-request CSP nonce (9.1) makes the HTML unique per request, so these pages are rendered dynamically on each request and the balancer does not cache the HTML. The data behind them is cached in the Next.js fetch data cache (`revalidate` 300 s for help and tariffs, 30 s for status), so rendering stays cheap. The spike measures server render time; if it hurts, an ADR may move to hash-based CSP for static public pages |
| Authenticated area pages (`/{locale}/app/*`, `/{locale}/staff/*`) | `Cache-Control: private, no-store` (the HTML is only a shell, but it must not be shared by an intermediary) |
| `/api/v1/*` | `Cache-Control: private, no-store` for personal and money data (set by the API); `public, max-age=60` is allowed only for the anonymous public KB and tariff endpoints |
| `/sw.js` (if a service worker is shipped) | `no-cache` |

### 8.4 Offline behaviour of the customer portal

The portal ships a web app manifest (FR/EN names via `manifest.ts`, 192 and 512 icons, `theme_color` from tokens) and a small hand-written service worker at `/sw.js` registered only on `/{locale}/app` and public pages, scope `/`. Next.js does not provide a service worker; no framework plugin is used, so the worker is about 60 lines and reviewed as security code.

What the service worker may cache: only same-origin `GET` requests for (a) `/_next/static/*` assets (cache-first, versioned by build id), (b) the public help article pages and the offline fallback page `/{locale}/offline` (stale-while-revalidate, public content only), (c) the manifest and icons. What it must never cache: any `/api/*` response, any HTML under `/{locale}/app/*` or `/{locale}/staff/*`, any response with `Set-Cookie` or `Authorization` involvement, any `POST`. It does not handle `fetch` for those at all (no `respondWith`), so there is no authenticated data at rest in Cache Storage and nothing to leak on a shared phone. A worker update shows "Update available. Reload" and never forces a reload mid-payment. Last-known balance and history for offline display come from the app-level IndexedDB persistence in 5.4 (limited fields, wiped on logout, disabled in shared-phone mode), not from the worker. Because authenticated HTML is not cached, opening `/fr/app` with no connection shows the cached offline page (with a link "Show my last known balance" that opens the client route from the already-cached JS chunks only if the page was loaded earlier in this browser session; otherwise a message that the app needs a connection once).

| Capability | Offline behaviour |
|---|---|
| Open the app | Shows the offline page (cached); if the portal was open in the session and JS is cached, last known values appear |
| See balance | Shows cached value with "Offline. Last updated 14:02" |
| See last 20 transactions and open tickets | Cached, read only |
| Read help pages and FAQ | Works for pages visited before and the pages precached at install |
| Top up | Not possible, explained; no payment queued (money correctness, invariant 3, and the mobile-money prompt requires the live provider) |
| Reply to a ticket or create a ticket | Draft saved locally, "Send when online" button (user triggers, so the user sees the result); Background Sync is not used silently to send customer messages |
| Language and theme | Works |
| Login | Not possible without a connection; existing session stays valid until refresh token expiry |
| Payment status page | Shows last known status and "Cannot check now. Do not pay again." |

A connectivity indicator uses `navigator.onLine` plus a failed-request heuristic (online flag alone lies on captive networks). Cached personal data is cleared on logout and encrypted by nothing beyond browser storage, so the cache holds only the fields listed above, and it is excluded on shared-device mode ("This is a shared phone" toggle at login disables persistence).

### 8.5 Images and fonts policy

Images: no decorative raster images in the portal; icons are an inline SVG sprite (about 4 KB) of fewer than 30 icons; illustrations, if any, are inline SVG under 3 KB. Ticket attachments follow D-30: at most 3 files of 5 MB each (jpg, png, pdf), checked client-side before upload and again by the server scan. Photos are compressed client-side (canvas, max 1280 px, JPEG quality 0.7, usually under 300 KB) to save the customer's data, PDFs are not altered; uploads show progress and are retried by the user. Attachments are served from a cookie-less domain, and staff views link to them rather than inline rendering of pdf. Staff avatars do not exist (initials only). `loading="lazy"` and explicit `width`/`height` for any image.

The Next.js image optimiser is disabled (`images.unoptimized: true`, no `remotePatterns`), so there is no server-side request forgery surface and no remote images anywhere; all images are same-origin static files or inline SVG, and `next/image` is allowed only with `unoptimized` for static local assets (a lint rule bans remote `src`).

Fonts: system font stack for staff; the portal uses one variable font subset through `next/font/local` (self-hosted, Latin plus Latin-1 supplement for French accents, WOFF2, at most 30 KB, `display: 'swap'`, preloaded, automatic fallback metric adjustment) or the system stack if the measured LCP gain is below 100 ms. No `next/font/google` (it would fetch from a third party at build or run time and is banned). No icon fonts.

---

## 9. Frontend security and test strategy

### 9.1 Security notes

| Topic | Decision |
|---|---|
| Token storage (D-17) | Access token (EdDSA JWT, 10 minutes, with a token-version claim checked for revocation) held in memory only. Refresh token is opaque and rotating with reuse detection and no grace window, in an `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth` cookie, never in a response body for browsers. Nothing in `localStorage` or `sessionStorage` grants access. On page load the app silently calls refresh to obtain a new access token. Staff idle 30 minutes, absolute 8 hours; customer idle 5 minutes, absolute 12 hours. A refresh-reuse response signs the user out on all tabs (`BroadcastChannel`) and shows "For your safety, please sign in again". The sign-in page offers a "Shared phone" mode for customers (D-11) that skips offline persistence. |
| Stream tickets (D-18) | The dashboard stream ticket is single use, 30 seconds, IP-bound and carried in the query string because `EventSource` cannot set headers. It is never logged client-side, the balancer strips it from access logs, and it is useless after first use. Other tokens are never placed in URLs. |
| XSS | React escapes by default; `dangerouslySetInnerHTML` is banned by lint except in one `SafeMarkdown` component that renders KB and AI text using a Markdown parser with raw HTML disabled plus DOMPurify as a second layer. AI output and customer messages are always treated as untrusted text. Links in AI or customer text get `rel="noopener noreferrer nofollow"` and external-link warning; `javascript:` URLs stripped. SQL display uses text nodes only. |
| CSP (D-44) | Set per request in `middleware.ts`, not statically: the middleware generates a random nonce (16 bytes, base64) for every request, sets the request header `x-nonce` and the response header `Content-Security-Policy`: `default-src 'self'; script-src 'self' 'nonce-<nonce>' 'strict-dynamic'; style-src 'self' 'nonce-<nonce>'; connect-src 'self'; img-src 'self' data:; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'; worker-src 'self'; manifest-src 'self'; report-uri /api/csp-report`. No `unsafe-inline` and no `unsafe-eval` in production (the development build may add `unsafe-eval` for React refresh only). Because the CSP depends on a per-request nonce, every page is rendered dynamically (see 8.3 for the consequence for caching). Root layout reads the nonce with `headers()` and passes it to `next/script` and to the next-intl provider; third-party scripts do not exist. `connect-src 'self'` covers API, SSE and `/api/csp-report`. Trusted Types `require-trusted-types-for 'script'` starts in a separate report-only header. Also `Referrer-Policy: same-origin`, `X-Content-Type-Options: nosniff`, `Permissions-Policy` minimal (camera, microphone, geolocation off; `publickey-credentials-get` and `-create` self). `/api/csp-report` rate limits and drops reports with personal data. |
| CSRF (D-46) | Only `POST /api/v1/auth/refresh` and `POST /api/v1/auth/logout` use the cookie; the client sends `X-Hub-CSRF: 1` on both, the browser sends `Origin`, and the cookie is `SameSite=Strict`. Every other API call authenticates with the bearer header, which a cross-site request cannot attach. Next.js Server Actions are disabled (`'use server'` is a lint error), so Next.js adds no cookie-authenticated mutation endpoint. Next.js route handlers in the app (`/api/csp-report` only) accept no credentials. No state-changing GET. |
| Clickjacking | `frame-ancestors 'none'`. |
| Secrets (D-44) | The Next.js server holds no secrets and the browser bundle must not either. No variable beginning `NEXT_PUBLIC_` may hold a secret, key or credential; the only allowed ones are non-secret: `NEXT_PUBLIC_API_BASE` (value `/api/v1`), `NEXT_PUBLIC_BUILD_ID`, `NEXT_PUBLIC_DEFAULT_LOCALE`. A CI script fails the build if another `NEXT_PUBLIC_` name appears or if the build output contains strings matching the secret scanner patterns (including `sgh_`). Environment-specific values are set at runtime by the same-site API (no per-environment rebuild needed since all calls are relative to `/api/v1`). Integrator API keys are shown once and never stored client-side. |
| Server runtime | `hub-web` runs as a non-root Node user with a read-only filesystem, no database credentials and no outbound network except the health probe; `X-Forwarded-*` headers are trusted only from the balancer. |
| Dependencies | `npm audit` and an SBOM in CI, Renovate or Dependabot (NFR-14), lockfile enforced, `--ignore-scripts` in CI, Next.js security releases patched within seven days. Subresource integrity is unnecessary since all assets are same origin. |
| Privacy in the browser | Customer phone numbers masked by default in staff views (`+243 ** *** 678`) with a "Reveal" button that is audited (server call), AI personal-data flags shown. Errors and telemetry strip personal data. `autocomplete="off"` on sensitive staff search fields, and no personal data in URLs (ids only, never phone or name). |
| Staff authentication (D-16) | Company identity provider (OIDC) preferred, with MFA enforced there; local break-glass accounts use TOTP; WebAuthn required for finance officers and administrators before go-live; backup codes shown once; step-up (re-authentication within 5 minutes) before approvals, role grants, key creation, export and erasure. Flows in 3.12 and 3.13. |
| Export and erasure UI (FR-20) | Customer account page requests export and erasure; the portal explains the retention that applies to ledger records (kept by law, personal fields anonymised), not a false promise of total deletion. |

### 9.2 Frontend test strategy

| Level | Tools | Scope | Notes |
|---|---|---|---|
| Unit | Vitest | `money.ts`, `dates.ts`, form parsers (comma and dot decimals, minor-unit conversion), SSE reducer (ordering, gaps, duplicates), role-guard table, query key factories | `money` property tests with fast-check: round trip, no float drift, FR and EN output snapshots including U+202F separators |
| Component | Vitest (jsdom) + Testing Library + `user-event`; Jest is not used | design-system components, state variants (loading, empty, error, partial, offline), AiBadge presence on every AI component; client components are tested inside a `NextIntlClientProvider` wrapper with real FR and EN messages and a `QueryClientProvider` with retries off | Queries by role and name, so tests double as accessibility checks. Async server components are not unit-tested (Vitest cannot render them yet); they are covered by Playwright against `next start` |
| Config tests | Vitest | `next.config` keeps Server Actions off and images unoptimised; `middleware` sets a fresh nonce per request and the expected CSP directives; a script fails on forbidden `NEXT_PUBLIC_` names and on `'use server'` | Direct tests of D-44 and D-46 |
| i18n completeness | `npm run i18n:check` in CI | key parity, ICU argument parity, unused and missing keys, French punctuation, `[EN]` marker absent on `main` | blocks merge |
| Contract | MSW handlers generated from `openapi.yaml`, plus the type-drift check; optionally Schemathesis on the backend side | Mocks cannot return shapes the spec forbids | A failing generated-client diff blocks CI |
| Integration (page) | Vitest browser mode or Playwright component testing | Top-up status polling and states, draft review, ask-the-data refusal | Fake timers for the undo window and SSE backoff |
| End to end | Playwright against the docker compose stack with provider stub and meter simulator | Critical journeys: top-up to credited (provider stub webhook), double-tap does not double charge, needs-review path, ticket to AI draft to approve to undo, role limits in ask-the-data, reconciliation resolve, API key create-and-revoke, language switch FR, offline portal behaviour (Playwright `context.setOffline`) | Traces on failure; runs on every merge to main, a smoke subset on PRs |
| Negative AI tests | Playwright + MSW | Assert that no network call to a send endpoint occurs without the confirm dialog (spy on requests); refusal and error states never render as a draft; cost fields absent from the DOM for non-admins (DOM text search) | Direct tests of invariant 2 and the cost rule |
| Visual regression | Storybook + Playwright screenshots (or Chromatic) in light, dark, FR, pseudo-locale, 320 px | Catch clipping and token regressions | Review required for diffs |
| Accessibility | Section 7.6 | | |
| Performance | Lighthouse CI on the portal home and top-up, bundle size checks, a nightly run on a real device farm or emulated Moto G4 profile | Budgets in 8.1 | Fail on more than 10 percent regression |
| Security | ESLint security rules, CSP report-only validation in staging, `npm audit`, a ZAP baseline scan on staging | | Linked to the threat model |

Coverage target: 85 percent lines on `shared/lib`, `features/*/hooks`, and 100 percent of role guard routes; no overall coverage chase, instead a critical-journey matrix reviewed in the test strategy (document 10). Test ids follow `TC-<FR>-n`, for example `TC-FR-02-4` is the double-tap top-up e2e.

---

## 10. Resolutions in revision 1

Earlier challenges (C1 to C9 of the first version) and how revision 1 of the shared context (section 7) resolved them, plus the review items this revision absorbed.

| # | Earlier challenge | Outcome |
|---|---|---|
| C1 | Where the refresh token lives | Accepted as D-17: HttpOnly, Secure, SameSite=Strict cookie at `Path=/api/v1/auth`; access token in memory, 10-minute EdDSA JWT; staff idle 30 min and absolute 8 h; customer idle 5 min and absolute 12 h. Sections 5.8, 7.1 (SC 2.2.1) and 9.1 updated. |
| C2 | 30-second recall window and urgent tickets | Accepted with a change as D-35: a fixed 30-second recall via outbox `available_at` and `POST /drafts/{id}/recall`. The per-ticket zero-delay option is rejected (no urgent bypass; the human approval step is the control). Section 3.4 updated, `send_after` removed. |
| C3 | Telling customers about AI assistance | Accepted as D-35: a disclosure sentence in the portal terms and new-ticket page; wording **[open: legal]**. The string is a translation key so the final wording needs no code change. |
| C4 | Audit visibility for finance and operations | Accepted through the RBAC matrix in 07 section 4.6: finance sees money streams, operations system streams, the administrator all streams read-only. Section 3.9 and nav table updated. |
| C5 | Portal payment status without a stream | Accepted: the portal polls `GET /payments/{id}`; SSE is for the staff dashboard only (D-18). Section 5.4. |
| C6 | CDF scale and display | Accepted (C-06 of the consistency review): two decimals stored per ISO 4217, whole francs displayed above 100 FC on summary views, never hide non-zero minor units on money-moving screens. Section 6.3 and 3.1. |
| C7 | 90 KB portal budget versus React | Resolved by the stack change (D-44): the portal first-load budget is 130 KB gzipped and the staff app 250 KB, with no Preact. Pending the early spike **[open]**. |
| C8 | Who sees AI cost | Resolved by D-14 and D-24: administrator only, on `/admin/ai-spend`, and since the administrator cannot reach tickets, drafts or ask-the-data, no per-item cost display exists anywhere else. Section 4 updated. |
| C9 | MFA recovery | Accepted as D-16: one-time backup codes; an administrator reset needs a second administrator; WebAuthn for finance officers and administrators. Screens in 3.12 and 3.13. |

Other changes from revision 1: roles and navigation follow D-13 and D-14 (administrator is a platform administrator with no tickets, money, customer content or ask-the-data; KB editor is a permission; KB publication needs a second approver, D-36); maker-checker approvals screen, step-up prompt, privacy request flows, incident linking and accounting-period lock confirmation added (3.11 to 3.16, D-15, D-16, D-29); triage is auto-applied as a marked suggestion with recorded override (D-32); money-affecting drafts show finance co-approval (D-35); canonical top-up statuses mapped to labels and customer balance defined as kWh plus wallet remainder (D-01, D-10); attachment and message limits follow D-30 (3 files, 5 MB, jpg, png, pdf; 5,000 characters).

**Stack change (D-42 to D-46, ADR-0021).** The frontend moved from React with Vite as a single-page app to Next.js (App Router) as the `hub-web` deployable on the same site as the Django API. Consequences made in this document: one application with `(public)`, `(portal)` and `(staff)` route groups under `/fr` and `/en` (2.1, replacing the two host names and two build entries); public pages server rendered and authenticated pages client components with the token in memory and recovery by the refresh cookie (2.1, 5.9); `next-intl` replaces i18next (6); a per-request nonce CSP forces dynamic rendering (9.1, 8.3); Server Actions disabled and `X-Hub-CSRF` on the two cookie endpoints (9.1); a hand-written service worker that never caches authenticated responses (8.4); revised budgets and the spike (8.1). Screens, accessibility plan and AI rules are unchanged except that the ask-the-data wireframe no longer shows latency (cost and latency exist only on `/admin/ai-spend`).

Items still open: the spike that confirms the 130 KB and 250 KB budgets and the server render cost of dynamic public pages; the exact legal wording of the AI disclosure (D-35), whether revenue recognition at token issuance changes any finance label (D-10, finance lead), and the approver for accounting-period lock and reopen (this document assumes it follows `api/permissions.yaml` once that file exists).
