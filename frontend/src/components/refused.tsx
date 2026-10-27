"use client";

import type { ReactNode } from "react";

import { Callout } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { refusalReason, rolesFor, type DemoOperation } from "@/lib/permissions";
import { usePrincipal } from "@/lib/session";

/**
 * "The API said no, and here is why."
 *
 * This is a deliberate part of the demo: the role matrix is a real, tested rule of this backend (D-14
 * and `api/permissions.yaml`), so a section refuses with the same words the contract uses instead of
 * being hidden. A reviewer can then see that the server, not the UI, is the authority — and that the
 * demo knows which role would grant the operation.
 */
export function Refused({ operation, error }: { operation: DemoOperation; error: unknown }) {
  const principal = usePrincipal();
  return (
    <Callout tone="bad" title="The API refused this for your role">
      <p>
        <span className="font-mono text-xs">{errorMessage(error)}</span>
      </p>
      <p className="mt-2">
        You are signed in as{" "}
        <strong>{principal?.display_name || principal?.type || "an unknown principal"}</strong>
        {principal?.roles.length ? ` (${principal.roles.join(", ")})` : ""}. {refusalReason(operation)}{" "}
        The seeded accounts on the sign-in screen cover each role, one per seat.
      </p>
    </Callout>
  );
}

/** A quieter version: a section this role does not have, with the reason inline. */
export function NotYourSeat({
  operation,
  children,
}: {
  operation: DemoOperation;
  children?: ReactNode;
}) {
  return (
    <Callout tone="neutral" title={`Not this role's seat: ${rolesFor(operation).join(", ")}`}>
      {children ?? `The contract grants this operation to ${rolesFor(operation).join(", ")}.`}
    </Callout>
  );
}
