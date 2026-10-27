import { describe, expect, it } from "vitest";

import { base32Decode, totpCode } from "./totp";

/**
 * The staff login computes a real TOTP code, and the backend verifies it with pyotp. These vectors
 * were produced by pyotp itself for the three seeded accounts' published secrets, so the two
 * implementations cannot drift into a "the demo cannot sign in" failure nobody would enjoy debugging.
 */
const VECTORS: readonly { secret: string; atMs: number; code: string }[] = [
  { secret: "JBSWY3DPEHPK3PXP", atMs: 0, code: "282760" },
  { secret: "JBSWY3DPEHPK3PXP", atMs: 30_000, code: "996554" },
  { secret: "JBSWY3DPEHPK3PXP", atMs: 1_234_567_890_000, code: "742275" },
  { secret: "JBSWY3DPEHPK3PXP", atMs: 1_800_000_000_000, code: "309848" },
  { secret: "KRSXG5CTMVRXEZLU", atMs: 0, code: "546858" },
  { secret: "KRSXG5CTMVRXEZLU", atMs: 30_000, code: "043081" },
  { secret: "KRSXG5CTMVRXEZLU", atMs: 1_234_567_890_000, code: "371074" },
  { secret: "GEZDGNBVGY3TQOJQ", atMs: 0, code: "891490" },
  { secret: "GEZDGNBVGY3TQOJQ", atMs: 1_234_567_890_000, code: "919219" },
  { secret: "GEZDGNBVGY3TQOJQ", atMs: 1_800_000_000_000, code: "388890" },
];

describe("base32Decode", () => {
  it("decodes the seeded secrets as pyotp does", () => {
    // "Hello!\xDE\xAD\xBE\xEF" in base32 is the RFC's own example, and the first seeded secret.
    expect([...base32Decode("JBSWY3DPEHPK3PXP")]).toEqual([
      0x48, 0x65, 0x6c, 0x6c, 0x6f, 0x21, 0xde, 0xad, 0xbe, 0xef,
    ]);
  });

  it("ignores case, spaces and padding", () => {
    expect([...base32Decode("jbswy3dp ehpk3pxp==")]).toEqual([...base32Decode("JBSWY3DPEHPK3PXP")]);
  });
});

describe("totpCode", () => {
  it.each(VECTORS)("matches pyotp for $secret at $atMs", async ({ secret, atMs, code }) => {
    expect(await totpCode(secret, atMs)).toBe(code);
  });

  it("pads a code whose truncated value has leading zeros", () => {
    // The vector above proves the same thing end to end: pyotp answers "043081", six characters.
    expect(VECTORS.find((vector) => vector.code.startsWith("0"))?.code).toHaveLength(6);
  });
});
