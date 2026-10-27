/**
 * TOTP, computed in the browser so the staff demo can finish step two honestly.
 *
 * The five seeded staff accounts have their TOTP secrets published in the repository on purpose
 * (`db/seed/test_users.json`: "Every secret here is published in the repository on purpose: none of
 * these accounts exists outside a throwaway database"). Without this, a demo would have to type a
 * code from a phone that has never enrolled those accounts — so the login screen offers to compute
 * the current code from the published secret, and says that is what it is doing.
 *
 * The algorithm is the standard one the backend implements with pyotp: base32 secret, SHA-1 HMAC,
 * 30-second step, six digits, truncated dynamically. `totp.test.ts` pins the codes against values
 * produced by pyotp itself, so the two implementations cannot drift.
 *
 * This file is deliberately not a general TOTP library: it has no drift compensation, no recovery
 * codes and no secret generator, because the demo needs exactly one thing from it.
 */

const BASE32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
const STEP_SECONDS = 30;
const DIGITS = 6;

/** RFC 4648 base32, case-insensitive, padding and spaces ignored. */
export function base32Decode(secret: string): Uint8Array {
  const cleaned = secret.replace(/[\s=]/g, "").toUpperCase();
  const bytes: number[] = [];
  let buffer = 0;
  let bits = 0;
  for (const character of cleaned) {
    const value = BASE32_ALPHABET.indexOf(character);
    if (value === -1) {
      throw new Error(`not a base32 character: ${character}`);
    }
    buffer = (buffer << 5) | value;
    bits += 5;
    if (bits >= 8) {
      bits -= 8;
      bytes.push((buffer >>> bits) & 0xff);
    }
  }
  return new Uint8Array(bytes);
}

/** The six-digit code for `secret` at `atMs` (defaults to now). */
export async function totpCode(secret: string, atMs: number = Date.now()): Promise<string> {
  const counter = Math.floor(atMs / 1000 / STEP_SECONDS);
  const key = await crypto.subtle.importKey(
    "raw",
    base32Decode(secret) as unknown as ArrayBuffer,
    { name: "HMAC", hash: "SHA-1" },
    false,
    ["sign"],
  );
  const message = new Uint8Array(8);
  const view = new DataView(message.buffer);
  view.setUint32(0, Math.floor(counter / 2 ** 32));
  view.setUint32(4, counter >>> 0);

  const digest = new Uint8Array(await crypto.subtle.sign("HMAC", key, message));
  // Dynamic truncation (RFC 4226 §5.4): the low nibble of the last byte picks the offset.
  const offset = digest[digest.length - 1]! & 0x0f;
  const binary =
    ((digest[offset]! & 0x7f) << 24) |
    (digest[offset + 1]! << 16) |
    (digest[offset + 2]! << 8) |
    digest[offset + 3]!;
  return String(binary % 10 ** DIGITS).padStart(DIGITS, "0");
}

/** Seconds until the current code rolls over — what a countdown in the UI would show. */
export function secondsUntilNextStep(atMs: number = Date.now()): number {
  return STEP_SECONDS - (Math.floor(atMs / 1000) % STEP_SECONDS);
}
