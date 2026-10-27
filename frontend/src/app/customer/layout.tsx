"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { SignOutButton } from "@/components/sign-out-button";
import { useSession } from "@/lib/session";

/**
 * The customer shell: a wordmark, who you are, and the way out.
 *
 * There is no navigation beyond the balance and a top-up (§5 of the plan), so the header carries the
 * customer's own name and nothing else. Like the staff shell, the sign-in screen lives inside this
 * route group and renders without it.
 */
export default function CustomerLayout({ children }: { children: ReactNode }) {
  const session = useSession();

  if (session.status !== "signed-in" || session.principal.type !== "customer") {
    return <main className="mx-auto max-w-5xl px-6 py-10">{children}</main>;
  }

  return (
    <div className="min-h-dvh">
      <header className="border-b border-line bg-surface">
        <div className="mx-auto flex max-w-5xl flex-wrap items-center gap-x-4 gap-y-2 px-6 py-3">
          <Link href="/customer" className="text-sm font-semibold tracking-[0.14em] uppercase">
            Soleil&nbsp;Grid
          </Link>
          <span className="text-sm text-muted">Customer portal</span>
          <div className="ml-auto flex items-center gap-3 text-sm">
            <span className="font-mono text-xs text-muted">
              {session.principal.id.slice(-8)}
            </span>
            <SignOutButton door="/customer/login" />
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-6 py-8">{children}</main>
    </div>
  );
}
