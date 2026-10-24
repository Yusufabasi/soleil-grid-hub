# ADR-0006: Authentication and sessions

**Date**: 2026-10-24
**Status**: accepted
**Deciders**: tech lead, security engineer, UX architect

## Context

Staff handle money and personal data; customers log in from shared, low-end phones; the Terms of
Reference require MFA and managed access. Stateless tokens cannot be revoked within their lifetime.

## Decision

Staff sign in through the company identity provider over OIDC with enforced MFA; local accounts exist
for break-glass and use TOTP; WebAuthn is required for finance officers and administrators before
go-live. Step-up authentication within five minutes guards approvals, role grants, key creation,
export and erasure. Sessions: an EdDSA-signed access JWT of ten minutes carrying a token-version claim
checked for revocation, and an opaque rotating refresh token with reuse detection and no grace window,
delivered to browsers in an `HttpOnly; Secure; SameSite=Strict; Path=/api/v1/auth` cookie. Staff idle
30 minutes and absolute 8 hours; customers idle 5 minutes and absolute 12 hours, signing in by phone
one-time code with strict abuse controls. The OIDC authorization-code exchange runs in `hub-api` (confidential client, PKCE), never in `hub-web` (D-47).

## Alternatives Considered

### Server-side sessions only
- **Pros**: instant revocation.
- **Cons**: shared session store on the hot path; poorer fit for machine clients.
- **Why not**: short JWTs plus a version claim give near-instant revocation without a lookup per request.

### Refresh token in browser storage
- **Pros**: simplest client.
- **Cons**: the largest XSS-to-takeover risk.
- **Why not**: rejected by both the UX and security reviews.

## Consequences

### Positive
- A stolen access token lives at most ten minutes; refresh theft is detected by reuse.

### Negative
- The app and API must share a site for the cookie; the edge is configured accordingly.

### Risks
- Identity-provider outage blocks staff login; break-glass accounts and their audit are the mitigation.
