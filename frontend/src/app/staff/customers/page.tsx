"use client";

import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { Refused } from "@/components/refused";
import { SessionGate } from "@/components/session-gate";
import {
  Button,
  Callout,
  Card,
  DataTable,
  Field,
  Loading,
  PageHeader,
  TextInput,
} from "@/components/ui";
import { apiList, errorMessage, isForbidden } from "@/lib/api";
import { formatDate } from "@/lib/format";
import { useResource } from "@/lib/use-resource";
import type { Customer, CustomerPseudonymous, Plan, Site } from "@/lib/types";

/**
 * The customer directory, as `listCustomers` returns it.
 *
 * Two role-dependent facts are visible here and are worth narrating:
 *
 * * `operations_engineer` gets `CustomerPseudonymous` rows — a pseudonym instead of a name and no
 *   phone number at all — because that is what the contract's `x-pseudonymised-for` says;
 * * `administrator` cannot read this operation at all (D-14), so the page says so rather than
 *   showing an empty table.
 */
export default function StaffCustomersPage() {
  return (
    <SessionGate persona="staff">
      <Directory />
    </SessionGate>
  );
}

function Directory() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [term, setTerm] = useState("");

  const load = useCallback(
    () => apiList<Customer | CustomerPseudonymous>("/customers", { query: { q: term, limit: 25 } }),
    [term],
  );
  const loadSites = useCallback(() => apiList<Site>("/sites", { query: { limit: 25 } }), []);
  const loadPlans = useCallback(() => apiList<Plan>("/plans", { query: { limit: 25 } }), []);

  const customers = useResource(load);
  const sites = useResource(loadSites);
  const plans = useResource(loadPlans);

  const siteName = (siteId: string) =>
    sites.data?.data.find((site) => site.id === siteId)?.name ?? siteId.slice(-8);
  const planName = (planId: string) =>
    plans.data?.data.find((plan) => plan.id === planId)?.name ?? planId.slice(-8);

  if (customers.loading && customers.data === null) {
    return <Loading what="Loading customers" />;
  }

  const rows = customers.data?.data ?? [];

  return (
    <>
      <PageHeader
        eyebrow="Staff console"
        title="Customers"
        description={`${rows.length} on this page · phone numbers are masked for support and withheld outright from operations, whose records are pseudonymous.`}
      />

      <Card
        title="Directory"
        actions={
          <Button variant="secondary" loading={customers.loading} onClick={customers.reload}>
            Refresh
          </Button>
        }
      >
        <form
          className="mb-4 flex flex-wrap items-end gap-3"
          onSubmit={(event) => {
            event.preventDefault();
            setTerm(query.trim());
          }}
        >
          <div className="w-full max-w-sm">
            <Field label="Search" hint="Name, number or phone (the API's `q`).">
              {(ids) => (
                <TextInput
                  {...ids}
                  value={query}
                  placeholder="Awa"
                  onChange={(event) => setQuery(event.target.value)}
                />
              )}
            </Field>
          </div>
          <Button variant="secondary" type="submit">
            Search
          </Button>
          {term ? (
            <Button
              variant="ghost"
              type="button"
              onClick={() => {
                setQuery("");
                setTerm("");
              }}
            >
              Clear
            </Button>
          ) : null}
        </form>

        {customers.error ? (
          isForbidden(customers.error) ? (
            <Refused operation="listCustomers" error={customers.error} />
          ) : (
            <Callout tone="bad" title="Could not read customers">
              {errorMessage(customers.error)}
            </Callout>
          )
        ) : (
          <DataTable
            caption="Customers"
            rows={rows}
            rowKey={(customer) => customer.id}
            empty="No customer matches that search."
            onRowClick={(customer) => router.push(`/staff/customers/${customer.id}`)}
            columns={[
              {
                header: "Customer",
                cell: (customer) =>
                  "full_name" in customer ? (
                    <span>
                      <span className="font-medium">{customer.full_name}</span>
                      <span className="block font-mono text-xs text-muted">
                        {customer.customer_number}
                      </span>
                    </span>
                  ) : (
                    <span className="font-mono text-xs">{customer.pseudonym}</span>
                  ),
              },
              {
                header: "Phone",
                cell: (customer) => (
                  <span className="font-mono text-xs">
                    {"phone_e164" in customer ? customer.phone_e164 : "withheld"}
                  </span>
                ),
              },
              { header: "Status", cell: (customer) => <span className="text-xs">{customer.status}</span> },
              { header: "Site", cell: (customer) => siteName(customer.site_id) },
              { header: "Plan", cell: (customer) => planName(customer.plan_id) },
              {
                header: "Registered",
                align: "right",
                cell: (customer) => (
                  <span className="text-xs text-muted">{formatDate(customer.created_at)}</span>
                ),
              },
            ]}
          />
        )}
      </Card>

      <p className="mt-3 text-xs text-muted">
        Selecting a row opens the profile. This demo has no create or edit form: those operations are
        not routed in the backend yet, so the screen does not offer them.
      </p>
    </>
  );
}
