# ADR-0021: Next.js frontend as its own deployable (`hub-web`)

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, UX architect; the product owner stated Next.js as their preferred frontend

## Context

Revision 0 chose a React and Vite single-page app on a CDN. The owner prefers Next.js. The customer portal
runs on low-end phones on 3G, which values HTML-first rendering, while the staff app is highly interactive
and authenticated.

## Decision

Next.js App Router with strict TypeScript as a Node standalone deployable behind the same balancer and the
same site as the API (pool `be_web` for `/` and `/_next/*`, everything under `/api/` to the Django pools), so
the refresh cookie scoped to `/api/v1/auth` works unchanged. Authenticated screens are client components that
hold the access token in memory; server components render public pages (login shell, public help from the
knowledge base, tariffs, status). The Next.js server holds no secrets and never reads the database. The image
optimiser is disabled. The API client is generated from `api/openapi.yaml`. next-intl provides `/fr` and `/en`.
A strict CSP with nonces is set in middleware. Budgets: portal first load 130 KB gzipped, staff app 250 KB,
confirmed by a spike.

## Alternatives Considered

### React with Vite as a static single-page app
- **Pros**: static hosting on a CDN, smaller baseline JavaScript, no extra runtime.
- **Cons**: no server rendering for public pages; routing, code splitting and i18n conventions assembled by hand.
- **Why not**: the cost of a Node runtime is modest and the owner prefers Next.js; the larger baseline is accepted and measured.

### Backend-for-frontend with server-side tokens
- **Pros**: tokens never reach the browser; authenticated pages can render on the server.
- **Cons**: a second session layer and a Next.js server that must be trusted with credentials.
- **Why not**: deferred; revisit if the portal's measured performance on low-end phones needs it.

## Consequences

### Positive
- Server-rendered public pages, conventional routing and code splitting, a typed generated client.

### Negative
- A fourth deployable to build, scan, scale and monitor; a larger baseline bundle than a Vite build.

### Risks
- Authenticated pages are client-rendered, so the server-rendering benefit applies to public pages only; the spike must confirm the portal meets its budget and interaction latency on a low-end device profile.
