/**
 * The sign-in calls, one function per endpoint, so a screen never builds a request itself.
 *
 * Two doors, exactly as the plan draws them (§5): staff sign in with a password and a TOTP code,
 * customers with a phone number, a solved proof of work and a one-time code. The third entry is the
 * **sandbox mint** — `POST /dev/mint-token` (demo-shell-plan.md §3) — which exists only while
 * `HUB_ENV` is `local` or `test` and is what the demo uses for a customer session, because the real
 * one-time code is unreachable over HTTP by design and there is no SMS gateway in this sandbox.
 *
 * That route answers the same `TokenPair` document as the real endpoints, so `applyTokenPair` is the
 * one place a session is opened.
 */

import { apiGet, apiPost } from "./api";
import type {
  MfaChallenge,
  OtpChallenge,
  Principal,
  ProofChallenge,
  TokenPair,
} from "./types";

/** Step one: password. Answers a challenge whether or not the account exists. */
export async function staffLogin(email: string, password: string): Promise<MfaChallenge> {
  return apiPost<MfaChallenge>(
    "/auth/staff/login",
    { email, password },
    { auth: false },
  );
}

/** Step two: the six-digit code. This is where the refresh cookie is set. */
export async function staffMfaVerify(mfaToken: string, totpCode: string): Promise<TokenPair> {
  return apiPost<TokenPair>("/auth/staff/mfa/verify", { mfa_token: mfaToken, totp_code: totpCode }, {
    auth: false,
  });
}

/** A good citizen's sign-out: revokes the session the cookie points at, then the cookie is gone. */
export async function logout(): Promise<void> {
  await apiPost<void>("/auth/logout", {});
}

/** The customer's puzzle: a secret and the number of zero bits to find. */
export async function proofChallenge(): Promise<ProofChallenge> {
  return apiGet<ProofChallenge>("/auth/proof-challenge", { auth: false });
}

/** Ask for a code. Always 202 with a challenge; no SMS is sent to an unknown number. */
export async function requestCustomerOtp(
  phoneE164: string,
  proofToken: string,
  locale: "en" | "fr" = "en",
): Promise<OtpChallenge> {
  return apiPost<OtpChallenge>(
    "/auth/customer/otp/request",
    { phone_e164: phoneE164, proof_token: proofToken, locale },
    { auth: false },
  );
}

/** Spend the challenge. Five wrong codes kill it (the backend counts them per challenge). */
export async function verifyCustomerOtp(challengeId: string, code: string): Promise<TokenPair> {
  return apiPost<TokenPair>(
    "/auth/customer/otp/verify",
    { challenge_id: challengeId, code },
    { auth: false },
  );
}

/**
 * The sandbox mint: a customer session without the SMS hop.
 *
 * `kind: "staff"` exists too, and the demo does not use it for the staff door — the staff flow is
 * shown end to end, password and TOTP included — but a script or a future screen can.
 */
export async function devMintToken(
  kind: "staff" | "customer",
  subject: string,
): Promise<TokenPair> {
  return apiPost<TokenPair>("/dev/mint-token", { kind, subject }, { auth: false });
}

/** `getMe`: the principal as the API sees it, used to re-read roles after a refresh. */
export async function getMe(): Promise<Principal> {
  return apiGet<Principal>("/me");
}
