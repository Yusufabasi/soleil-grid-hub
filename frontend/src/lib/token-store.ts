/**
 * Where the access token lives: in this module, in memory, for the life of the page.
 *
 * Not `localStorage`, not `sessionStorage`, not a readable cookie (demo-shell-plan.md §4). The
 * token is a bearer credential good for ten minutes; a hard refresh losing it is the price of not
 * handing a cross-site script something it can read. The same object is what the API wrapper reads
 * and what React subscribes to, so nothing is copied.
 *
 * The refresh token is not here at all: it is the `httpOnly` cookie the backend set on
 * `staffMfaVerify`/`verifyCustomerOtp`, and the browser carries it without this code seeing it.
 */

import type { Principal } from "./types";

export type SessionState =
  | { status: "signed-out"; accessToken: null; principal: null }
  | { status: "signed-in"; accessToken: string; principal: Principal };

const SIGNED_OUT: SessionState = { status: "signed-out", accessToken: null, principal: null };

let state: SessionState = SIGNED_OUT;
const listeners = new Set<() => void>();

export function getSession(): SessionState {
  return state;
}

export function getAccessToken(): string | null {
  return state.accessToken;
}

export function setSession(accessToken: string, principal: Principal): void {
  state = { status: "signed-in", accessToken, principal };
  emit();
}

export function clearSession(): void {
  state = SIGNED_OUT;
  emit();
}

/** `useSyncExternalStore`'s two arguments: read the snapshot, and hear about changes. */
export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function emit(): void {
  for (const listener of listeners) {
    listener();
  }
}
