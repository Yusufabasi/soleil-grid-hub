import { describe, expect, it } from "vitest";

import { can, refusalReason, rolesFor } from "./permissions";
import type { Principal } from "./types";

function principal(type: Principal["type"], roles: string[]): Principal {
  return {
    id: "0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0032",
    type,
    display_name: "Test",
    roles,
    attributes: [],
    scopes: [],
    site_ids: [],
    locale: "en",
    mfa_enrolled: true,
    acr: "mfa",
    auth_time: "2026-10-28T12:00:00Z",
  };
}

const support = principal("staff", ["support_agent"]);
const finance = principal("staff", ["finance_officer"]);
const operations = principal("staff", ["operations_engineer"]);
const administrator = principal("staff", ["administrator"]);
const customer = principal("customer", []);

/**
 * The demo draws its sections from this table. What it is *for* is refusing a section with the right
 * sentence, so the cases worth pinning are the disagreements between roles — the ones a reviewer will
 * check by signing in as each seat in turn.
 */
describe("can", () => {
  it("nobody signed in sees nothing", () => {
    expect(can(null, "listCustomers")).toBe(false);
  });

  it("no staff role may move customer money", () => {
    for (const staff of [support, finance, operations, administrator]) {
      expect(can(staff, "createTopUp")).toBe(false);
    }
    expect(can(customer, "createTopUp")).toBe(true);
  });

  it("the ledger belongs to finance alone", () => {
    expect(can(finance, "listLedgerAccounts")).toBe(true);
    expect(can(finance, "listLedgerTransactions")).toBe(true);
    expect(can(support, "listLedgerAccounts")).toBe(false);
    expect(can(operations, "listLedgerAccounts")).toBe(false);
  });

  it("the meter registry excludes finance, and the wallet includes it", () => {
    expect(can(operations, "listMeters")).toBe(true);
    expect(can(support, "listMeters")).toBe(true);
    expect(can(finance, "listMeters")).toBe(false);
    expect(can(finance, "getCustomerWallet")).toBe(true);
    expect(can(support, "getCustomerWallet")).toBe(false);
  });

  it("the administrator has no customer access at all (D-14)", () => {
    expect(can(administrator, "listCustomers")).toBe(false);
    expect(can(administrator, "getCustomerWallet")).toBe(false);
  });

  it("a customer acts through their kind, not through roles", () => {
    expect(customer.roles).toEqual([]);
    expect(can(customer, "getCustomerWallet")).toBe(true);
    expect(can(customer, "listMeters")).toBe(true);
    expect(can(customer, "listLedgerAccounts")).toBe(false);
  });

  it("names the roles that would grant an operation", () => {
    expect(rolesFor("listLedgerAccounts")).toEqual(["finance_officer"]);
    expect(refusalReason("getCustomerWallet")).toContain("finance_officer");
  });
});
