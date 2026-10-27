"use client";

/**
 * React glue over the token store — deliberately thin, and the only client-side provider the shell
 * has. The token itself belongs to `token-store.ts` so that `api.ts` never has to import React.
 *
 * A hard refresh signs you out, by construction and on purpose (demo-shell-plan.md §4): the token
 * exists in memory and nowhere else. Every screen that needs a session renders its own gate rather
 * than relying on a cookie the browser would have kept around.
 */

import { createContext, useCallback, useContext, useMemo, useSyncExternalStore } from "react";
import type { ReactNode } from "react";

import {
  clearSession,
  getSession,
  setSession,
  subscribe,
  type SessionState,
} from "./token-store";
import type { Principal, TokenPair } from "./types";

type SessionContextValue = {
  session: SessionState;
  /** Apply the `TokenPair` any of the sign-in endpoints answers with. */
  signIn: (pair: TokenPair) => void;
  signOut: () => void;
};

const SessionContext = createContext<SessionContextValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const session = useSyncExternalStore(
    subscribe,
    getSession,
    // No server snapshot: the token is per-browser memory, and rendering a signed-in shell on the
    // server would be a lie. Every consumer of this context is a client component.
    () => getSession(),
  );

  const signIn = useCallback((pair: TokenPair) => {
    setSession(pair.access_token, pair.principal);
  }, []);
  const signOut = useCallback(() => clearSession(), []);

  const value = useMemo<SessionContextValue>(
    () => ({ session, signIn, signOut }),
    [session, signIn, signOut],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

function useSessionContext(): SessionContextValue {
  const value = useContext(SessionContext);
  if (value === null) {
    throw new Error("useSession must be used inside <SessionProvider>");
  }
  return value;
}

export function useSession(): SessionState {
  return useSessionContext().session;
}

/** The principal, or null when nobody is signed in. */
export function usePrincipal(): Principal | null {
  return useSessionContext().session.principal;
}

export function useSignIn(): (pair: TokenPair) => void {
  return useSessionContext().signIn;
}

export function useSignOut(): () => void {
  return useSessionContext().signOut;
}

// Re-exported so a caller has one place to import from.
export { getSession, clearSession, setSession, subscribe };
