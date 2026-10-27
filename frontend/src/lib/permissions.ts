/**
 * The authorization matrix, hand-written for the operations this demo actually calls
 * (demo-shell-plan.md §4: the fuller plan generates this from `api/permissions.yaml`; the demo
 * mirrors the rows it needs and says so).
 *
 * Two things this is and is not:
 *
 * * it **is** the reason the staff console can say "your role cannot read the ledger — that is
 *   `finance_officer`" instead of hiding a section and letting a reviewer wonder whether the
 *   feature exists;
 * * it is **not** a security control. Every endpoint enforces the same matrix server-side, and a
 *   bug here changes what is drawn, not what is allowed. The screens still render the API's 403
 *   when it disagrees — which is the honest way round, and the demo shows it: Support Agent is
 *   refused the wallet by the backend, not by this file.
 */

import type { Principal } from "./types";

/** The routed operations this demo reads or writes (§2 of the plan). */
export type DemoOperation =
  | "getMe"
  | "listCustomers"
  | "getCustomer"
  | "listSites"
  | "getSite"
  | "listPlans"
  | "getPlan"
  | "listPlanTariffs"
  | "listMeters"
  | "getMeter"
  | "listCustomerMeters"
  | "getCustomerWallet"
  | "listTopUps"
  | "getTopUp"
  | "createTopUp"
  | "cancelTopUp"
  | "listLedgerAccounts"
  | "getLedgerAccount"
  | "listLedgerTransactions"
  | "getLedgerTransaction";

const OPERATION_ROLES: Record<DemoOperation, readonly string[]> = {
  getMe: ["customer", "support_agent", "finance_officer", "operations_engineer", "administrator"],
  listCustomers: ["support_agent", "finance_officer", "operations_engineer", "integrator"],
  getCustomer: [
    "customer",
    "support_agent",
    "finance_officer",
    "operations_engineer",
    "integrator",
  ],
  listSites: ["support_agent", "finance_officer", "operations_engineer", "integrator"],
  getSite: ["customer", "support_agent", "finance_officer", "operations_engineer", "integrator"],
  listPlans: ["support_agent", "finance_officer", "operations_engineer", "integrator"],
  getPlan: ["support_agent", "finance_officer", "operations_engineer", "integrator"],
  listPlanTariffs: ["support_agent", "finance_officer", "operations_engineer"],
  listMeters: ["customer", "support_agent", "operations_engineer", "integrator"],
  getMeter: ["customer", "support_agent", "operations_engineer", "integrator"],
  listCustomerMeters: ["customer", "support_agent", "operations_engineer", "integrator"],
  getCustomerWallet: ["customer", "finance_officer"],
  listTopUps: ["customer", "finance_officer", "integrator"],
  getTopUp: ["customer", "finance_officer", "integrator"],
  createTopUp: ["customer", "integrator"],
  cancelTopUp: ["customer"],
  listLedgerAccounts: ["finance_officer"],
  getLedgerAccount: ["finance_officer"],
  listLedgerTransactions: ["finance_officer"],
  getLedgerTransaction: ["finance_officer"],
};

/** The roles the contract lists for an operation — what a screen names when it refuses. */
export function rolesFor(operation: DemoOperation): readonly string[] {
  return OPERATION_ROLES[operation];
}

/**
 * Whether the signed-in principal should be shown this operation.
 *
 * Staff act with their role set; every other principal acts with its kind as a role
 * (`hub.shared.api.permissions.roles_of`), which is why the customer's empty `roles` list still
 * reaches the customer operations.
 */
export function can(principal: Principal | null, operation: DemoOperation): boolean {
  if (principal === null) {
    return false;
  }
  const held = principal.type === "staff" ? principal.roles : [principal.type];
  return held.some((role) => OPERATION_ROLES[operation].includes(role));
}

/** A sentence for a screen that was refused: which role would grant it. */
export function refusalReason(operation: DemoOperation): string {
  return `The contract grants this to ${rolesFor(operation).join(", ")}.`;
}
