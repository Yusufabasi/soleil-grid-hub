"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

import { SignOutButton } from "@/components/sign-out-button";
import { can, type DemoOperation } from "@/lib/permissions";
import { usePrincipal, useSession } from "@/lib/session";

/** The three sections of the staff console (§5 of the plan) and the read each one needs. */
const SECTIONS: readonly { href: string; label: string; operation: DemoOperation }[] = [
  { href: "/staff/customers", label: "Customers", operation: "listCustomers" },
  { href: "/staff/meters", label: "Meters & sites", operation: "listMeters" },
  { href: "/staff/ledger", label: "Ledger", operation: "listLedgerAccounts" },
];

/**
 * The staff shell, and the one place the demo leans on the role matrix for navigation.
 *
 * A section the signed-in role cannot read is *shown and marked*, not hidden: `finance_officer`
 * cannot list meters and `operations_engineer` cannot read the ledger, and a reviewer who could not
 * see the section at all would have to guess whether that is a missing feature or a refused one. The
 * mark says which role has it; the page behind it does not rely on this — the API refuses it anyway.
 */
export default function StaffLayout({ children }: { children: ReactNode }) {
  const session = useSession();
  const principal = usePrincipal();
  const pathname = usePathname();

  // The sign-in screen is inside this route group and must render without a session or a nav.
  if (session.status !== "signed-in" || session.principal.type !== "staff") {
    return <main className="mx-auto max-w-6xl px-6 py-10">{children}</main>;
  }

  return (
    <div className="min-h-dvh">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-6 py-3">
          <Link href="/staff/customers" className="text-sm font-semibold tracking-[0.14em] uppercase">
            Soleil&nbsp;Grid&nbsp;Hub
          </Link>
          <nav aria-label="Staff sections" className="flex flex-wrap items-center gap-1">
            {SECTIONS.map((section) => {
              const allowed = can(principal, section.operation);
              const current = pathname.startsWith(section.href);
              return (
                <Link
                  key={section.href}
                  href={section.href}
                  aria-current={current ? "page" : undefined}
                  title={allowed ? undefined : `Readable by ${section.operation === "listLedgerAccounts" ? "finance_officer" : "operations_engineer, support_agent"}`}
                  className={`rounded-md px-3 py-1.5 text-sm ${
                    current ? "bg-accent-soft font-medium text-accent" : "text-muted hover:bg-paper"
                  } ${allowed ? "" : "opacity-60"}`}
                >
                  {section.label}
                  {allowed ? null : <span className="ml-1 text-xs">·</span>}
                </Link>
              );
            })}
          </nav>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="text-muted">
              {principal?.display_name || "staff"}
              {principal?.roles.length ? ` · ${principal.roles.join(", ")}` : ""}
            </span>
            <SignOutButton door="/staff/login" />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-6 py-8">{children}</main>
    </div>
  );
}
