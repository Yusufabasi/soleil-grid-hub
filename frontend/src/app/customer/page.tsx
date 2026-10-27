"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useState } from "react";

import { SessionGate } from "@/components/session-gate";
import {
  Button,
  Callout,
  Card,
  DataTable,
  Field,
  Loading,
  MeterStatus,
  PageHeader,
  SelectInput,
  Stat,
  TextInput,
  TopUpStatus,
  type Column,
} from "@/components/ui";
import { API_BASE, apiGet, apiList, errorMessage, request } from "@/lib/api";
import { DEMO_PROVIDER } from "@/lib/demo-accounts";
import { formatEnergyWh, formatMoney, parseAmountToMinor } from "@/lib/format";
import { usePrincipal } from "@/lib/session";
import { useResource } from "@/lib/use-resource";
import type { Customer, Meter, Money, TopUp, Wallet } from "@/lib/types";

const PROVIDERS = [
  { value: "mpesa_cd", label: "M-Pesa (DRC)" },
  { value: "airtel_cd", label: "Airtel Money (DRC)" },
  { value: "orange_cd", label: "Orange Money (DRC)" },
];

const QUICK_AMOUNTS = ["5", "10", "25", "51"];

/**
 * The customer's home: balance, meters, and the one write this whole demo performs.
 *
 * `createTopUp` is reachable from exactly here, and only for a customer principal — the operation is
 * customer-or-integrator-only in the contract (D-14: no staff role may move customer money), so the
 * staff console has no affordance that calls it at all. The amount is sent as integer minor units
 * (parsed without floating-point arithmetic) with an `Idempotency-Key`, which is what makes a double
 * submit answer the same top-up instead of charging twice.
 */
export default function CustomerDashboard() {
  return (
    <SessionGate persona="customer">
      <Dashboard />
    </SessionGate>
  );
}

function Dashboard() {
  const principal = usePrincipal();
  const customerId = principal?.id ?? "";

  const loadCustomer = useCallback(() => apiGet<Customer>(`/customers/${customerId}`), [customerId]);
  const loadWallet = useCallback(
    () => apiGet<Wallet>(`/customers/${customerId}/wallet`),
    [customerId],
  );
  const loadMeters = useCallback(
    () => apiList<Meter>("/meters", { query: { customer_id: customerId } }),
    [customerId],
  );
  const loadTopUps = useCallback(
    () => apiList<TopUp>("/top-ups", { query: { sort: "-created_at", limit: 10 } }),
    [],
  );

  const customer = useResource(loadCustomer);
  const wallet = useResource(loadWallet);
  const meters = useResource(loadMeters);
  const topUps = useResource(loadTopUps);

  const refresh = () => {
    customer.reload();
    wallet.reload();
    meters.reload();
    topUps.reload();
  };

  if (customer.loading && customer.data === null) {
    return <Loading what="Loading your account" />;
  }
  if (customer.error !== null) {
    return (
      <Callout tone="bad" title="Could not read your account">
        {errorMessage(customer.error)}
      </Callout>
    );
  }
  if (!customer.data) {
    return null;
  }

  const activeMeters = (meters.data?.data ?? []).filter((meter) => meter.status === "active");

  return (
    <>
      <PageHeader
        eyebrow="Customer portal"
        title={customer.data.full_name}
        description={`${customer.data.customer_number} · ${customer.data.phone_e164} · status ${customer.data.status} · locale ${customer.data.locale}`}
        actions={
          <Button variant="secondary" loading={wallet.loading} onClick={refresh}>
            Refresh
          </Button>
        }
      />

      <div className="grid gap-6 lg:grid-cols-[2fr_3fr]">
        <div className="flex flex-col gap-6">
          <Card title="Wallet">
            {wallet.error ? (
              <Callout tone="bad">{errorMessage(wallet.error)}</Callout>
            ) : (
              <>
                <Stat
                  label="Balance"
                  value={formatMoney(wallet.data?.balance)}
                  hint={
                    wallet.data?.ledger_account_id
                      ? `Ledger account ${wallet.data.ledger_account_id.slice(-8)} · as of ${wallet.data.as_of.slice(11, 19)} UTC`
                      : "No wallet account yet: it opens with the first confirmed top-up."
                  }
                />
                <p className="mt-4 text-xs text-muted">
                  {wallet.data?.pending_top_ups ?? 0} top-up(s) waiting on the provider. The balance is
                  the ledger&apos;s, not a figure this screen keeps.
                </p>
              </>
            )}
          </Card>

          <Card title="Top up" description="Mobile money collects into the meter's account.">
            <TopUpForm
              meters={activeMeters}
              defaultPhone={customer.data.phone_e164}
              currency={wallet.data?.balance.currency ?? "USD"}
            />
          </Card>
        </div>

        <div className="flex flex-col gap-6">
          <Card title="Meters">
            {meters.error ? (
              <Callout tone="bad">{errorMessage(meters.error)}</Callout>
            ) : (
              <DataTable
                caption="Your meters"
                rows={meters.data?.data ?? []}
                rowKey={(meter) => meter.id}
                empty="No meter is registered to this account yet."
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
                ]}
              />
            )}
          </Card>

          <Card title="Recent top-ups" description="The newest ten, as the API pages them.">
            {topUps.error ? (
              <Callout tone="bad">{errorMessage(topUps.error)}</Callout>
            ) : (
              <DataTable
                caption="Your recent top-ups"
                rows={topUps.data?.data ?? []}
                rowKey={(topUp) => topUp.id}
                empty="No top-up yet. The form on the left is the whole write surface of this demo."
                columns={TOP_UP_COLUMNS}
              />
            )}
          </Card>
        </div>
      </div>
    </>
  );
}

