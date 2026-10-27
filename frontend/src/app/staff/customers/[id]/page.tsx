"use client";

import { useParams } from "next/navigation";
import { useCallback } from "react";

import { NotYourSeat, Refused } from "@/components/refused";
import { SessionGate } from "@/components/session-gate";
import {
  Button,
  Callout,
  Card,
  DataTable,
  KeyValue,
  Loading,
  MeterStatus,
  PageHeader,
  Stat,
  TopUpStatus,
} from "@/components/ui";
import { apiGet, apiList, errorMessage, isForbidden } from "@/lib/api";
import { formatEnergyWh, formatMoney } from "@/lib/format";
import { can } from "@/lib/permissions";
import { usePrincipal } from "@/lib/session";
import { useResource } from "@/lib/use-resource";
import type { Customer, Meter, Plan, Site, TopUp, Wallet } from "@/lib/types";

/**
 * One customer, from whichever side of the matrix you came in through.
 *
 * This screen is the clearest place to see the role rules, because three different operations meet
 * here and each is granted to a different set: `getCustomer` (support, finance, operations),
 * `listCustomerMeters` (support, operations) and `getCustomerWallet` (finance) — while `listTopUps` is
 * finance only. A section this role cannot read says which role can, from the same table the API
 * enforces, instead of vanishing.
 *
 * The disabled button is deliberate, not unfinished: `createTopUp` is customer-or-integrator only
 * (D-14: no staff role may move customer money), so the console offers no way to do it and says why.
 */
export default function StaffCustomerDetailPage() {
  return (
    <SessionGate persona="staff">
      <Profile />
    </SessionGate>
  );
}

