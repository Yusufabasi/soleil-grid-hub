"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button, Callout, Card, Field, PageHeader, TextInput } from "@/components/ui";
import { errorMessage } from "@/lib/api";
import { staffLogin, staffMfaVerify } from "@/lib/auth";
import { DEMO_STAFF, DEMO_STAFF_PASSWORD, type DemoStaffAccount } from "@/lib/demo-accounts";
import { useSignIn } from "@/lib/session";
import { secondsUntilNextStep, totpCode } from "@/lib/totp";
import type { MfaChallenge } from "@/lib/types";

function accountFor(email: string): DemoStaffAccount | undefined {
  return DEMO_STAFF.find((account) => account.email === email);
}

/**
 * The staff door: password, then a one-time code (D-16's break-glass chain).
 *
 * Both steps are real. Step one is the account's password and answers a five-minute `mfa_token`, not
 * a session. Step two is a genuine TOTP code, checked against the same window and replay guard the
 * backend uses for every operator — and because these are throwaway accounts whose secrets are
 * published in the repository, the screen can compute the current code for you and says so. That is
 * the only shortcut here, and it is a property of the seed data, not of the flow.
 *
 * On success the access token goes into memory and the refresh token arrives as the `httpOnly`
 * cookie the backend sets; the shell then sends you to the customer directory.
 */
export default function StaffLoginPage() {
  const signIn = useSignIn();
  const router = useRouter();

  const [email, setEmail] = useState(DEMO_STAFF[0]!.email);
  const [password, setPassword] = useState(DEMO_STAFF_PASSWORD);
  const [challenge, setChallenge] = useState<MfaChallenge | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState<"password" | "code" | "compute" | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [note, setNote] = useState<string | null>(null);

  const selected = accountFor(email);

  async function submitPassword() {
    setBusy("password");
    setError(null);
    try {
      const answered = await staffLogin(email, password);
      setChallenge(answered);
      setNote(
        answered.enrollment_required
          ? "This account has no second factor enrolled: the backend is asking you to enrol before it will finish the login, and enrolling is outside this demo's scope."
          : null,
      );
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(null);
    }
  }

  async function submitCode() {
    if (!challenge) {
      return;
    }
    setBusy("code");
    setError(null);
    try {
      const pair = await staffMfaVerify(challenge.mfa_token, code.trim());
      signIn(pair);
      router.replace("/staff/customers");
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(null);
    }
  }

  async function computeCode() {
    if (!selected) {
      return;
    }
    setBusy("compute");
    setError(null);
    try {
      const computed = await totpCode(selected.totpSecret);
      setCode(computed);
      setNote(
        `Computed from the published secret in db/seed/test_users.json (${selected.totpSecret}). It is valid for another ${secondsUntilNextStep()} s.`,
      );
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mx-auto max-w-xl py-6">
      <PageHeader
        eyebrow="Staff console"
        title="Sign in"
        description="Password first, then the six-digit code. The code is checked against the same clock, window and replay guard as every operator's."
      />

      {challenge === null ? (
        <Card title="Step 1 of 2 · password">
          <div className="flex flex-col gap-4">
            <Field label="Account">
              {(ids) => (
                <select
                  {...ids}
                  value={email}
                  onChange={(event) => {
                    setEmail(event.target.value);
                    setError(null);
                    setNote(null);
                  }}
                  className="w-full rounded-md border border-line bg-surface px-3 py-2 text-sm"
                >
                  {DEMO_STAFF.map((account) => (
                    <option key={account.email} value={account.email}>
                      {account.name} · {account.role} · {account.email}
                    </option>
                  ))}
                </select>
              )}
            </Field>
            {selected ? (
              <p className="text-xs text-muted">
                Sees {selected.sees}. The password is the published one from{" "}
                <span className="font-mono">db/seed/test_users.json</span>.
              </p>
            ) : null}
            <Field label="Password" hint="Prefilled with the seeded account's published password.">
              {(ids) => (
                <TextInput
                  {...ids}
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                />
              )}
            </Field>
            <div>
              <Button variant="primary" loading={busy === "password"} onClick={submitPassword}>
                Continue
              </Button>
            </div>
            {error ? <Callout tone="bad" title="Sign-in refused">{errorMessage(error)}</Callout> : null}
          </div>
        </Card>
      ) : (
        <Card
          title="Step 2 of 2 · one-time code"
          description={`The challenge expires in ${Math.round(challenge.mfa_token_expires_in / 60)} minutes. Methods offered: ${challenge.methods.join(", ")}.`}
          actions={
            <Button variant="ghost" onClick={() => { setChallenge(null); setCode(""); setNote(null); }}>
              Back
            </Button>
          }
        >
          <div className="flex flex-col gap-4">
            <Field
              label="Six-digit code"
              hint="From an authenticator app, or computed below from the account's published secret."
            >
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
                loading={busy === "code"}
                disabled={code.length !== 6}
                onClick={submitCode}
              >
                Verify and sign in
              </Button>
              <Button variant="secondary" loading={busy === "compute"} onClick={computeCode}>
                Compute the code for me
              </Button>
            </div>
            {error ? <Callout tone="bad" title="Code refused">{errorMessage(error)}</Callout> : null}
          </div>
        </Card>
      )}

      {note ? (
        <div className="mt-4">
          <Callout tone="info">{note}</Callout>
        </div>
      ) : null}

      <div className="mt-6">
        <Callout tone="neutral" title="About this session">
          The access token is kept in memory only and the refresh token is an <span className="font-mono">httpOnly</span>{" "}
          cookie, so a hard refresh signs you out by design. Signing in as each seeded role in turn is
          the quickest way to see the authorization matrix do its job.
        </Callout>
      </div>
    </div>
  );
}
