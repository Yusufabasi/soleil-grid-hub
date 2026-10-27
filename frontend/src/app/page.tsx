import Link from "next/link";

/**
 * The front door. A server component and nothing but copy and links: there is no data to fetch until
 * somebody is signed in, and no session to read (the token lives in the browser's memory).
 */
export default function LandingPage() {
  return (
    <main className="mx-auto flex min-h-dvh max-w-5xl flex-col px-6 py-10 sm:py-16">
      <header className="flex items-center justify-between">
        <p className="text-sm font-semibold tracking-[0.18em] uppercase">Soleil&nbsp;Grid&nbsp;Hub</p>
        <p className="rounded-full border border-line bg-surface px-3 py-1 text-xs text-muted">
          demo build
        </p>
      </header>

      <section className="mt-16 max-w-3xl sm:mt-24">
        <p className="text-xs font-medium tracking-[0.18em] text-muted uppercase">
          Prepaid solar energy
        </p>
        <h1 className="mt-3 text-4xl leading-tight font-semibold text-balance sm:text-5xl">
          A customer&apos;s mobile-money payment, and where it lands in the ledger.
        </h1>
        <p className="mt-6 max-w-2xl text-lg text-muted">
          This shell sits in front of a real backend: customer and staff identity, the site, plan and
          meter registry, a double-entry ledger, and mobile-money collection through a provider double
          that signs its own webhooks. Nothing on these screens is mocked — every number is read from
          the API, and the two payment outcomes you can drive below go through it end to end.
        </p>
      </section>

      <section className="mt-12 grid gap-4 sm:grid-cols-2">
        <Link
          href="/customer/login"
          className="group rounded-lg border border-line bg-surface p-6 transition-colors hover:border-accent/40 hover:bg-accent-soft/50"
        >
          <p className="text-xs font-medium tracking-wide text-muted uppercase">Customer</p>
          <p className="mt-2 text-xl font-semibold">I&apos;m a customer</p>
          <p className="mt-2 text-sm text-muted">
            Wallet balance, your meter, and a top-up: watch it stay pending, confirm, or be refused
            without being credited.
          </p>
          <p className="mt-4 text-sm font-medium text-accent group-hover:underline">
            Sign in with a phone number →
          </p>
        </Link>
        <Link
          href="/staff/login"
          className="group rounded-lg border border-line bg-surface p-6 transition-colors hover:border-accent/40 hover:bg-accent-soft/50"
        >
          <p className="text-xs font-medium tracking-wide text-muted uppercase">Staff</p>
          <p className="mt-2 text-xl font-semibold">I&apos;m staff</p>
          <p className="mt-2 text-sm text-muted">
            The customer directory, the meter registry across sites, and the ledger — where the
            suspense account explains a payment that went wrong.
          </p>
          <p className="mt-4 text-sm font-medium text-accent group-hover:underline">
            Sign in with password and code →
          </p>
        </Link>
      </section>

      <section className="mt-16 grid gap-10 sm:grid-cols-3">
        <div>
          <h2 className="text-sm font-semibold tracking-wide uppercase">What to try</h2>
          <ul className="mt-3 space-y-2 text-sm text-muted">
            <li>
              Top up 51.00 USD and let the sandbox confirm the payment: the wallet balance moves and a
              double-entry posting appears in the ledger.
            </li>
            <li>
              Top up again and make the provider report a different amount: the customer stays
              unconfirmed and uncredited, and the posting lands on suspense instead.
            </li>
            <li>
              Sign in as each seeded role and watch the same screens grant different things — that is
              the authorization matrix, enforced by the API.
            </li>
          </ul>
        </div>
        <div>
          <h2 className="text-sm font-semibold tracking-wide uppercase">What is not here</h2>
          <p className="mt-3 text-sm text-muted">
            No translations, no generated API client, no dashboard stream, no meter telemetry, and no
            write forms for the registry: those operations are not routed in the backend yet, so the UI
            does not pretend otherwise. The full list, and the reasoning, is in{" "}
            <span className="font-mono text-xs">docs/plan/frontend/demo-shell-plan.md</span>.
          </p>
        </div>
        <div>
          <h2 className="text-sm font-semibold tracking-wide uppercase">Signing in</h2>
          <p className="mt-3 text-sm text-muted">
            Staff step two is a real TOTP code, computed from the test account&apos;s published secret.
            Customer sign-in solves a real proof-of-work challenge; the one-time code itself is never
            readable over HTTP by design, so the sandbox offers the same token route this project&apos;s
            own end-to-end test uses — one that cannot exist outside a local or test environment.
          </p>
        </div>
      </section>

      <footer className="mt-auto pt-16 text-xs text-muted">
        Soleil Grid Hub · local stack: balancer on{" "}
        <span className="font-mono">:8080</span>, API at{" "}
        <span className="font-mono">/api/v1</span>
      </footer>
    </main>
  );
}
