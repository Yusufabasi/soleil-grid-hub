"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui";
import { logout } from "@/lib/auth";
import { useSignOut } from "@/lib/session";

/**
 * Sign out: revoke what the cookie points at, then forget the token in memory.
 *
 * Both halves matter. `POST /auth/logout` is what the backend needs to see (it revokes the refresh
 * family); clearing the in-memory access token is what this browser needs, and it happens even if the
 * request fails, because a client-side "signed out" that still holds a live bearer token is worse than
 * an unreachable server.
 */
export function SignOutButton({ door }: { door: string }) {
  const signOut = useSignOut();
  const router = useRouter();
  const [busy, setBusy] = useState(false);

  return (
    <Button
      variant="ghost"
      loading={busy}
      onClick={async () => {
        setBusy(true);
        try {
          await logout();
        } catch {
          // The session is over as far as this browser is concerned either way.
        }
        signOut();
        router.replace(door);
      }}
    >
      Sign out
    </Button>
  );
}
