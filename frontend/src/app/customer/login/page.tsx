"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button, Callout, Card, Field, PageHeader, TextInput } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { devMintToken, proofChallenge, requestCustomerOtp, verifyCustomerOtp } from "@/lib/auth";
import { DEMO_CUSTOMERS } from "@/lib/demo-accounts";
import { solveProof, type ProofProgress } from "@/lib/pow";
import { useSignIn } from "@/lib/session";
import type { OtpChallenge } from "@/lib/types";

/**
 * The customer door, and the most interesting screen in the demo to explain.
 *
 * Everything up to the code is real and runs here in the browser: a phone number, a one-time-code
 * request behind a **hashcash proof of work** (the client finds a nonce whose `sha256` starts with
 * `difficulty` zero bits, and the server spends the challenge whether or not the solve was right), and
 * the same three hourly ceilings the backend applies to real numbers.
 *
 * Then the demo has to be honest about one thing. The code is never retrievable — not over HTTP, not
 * from a log, not in plaintext in the database (`hub.identity.application.customer_otp` stores a
 * `code_hash`) — and there is no SMS gateway in this sandbox. So the step is offered and the sandbox
 * button beside it says exactly what it does: open the session through `POST /dev/mint-token`, the
 * same kind of bypass this project's own end-to-end suite performs, on a route that does not exist
 * outside a local or test environment.
 */
export default function CustomerLoginPage() {
  const signIn = useSignIn();
  const router = useRouter();

  const [phone, setPhone] = useState(DEMO_CUSTOMERS[0]!.phone);
  const [challenge, setChallenge] = useState<OtpChallenge | null>(null);
  const [code, setCode] = useState("");
  const [progress, setProgress] = useState<ProofProgress | null>(null);
  const [busy, setBusy] = useState<"request" | "verify" | "mint" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const known = DEMO_CUSTOMERS.find((customer) => customer.phone === phone);

  async function requestCode() {
    setBusy("request");
    setError(null);
    setNotice(null);
    setProgress(null);
    try {
      // Two real calls: the challenge, then the solve, then the request. The solve is what costs
      // CPU, so it reports progress instead of freezing the tab.
      const puzzle = await proofChallenge();
      const proofToken = await solveProof(puzzle.challenge, puzzle.difficulty, setProgress);
      const answered = await requestCustomerOtp(phone, proofToken, "en");
      setChallenge(answered);
      setNotice(
        `A code was requested for ${phone}. The API answers 202 whether or not the number is registered, so this is not confirmation that it is.`,
      );
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(null);
      setProgress(null);
    }
  }

  async function submitCode() {
    if (!challenge) {
      return;
    }
    setBusy("verify");
    setError(null);
    try {
      const pair = await verifyCustomerOtp(challenge.challenge_id, code.trim());
      signIn(pair);
      router.replace("/customer");
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(null);
    }
  }

  async function sandboxMint() {
    if (!known) {
      return;
    }
    setBusy("mint");
    setError(null);
    try {
      const pair = await devMintToken("customer", known.id);
      signIn(pair);
      router.replace("/customer");
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-2xl py-6">
      <PageHeader
        eyebrow="Customer portal"
        title="Sign in with your phone number"
        description="A one-time code by SMS, behind a proof-of-work challenge that makes asking for a code cost something."
      />

      {challenge === null ? (
        <Card title="Step 1 of 2 · your number">
          <div className="flex flex-col gap-4">
            <Field label="Phone number" hint="E.164. Only the prefixes the deployment allows are accepted.">
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
            <Field label="Seed a number" hint="Or type your own: the flow is the same.">
              {(ids) => (
                <select
                  {...ids}
                  value={known?.phone ?? ""}
                  onChange={(event) => setPhone(event.target.value)}
                  className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm"
                >
                  {DEMO_CUSTOMERS.map((customer) => (
                    <option key={customer.id} value={customer.phone}>
                      {customer.name} · {customer.phone} · {customer.site}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            <div className="flex items-center gap-3">
              <Button variant="primary" loading={busy === "request"} onClick={requestCode}>
                Request a code
              </Button>
              {progress ? (
                <span className="font-mono text-xs text-muted" role="status">
                  solving proof of work · {progress.attempts.toLocaleString()} attempts ·{" "}
                  {progress.seconds.toFixed(1)}s
                </span>
              ) : null}
            </div>
            {error ? <Callout tone="bad" title="Could not request a code">{errorMessage(error)}</Callout> : null}
          </div>
        </Card>
      ) : (
        <Card
          title="Step 2 of 2 · the code"
          description={`Challenge ${challenge.challenge_id.slice(-8)}, expires in ${challenge.expires_in} s. Five wrong codes kill it.`}
          actions={
            <Button
              variant="ghost"
              onClick={() => {
                setChallenge(null);
                setCode("");
                setNotice(null);
              }}
            >
              Start over
            </Button>
          }
        >
          <div className="flex flex-col gap-4">
            <Field label="Six-digit code" hint="Sent by SMS in a real deployment; there is no SMS leg here.">
              {(ids) => (
                <TextInput
                  {...ids}
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={6}
                  placeholder="000000"
                  className="font-mono tracking-[0.3em]"
                  value={code}
                  onChange={(event) => setCode(event.target.value.replace(/\D/g, ""))}
                />
              )}
            </Field>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                variant="primary"
                loading={busy === "verify"}
                disabled={code.length !== 6}
                onClick={submitCode}
              >
                Verify and sign in
              </Button>
            </div>
            {error ? <Callout tone="bad" title="Code refused">{errorMessage(error)}</Callout> : null}
          </div>
        </Card>
      )}

      {notice ? (
        <div className="mt-4">
          <Callout tone="info">{notice}</Callout>
        </div>
      ) : null}

      <div className="mt-6 flex flex-col gap-3">
        <Callout tone="neutral" title="Sandbox: no SMS gateway in this environment">
          <p>
            The one-time code is deliberately unreadable: the backend stores a hash, logs nothing, and
            there is no route or setting that returns it. So the demo opens the customer session the
            way this project&apos;s own end-to-end test does — through{" "}
            <span className="font-mono text-xs">POST /api/v1/dev/mint-token</span>, a route that is
            mounted only when <span className="font-mono text-xs">HUB_ENV</span> is{" "}
            <span className="font-mono text-xs">local</span> or{" "}
            <span className="font-mono text-xs">test</span> and is genuinely absent from the URLconf
            everywhere else.
          </p>
        </Callout>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="secondary"
            loading={busy === "mint"}
            disabled={!known}
            onClick={sandboxMint}
          >
            Open a session as {known ? known.name : "a seeded customer"}
          </Button>
          {known ? null : (
            <span className="text-xs text-muted">
              The sandbox mint needs a seeded account&apos;s id; pick one from the list above.
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