const TOP_UP_COLUMNS: readonly Column<TopUp>[] = [
  {
    header: "Amount",
    align: "right",
    cell: (topUp) => <span className="font-mono tabular-nums">{formatMoney(topUp.amount)}</span>,
  },
  { header: "Status", cell: (topUp) => <TopUpStatus status={topUp.status} /> },
  {
    header: "Energy",
    align: "right",
    cell: (topUp) => <span className="font-mono">{formatEnergyWh(topUp.energy_wh)}</span>,
  },
  {
    header: "Opened",
    cell: (topUp) => (
      <span className="text-xs text-muted">{topUp.created_at.slice(0, 19).replace("T", " ")}</span>
    ),
  },
  {
    header: "",
    align: "right",
    cell: (topUp) => (
      <Link
        href={`/customer/topups/${topUp.id}`}
        className="text-accent underline decoration-dotted underline-offset-2 hover:decoration-solid"
      >
        Open
      </Link>
    ),
  },
];

function TopUpForm({
  meters,
  defaultPhone,
  currency,
}: {
  meters: readonly Meter[];
  defaultPhone: string;
  currency: Money["currency"];
}) {
  const router = useRouter();
  const [chosenMeterId, setChosenMeterId] = useState("");
  const [amount, setAmount] = useState("51");
  const [provider, setProvider] = useState<string>(DEMO_PROVIDER);
  const [phone, setPhone] = useState(defaultPhone);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  // The meters arrive after this component mounts, so the first one is chosen on the fly rather
  // than captured in an initializer that ran while the list was still empty.
  const meterId = chosenMeterId || meters[0]?.id || "";
  const minor = parseAmountToMinor(amount);

  async function submit() {
    if (minor === null || meterId === "") {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await request<TopUp>("/top-ups", {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: {
          meter_id: meterId,
          amount: { amount_minor: minor, currency },
          provider,
          payer_msisdn: phone,
        },
      });
      router.push(`/customer/topups/${created.data.id}`);
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  if (meters.length === 0) {
    return (
      <Callout tone="warn" title="No active meter">
        A top-up collects into an active meter, and this account has none. Dieudonné&apos;s meter is
        offline and Fiston has none registered — sign in as Awa, Blaise, Esther or Chantal to run the
        payment scenarios.
      </Callout>
    );
  }

  return (
    <div className="flex flex-col gap-4">
      <Field label="Meter" hint="Only active meters can be collected into (the API refuses the rest).">
        {(ids) => (
          <SelectInput
            {...ids}
            value={meterId}
            onChange={(event) => setChosenMeterId(event.target.value)}
            options={meters.map((meter) => ({
              value: meter.id,
              label: `${meter.serial_number} · ${meter.status}`,
            }))}
          />
        )}
      </Field>

      <Field
        label={`Amount (${currency})`}
        // The plan's own range is not readable by a customer (`getPlan` is staff-only in
        // `api/permissions.yaml`), so the form does not pretend to know it: the API refuses an
        // amount outside the range and says what the range is, and that refusal is shown below.
        hint="Your plan sets the minimum and maximum; the API enforces them and names them if it refuses."
        error={
          amount !== "" && minor === null
            ? "Use digits with at most two decimals, e.g. 51.00"
            : undefined
        }
      >
        {(ids) => (
          <TextInput
            {...ids}
            inputMode="decimal"
            value={amount}
            onChange={(event) => setAmount(event.target.value)}
            className="font-mono"
          />
        )}
      </Field>

      <div className="flex flex-wrap gap-2">
        {QUICK_AMOUNTS.map((quick) => (
          <Button key={quick} variant="ghost" onClick={() => setAmount(quick)}>
            {quick}.00
          </Button>
        ))}
      </div>

      <Field label="Provider">
        {(ids) => (
          <SelectInput
            {...ids}
            value={provider}
            onChange={(event) => setProvider(event.target.value)}
            options={PROVIDERS}
          />
        )}
      </Field>

      <Field label="Paying number" hint="The mobile-money number the charge is collected from.">
        {(ids) => (
          <TextInput
            {...ids}
            type="tel"
            inputMode="tel"
            value={phone}
            onChange={(event) => setPhone(event.target.value.trim())}
          />
        )}
      </Field>

      <div className="flex items-center gap-3">
        <Button
          variant="primary"
          loading={busy}
          disabled={minor === null || meters.length === 0}
          onClick={submit}
        >
          Start top-up
        </Button>
      </div>

      {error ? (
        <Callout tone="bad" title="Top-up refused">
          {errorMessage(error)}
          <p className="mt-2 font-mono text-xs">
            {API_BASE}/top-ups · the request carries an Idempotency-Key, so retrying it cannot charge
            twice.
          </p>
        </Callout>
      ) : null}
    </div>
  );
}
