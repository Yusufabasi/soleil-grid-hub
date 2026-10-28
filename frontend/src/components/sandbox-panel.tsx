"use client";

import { useState } from "react";

import { Button, Callout, Card } from "@/components/ui";
import { providerReference } from "@/lib/provider-reference";
import type { TopUp } from "@/lib/types";

type Action = "complete" | "mismatch" | null;

type StubAnswer = {
  provider_reference?: string;
  hub_reference?: string;
  mode?: string;
  stub_status?: number;
  stub?: unknown;
  error?: string;
};

async function callSandbox(path: string, body: unknown): Promise<StubAnswer> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const parsed = (await response.json()) as StubAnswer;
  if (!response.ok) {
    throw new Error(parsed.error ?? `the sandbox route answered ${response.status}`);
  }
  return parsed;
}

/**
 * The two buttons that make the demo's centrepiece happen (§6 of the plan).
 *
 * Both go through a Next.js route handler, not straight from the browser, because the provider
 * double's control API needs `STUB_CONTROL_TOKEN` and a secret does not belong in a client chunk. The
 * handlers are thin proxies; nothing about the hub's own payment path is stubbed, and the webhook the
 * double sends afterwards is signed and verified exactly as a real provider's would be.
 *
 * * **PIN confirmation** — the payer typed their PIN. The double completes the collection and
 *   delivers `payment.confirmed`; this screen's own polling shows the top-up turn `confirmed` and the
 *   wallet balance move.
 * * **Amount mismatch** — the fault worth demonstrating. The provider reports a *different* amount
 *   than it collected. The backend must not credit it: the customer sees no confirmation and no money,
 *   and the posting lands on the ledger's suspense account instead. Registered against this
 *   collection's own provider reference, so no other payment can be affected.
 */
export function SandboxPanel({ topUp, onChanged }: { topUp: TopUp; onChanged: () => void }) {
  const [running, setRunning] = useState<Action>(null);
  const [answer, setAnswer] = useState<{ action: Action; stub: StubAnswer } | null>(null);
  const [scenario, setScenario] = useState<StubAnswer | null>(null);
  const [error, setError] = useState<string | null>(null);

  const reference = providerReference(topUp.id);
  const settled = ["confirmed", "failed", "expired", "cancelled", "reversed"].includes(topUp.status);

  async function confirmPin() {
    setRunning("complete");
    setError(null);
    try {
      const stub = await callSandbox("/sandbox/complete-collection", { top_up_id: topUp.id });
      setAnswer({ action: "complete", stub });
      onChanged();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setRunning(null);
    }
  }

  async function mismatchThenConfirm() {
    setRunning("mismatch");
    setError(null);
    try {
      const stored = await callSandbox("/sandbox/add-scenario", {
        // The hub's own reference for the collection — the top-up id — which is what the double
        // compares a scenario against. The `MP…` string below addresses the collection, but it is
        // not what the collection is *called* on the way in.
        top_up_id: topUp.id,
        provider: topUp.provider,
        mode: "wrong_amount",
        // The provider will claim it collected a hundred minor units more than the customer asked for.
        params: { amount_minor: topUp.amount.amount_minor + 100 },
      });
      const stub = await callSandbox("/sandbox/complete-collection", { top_up_id: topUp.id });
      setAnswer({ action: "mismatch", stub });
      setScenario(stored);
      onChanged();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setRunning(null);
    }
  }

  return (
    <Card
      title="Sandbox · the provider side"
      description="Plays the part a payer's phone plays in production. Nothing here is part of the payment path."
    >
      <div className="flex flex-col gap-5">
        <Callout tone="neutral" title="Where this goes">
          This browser asks a Next.js route handler, which holds the provider double&apos;s control
          token server-side and tells it what happened at the till. The double then signs a webhook and
          posts it through the real balancer into <span className="font-mono text-xs">hub-ingest-webhooks</span>,
          where the signature, the idempotency key and the ledger posting are all real.
        </Callout>

        <dl className="grid gap-2">
          <div>
            <dt className="text-xs font-medium tracking-wide text-muted uppercase">
              Collection reference
            </dt>
            <dd className="font-mono text-sm break-all">{reference}</dd>
          </div>
          <p className="text-xs text-muted">
            The provider&apos;s own id, derived from the top-up id as{" "}
            <span className="font-mono">MP + sha256(id)[:10]</span> — a pure function, which is why the
            sandbox needs no extra endpoint to find the collection.
          </p>
        </dl>

        <div className="flex flex-col gap-3">
          <Button
            variant="primary"
            loading={running === "complete"}
            disabled={running !== null || settled}
            onClick={confirmPin}
          >
            Simulate mobile-money PIN confirmation
          </Button>
          <p className="text-xs text-muted">
            Completes the collection successfully. Expect the status above to become{" "}
            <strong>confirmed</strong> within a couple of seconds, with the wallet balance up by{" "}
            {topUp.amount.amount_minor / 100} and one double-entry posting in the ledger.
          </p>

          <Button
            variant="danger"
            loading={running === "mismatch"}
            disabled={running !== null || settled}
            onClick={mismatchThenConfirm}
          >
            Simulate a provider amount mismatch
          </Button>
          <p className="text-xs text-muted">
            Makes the provider report {topUp.amount.amount_minor + 100} minor units for this
            collection, then completes it. Expect the top-up to stay unconfirmed with{" "}
            <span className="font-mono">ledger_transaction_id: null</span>, the wallet unchanged, and
            the posting on the ledger&apos;s <span className="font-mono">suspense</span> account — the
            observable from the customer&apos;s side is the pair of negatives: no confirmation, and no
            money.
          </p>
        </div>

        {settled ? (
          <Callout tone="neutral">
            This top-up is <strong>{topUp.status}</strong>, so the sandbox buttons are off. Start
            another top-up to run a scenario again.
          </Callout>
        ) : null}

        {error ? (
          <Callout tone="bad" title="The provider double refused">
            {error}
          </Callout>
        ) : null}

        {answer ? (
          <Callout
            tone={answer.action === "mismatch" ? "warn" : "ok"}
            title={
              answer.action === "mismatch"
                ? "Mismatch registered and the collection completed"
                : "Collection completed"
            }
          >
            <p>
              The double answered <span className="font-mono">{answer.stub.stub_status}</span> for{" "}
              <span className="font-mono">{answer.stub.provider_reference}</span>
              {scenario?.stub_status ? (
                <>
                  {" "}
                  after storing the scenario (<span className="font-mono">
                    {scenario.stub_status}
                  </span>
                  )
                </>
              ) : null}
              . It is delivering its signed webhook now; this screen keeps polling.
            </p>
          </Callout>
        ) : null}
      </div>
    </Card>
  );
}
