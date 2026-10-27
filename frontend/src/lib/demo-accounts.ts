/**
 * The seeded identities this demo signs in with, copied from the two fixtures the stack already
 * loads — `db/seed/test_users.json` (staff and their published TOTP secrets) and
 * `db/seed/registry_demo.json` (sites, plans, customers and meters).
 *
 * Every value here is synthetic and already published in the repository: none of these accounts
 * exists outside a throwaway database, which is exactly why the login screens can offer them. The
 * customer OTP code is *not* here — it is deliberately unreachable over HTTP (demo-shell-plan.md §3)
 * and the sandbox mint route is how a customer session is opened instead.
 */

export type DemoStaffAccount = {
  email: string;
  name: string;
  role: string;
  /** What this account can show you, in one line: the demo narrates the RBAC matrix. */
  sees: string;
  totpSecret: string;
};

/** The password `db/seed/test_users.json` gives every seeded account. */
export const DEMO_STAFF_PASSWORD = "Soleil!Local-2026";

export const DEMO_STAFF: readonly DemoStaffAccount[] = [
  {
    email: "support@example.test",
    name: "Support Agent",
    role: "support_agent",
    sees: "customers, sites, plans and the meter registry",
    totpSecret: "JBSWY3DPEHPK3PXP",
  },
  {
    email: "finance@example.test",
    name: "Finance Officer",
    role: "finance_officer",
    sees: "the ledger, wallets and top-ups — but not the meter registry",
    totpSecret: "KRSXG5CTMVRXEZLU",
  },
  {
    email: "operations@example.test",
    name: "Operations Engineer",
    role: "operations_engineer",
    sees: "the meter registry and sites; customer records are pseudonymised for this role",
    totpSecret: "GEZDGNBVGY3TQOJQ",
  },
];

export type DemoCustomer = {
  id: string;
  name: string;
  phone: string;
  site: string;
  plan: string;
  /** Whether the account has a meter a top-up can be collected into. */
  hasActiveMeter: boolean;
};

export const DEMO_CUSTOMERS: readonly DemoCustomer[] = [
  {
    id: "0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0101",
    name: "Awa Diallo",
    phone: "+243810000101",
    site: "Lubumbashi",
    plan: "Basic",
    hasActiveMeter: true,
  },
  {
    id: "0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0102",
    name: "Blaise Mukendi",
    phone: "+243810000102",
    site: "Lubumbashi",
    plan: "Solar Plus",
    hasActiveMeter: true,
  },
  {
    id: "0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0105",
    name: "Esther Ndaya",
    phone: "+243810000105",
    site: "Lubumbashi",
    plan: "Basic",
    hasActiveMeter: true,
  },
  {
    id: "0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0103",
    name: "Chantal Ilunga",
    phone: "+243810000103",
    site: "Kinshasa",
    plan: "Basic",
    hasActiveMeter: true,
  },
  {
    id: "0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0104",
    name: "Dieudonné Kabongo (suspended)",
    phone: "+243810000104",
    site: "Kinshasa",
    plan: "Solar Plus",
    hasActiveMeter: false,
  },
  {
    id: "0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0106",
    name: "Fiston Banza",
    phone: "+243810000106",
    site: "Kinshasa",
    plan: "Basic",
    hasActiveMeter: false,
  },
];

/** The provider the local stack collects from (`HUB_PROVIDER_BASE_URLS` in compose.yaml). */
export const DEMO_PROVIDER = "mpesa_cd" as const;
