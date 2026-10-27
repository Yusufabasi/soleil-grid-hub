"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { SessionGate } from "@/components/session-gate";
import { SandboxPanel } from "@/components/sandbox-panel";
import {
  Badge,
  Button,
  Callout,
  Card,
  KeyValue,
  Loading,
  PageHeader,
  Stat,
  TopUpStatus,
} from "@/components/ui";
import { apiGet, errorMessage } from "@/lib/api";
import { formatEnergyWh, formatMoney } from "@/lib/format";
import { usePrincipal } from "@/lib/session";
import { usePolling, useResource } from "@/lib/use-resource";
import type { TopUp, Wallet } from "@/lib/types";

const POLL_INTERVAL_MS = 2000;
/** The API's own guidance for `getTopUp` is "poll for two minutes, then back off". */
const POLL_WINDOW_MS = 120_000;

const SETTLED = new Set(["confirmed", "failed", "expired", "cancelled", "reversed"]);

/**
 * One top-up, watched live.
 *
 * This is where the demo's two payment outcomes happen. The screen polls `getTopUp` every two seconds
 * while the status is unsettled, shows the wallet balance beside it (so a confirmation visibly moves
 * money), and hands the provider side to the sandbox panel: completing the collection at the provider
 * double is what makes the signed webhook arrive through the real balancer and turn this top-up
 * `confirmed` without the frontend doing anything else.
 */
export default function TopUpDetailPage() {
  return (
    <SessionGate persona="customer">
      <Detail />
    </SessionGate>
  );
}

function Detail() {
  const params = useParams<{ id: string }>();
  const topUpId = params.id;
  const principal = usePrincipal();
  const customerId = principal?.id ?? "";

  const loadTopUp = useCallback(() => apiGet<TopUp>(`/top-ups/${topUpId}`), [topUpId]);
  const loadWallet = useCallback(
    () => apiGet<Wallet>(`/customers/${customerId}/wallet`),
    [customerId],
  );
  const topUp = useResource(loadTopUp);
  const wallet = useResource(loadWallet);

  const [polling, setPolling] = useState(true);
  useEffect(() => {
    const timer = setTimeout(() => setPolling(false), POLL_WINDOW_MS);
    return () => clearTimeout(timer);
  }, []);

  const status = topUp.data?.status ?? null;
  const watching = polling && status !== null && !SETTLED.has(status);
  usePolling(() => {
    topUp.reload();
    wallet.reload();
  }, POLL_INTERVAL_MS, watching);

  if (topUp.loading && topUp.data === null) {
    return <Loading what="Reading the top-up" />;
  }
  if (topUp.error !== null && topUp.data === null) {
    return (
      <Callout tone="bad" title="Could not read this top-up">
        {errorMessage(topUp.error)}
      </Callout>
    );
  }
  const document = topUp.data;
  if (!document) {
    return null;
  }

  const confirmed = document.status === "confirmed" || document.status === "reversed";

  return (
    <>
      <PageHeader
        eyebrow="Top-up"
        title={formatMoney(document.amount)}
        description={`${document.provider} · opened ${document.created_at.slice(0, 19).replace("T", " ")} UTC`}
        actions={
          <>
            <Button variant="secondary" onClick={() => { topUp.reload(); wallet.reload(); }}>
              Refresh
            </Button>
            <Link
              href="/customer"
              className="rounded-md border border-line bg-surface px-3.5 py-2 text-sm font-medium hover:bg-accent-soft"
            >
              Back to balance
            </Link>
          </>
        }
      />

      <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
        <div className="flex flex-col gap-6">
          <Card
            title="Status"
            actions={
              watching ? (
                <span className="flex items-center gap-2 text-xs text-muted" role="status">
                  <span
                    aria-hidden="true"
                    className="size-2 animate-pulse rounded-full bg-accent"
                  />
                  polling every {POLL_INTERVAL_MS / 1000}s
                </span>
              ) : (
                <span className="text-xs text-muted">
                  {SETTLED.has(document.status) ? "settled — polling stopped" : "polling window passed"}
                </span>
              )
            }
          >
            <div className="flex flex-wrap items-end gap-8">
              <div>
                <p className="text-xs font-medium tracking-wide text-muted uppercase">State</p>
                <p className="mt-1">
                  <TopUpStatus status={document.status} />
                  {document.late ? (
                    <span className="ml-2">
                      <Badge tone="info">late credit</Badge>
                    </span>
                  ) : null}
                </p>
              </div>
              <Stat label="Energy credited" value={formatEnergyWh(document.energy_wh)} />
              <Stat
                label="Wallet now"
                value={formatMoney(wallet.data?.balance)}
                hint={wallet.data?.as_of ? `as of ${wallet.data.as_of.slice(11, 19)} UTC` : undefined}
              />
            </div>

            {confirmed ? (
              <div className="mt-5">
                <Callout tone="ok" title="Confirmed and credited">
                  The signed webhook arrived, the ledger posted once, and the wallet balance above is
                  the ledger&apos;s own number{wallet.data?.ledger_account_id ? ` for account ${wallet.data.ledger_account_id.slice(-8)}` : ""}.
                </Callout>
              </div>
            ) : null}

            {document.status === "pending" || document.status === "pending_unknown" ? (
              <div className="mt-5">
                <Callout tone="warn" title="Waiting on the provider">
                  Nothing has been credited: `ledger_transaction_id` is{" "}
                  <span className="font-mono">{String(document.ledger_transaction_id)}</span> and
                  `confirmed_at` is <span className="font-mono">{String(document.confirmed_at)}</span>.
                  In a real deployment the payer&apos;s PIN would settle this; the sandbox panel beside
                  this card plays that part.
                </Callout>
              </div>
            ) : null}

            <div className="mt-6">
              <KeyValue
                rows={[
                  { label: "Top-up id", value: document.id },
                  { label: "Meter", value: document.meter_id },
                  { label: "Fee", value: formatMoney(document.fee) },
                  { label: "Quoted energy", value: formatEnergyWh(document.quoted_energy_wh ?? null) },
                  { label: "Tariff", value: formatMoney(document.quoted_tariff ?? null) },
                  { label: "Payer", value: document.payer_msisdn_masked },
                  { label: "Provider reference", value: document.provider_reference ?? "—" },
                  { label: "Ledger transaction", value: document.ledger_transaction_id ?? "—" },
                  { label: "Reversal", value: document.reversal_transaction_id ?? "—" },
                  { label: "Confirmed at", value: document.confirmed_at ?? "—" },
                  { label: "Expires at", value: document.expires_at },
                ]}
              />
            </div>
          </Card>
        </div>

        <SandboxPanel topUp={document} onChanged={() => { topUp.reload(); wallet.reload(); }} />
      </div>
    </>
  );
}