function Profile() {
  const params = useParams<{ id: string }>();
  const customerId = params.id;
  const principal = usePrincipal();

  const loadCustomer = useCallback(
    () => apiGet<Customer>(`/customers/${customerId}`),
    [customerId],
  );
  const loadWallet = useCallback(
    () => apiGet<Wallet>(`/customers/${customerId}/wallet`),
    [customerId],
  );
  const loadMeters = useCallback(
    () => apiList<Meter>(`/customers/${customerId}/meters`, {}),
    [customerId],
  );
  const loadTopUps = useCallback(
    () => apiList<TopUp>("/top-ups", { query: { customer_id: customerId, limit: 10 } }),
    [customerId],
  );

  const customer = useResource(loadCustomer);
  const wallet = useResource(loadWallet);
  const meters = useResource(loadMeters);
  const topUps = useResource(loadTopUps);

  const loadPlan = useCallback(
    () => apiGet<Plan>(`/plans/${customer.data?.plan_id ?? ""}`).catch(() => null),
    [customer.data?.plan_id],
  );
  const loadSite = useCallback(
    () => apiGet<Site>(`/sites/${customer.data?.site_id ?? ""}`).catch(() => null),
    [customer.data?.site_id],
  );
  const plan = useResource(loadPlan);
  const site = useResource(loadSite);

  const mayReadWallet = can(principal, "getCustomerWallet");
  const mayReadMeters = can(principal, "listCustomerMeters");
  const mayReadTopUps = can(principal, "listTopUps");

  if (customer.loading && customer.data === null) {
    return <Loading what="Loading the customer" />;
  }
  if (customer.error !== null) {
    return isForbidden(customer.error) ? (
      <Refused operation="getCustomer" error={customer.error} />
    ) : (
      <Callout tone="bad" title="Could not read this customer">
        {errorMessage(customer.error)}
      </Callout>
    );
  }
  const record = customer.data;
  if (!record) {
    return null;
  }

  return (
    <>
      <PageHeader
        eyebrow="Customer"
        title={record.full_name}
        description={`${record.customer_number} · ${record.phone_e164} · site ${site.data?.name ?? record.site_id.slice(-8)} · plan ${plan.data?.name ?? record.plan_id.slice(-8)}`}
        actions={
          <Button
            variant="secondary"
            disabled
            title="Staff cannot move customer money by design (D-14): createTopUp is granted to customer and integrator only."
          >
            Top up this customer
          </Button>
        }
      />

      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Profile">
          <KeyValue
            rows={[
              { label: "Status", value: record.status },
              { label: "Locale", value: record.locale },
              { label: "Email", value: record.email ?? "—" },
              { label: "Site", value: site.data?.name ?? record.site_id.slice(-8) },
              { label: "Plan", value: plan.data?.name ?? record.plan_id.slice(-8) },
              { label: "Version", value: String(record.version) },
              { label: "Registered", value: record.created_at.slice(0, 19).replace("T", " ") },
              { label: "Updated", value: record.updated_at.slice(0, 19).replace("T", " ") },
            ]}
          />
        </Card>

        <Card title="Wallet" description="Readable by finance_officer only — the customer's own money.">
          {mayReadWallet ? (
            wallet.error ? (
              isForbidden(wallet.error) ? (
                <Refused operation="getCustomerWallet" error={wallet.error} />
              ) : (
                <Callout tone="bad">{errorMessage(wallet.error)}</Callout>
              )
            ) : (
              <>
                <Stat
                  label="Balance"
                  value={formatMoney(wallet.data?.balance)}
                  hint={
                    wallet.data?.ledger_account_id
                      ? `ledger account ${wallet.data.ledger_account_id.slice(-8)}`
                      : "no wallet account yet"
                  }
                />
                <p className="mt-4 text-xs text-muted">
                  {wallet.data?.pending_top_ups ?? 0} top-up(s) awaiting the provider.
                </p>
              </>
            )
          ) : (
            <NotYourSeat operation="getCustomerWallet">
              A support agent sees the customer record but not the balance: the contract separates the
              two. Sign in as <span className="font-mono">finance@example.test</span> for this one.
            </NotYourSeat>
          )}
        </Card>

        <Card title="Plan and tariff">
          {plan.data ? (
            <KeyValue
              rows={[
                { label: "Plan", value: `${plan.data.name} (${plan.data.code})` },
                { label: "Currency", value: plan.data.currency },
                {
                  label: "Top-up range",
                  value: `${formatMoney(plan.data.min_top_up)} – ${formatMoney(plan.data.max_top_up)}`,
                },
                {
                  label: "Price per kWh",
                  value: formatMoney(plan.data.current_tariff?.price_per_kwh ?? null),
                },
                {
                  label: "Tariff effective",
                  value: plan.data.current_tariff?.effective_from.slice(0, 10) ?? "—",
                },
              ]}
            />
          ) : (
            <Loading what="Reading the plan" />
          )}
          {site.data ? (
            <p className="mt-4 text-xs text-muted">
              Site {site.data.code} · {site.data.timezone} · status {site.data.status} · health{" "}
              {site.data.health} · {site.data.meter_count} meters
            </p>
          ) : null}
        </Card>
      </div>

      <div className="mt-6 grid gap-6 lg:grid-cols-2">
        <Card title="Meters">
          {mayReadMeters ? (
            meters.error ? (
              isForbidden(meters.error) ? (
                <Refused operation="listCustomerMeters" error={meters.error} />
              ) : (
                <Callout tone="bad">{errorMessage(meters.error)}</Callout>
              )
            ) : (
              <DataTable
                caption="This customer's meters"
                rows={meters.data?.data ?? []}
                rowKey={(meter) => meter.id}
                empty="No meter registered."
                columns={[
                  {
                    header: "Serial",
                    cell: (meter) => <span className="font-mono">{meter.serial_number}</span>,
                  },
                  { header: "Status", cell: (meter) => <MeterStatus status={meter.status} /> },
                  {
                    header: "Credit",
                    align: "right",
                    cell: (meter) => formatEnergyWh(meter.credit_remaining_wh),
                  },
                  {
                    header: "Firmware",
                    align: "right",
                    cell: (meter) => (
                      <span className="font-mono text-xs">{meter.firmware_version ?? "—"}</span>
                    ),
                  },
                ]}
              />
            )
          ) : (
            <NotYourSeat operation="listCustomerMeters" />
          )}
        </Card>

        <Card title="Top-ups" description="Readable by finance_officer and the customer themselves.">
          {mayReadTopUps ? (
            topUps.error ? (
              isForbidden(topUps.error) ? (
                <Refused operation="listTopUps" error={topUps.error} />
              ) : (
                <Callout tone="bad">{errorMessage(topUps.error)}</Callout>
              )
            ) : (
              <DataTable
                caption="Recent top-ups for this customer"
                rows={topUps.data?.data ?? []}
                rowKey={(topUp) => topUp.id}
                empty="No top-up yet."
                columns={[
                  {
                    header: "Amount",
                    align: "right",
                    cell: (topUp) => (
                      <span className="font-mono tabular-nums">{formatMoney(topUp.amount)}</span>
                    ),
                  },
                  { header: "Status", cell: (topUp) => <TopUpStatus status={topUp.status} /> },
                  {
                    header: "Energy",
                    align: "right",
                    cell: (topUp) => <span className="font-mono">{formatEnergyWh(topUp.energy_wh)}</span>,
                  },
                  {
                    header: "Ledger",
                    cell: (topUp) => (
                      <span className="font-mono text-xs">
                        {topUp.ledger_transaction_id ? topUp.ledger_transaction_id.slice(-8) : "—"}
                      </span>
                    ),
                  },
                ]}
              />
            )
          ) : (
            <NotYourSeat operation="listTopUps">
              Support reads the directory; the payments book is finance&apos;s. This is the RBAC rule
              the demo wants to show, not a missing screen.
            </NotYourSeat>
          )}
        </Card>
      </div>
    </>
  );
}
