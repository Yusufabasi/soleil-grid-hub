"use client";

import { useRouter } from "next/navigation";
import type { ReactNode } from "react";
import { useEffect } from "react";

import { Callout, Loading } from "@/components/ui";
import { useSession } from "@/lib/session";

/**
 * The client-side door to a persona's area.
 *
 * A convenience, not a control: everything behind it is enforced by the API, and this component would
 * only ever change what is drawn (the fuller plan's rule is the same — "hiding is a convenience, the
 * API enforces"). It exists so a signed-out visitor lands on the sign-in screen instead of a page of
 * 401s.
 *
 * The sign-out on a hard refresh is not a bug: the access token lives in memory only
 * (demo-shell-plan.md §4), so a reload genuinely has no session and this sends you to the door.
 */
export function SessionGate({
  persona,
  children,
}: {
  persona: "customer" | "staff";
  children: ReactNode;
}) {
  const session = useSession();
  const router = useRouter();
  const door = persona === "staff" ? "/staff/login" : "/customer/login";

  useEffect(() => {
    if (session.status !== "signed-in") {
      router.replace(door);
    }
  }, [session.status, router, door]);

  if (session.status !== "signed-in") {
    return <Loading what="Checking your session" />;
  }

  if (session.principal.type !== persona) {
    return (
      <Callout tone="warn" title="This is the other door">
        You are signed in as a {session.principal.type}. The customer portal and the staff console
        hold separate sessions; sign in again through the other one.
      </Callout>
    );
  }

  return <>{children}</>;
}
