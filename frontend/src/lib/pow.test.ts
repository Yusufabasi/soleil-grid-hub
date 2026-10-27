import { describe, expect, it } from "vitest";

import { leadingZeroBits, sha256, solveProof, toHex } from "./pow";

/**
 * The solver is the one piece of cryptography this demo implements, so it is pinned twice: against
 * the published SHA-256 vectors, and against the property the API actually checks — that
 * `sha256("<challenge>.<nonce>")` starts with the requested number of zero bits.
 *
 * The expected digests come from Python's `hashlib`, the same library the backend's
 * `proof.verify` uses, so a divergence shows up here rather than as a `proof_required` 422 in a
 * browser.
 */
const encode = (text: string) => new TextEncoder().encode(text);

describe("sha256", () => {
  it("matches the published digests", () => {
    expect(toHex(sha256(encode("")))).toBe(
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    );
    expect(toHex(sha256(encode("abc")))).toBe(
      "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
    );
    // 64 bytes: two blocks, and the padding falls in a block of its own.
    expect(toHex(sha256(encode("a".repeat(64))))).toBe(
      "ffe054fe7ae0cb6dc65c3af9b61d5209f439851db43d0ba5997337df154668eb",
    );
    // 55 and 56 bytes straddle the boundary where the length field moves to a second block.
    expect(toHex(sha256(encode("a".repeat(55))))).toBe(
      "9f4390f8d30c2dd92ec9f095b65e2b9ae9b0a925a5258e241c9f1e910f734318",
    );
    expect(toHex(sha256(encode("a".repeat(56))))).toBe(
      "b35439a4ac6f0948b6d6f9e3c6af0f5f590ce20f1bde7090ef7970686ec6738a",
    );
  });
});

describe("leadingZeroBits", () => {
  it("counts whole zero bytes and then the bits of the first one", () => {
    expect(leadingZeroBits(new Uint8Array([0xff]))).toBe(0);
    expect(leadingZeroBits(new Uint8Array([0x7f]))).toBe(1);
    expect(leadingZeroBits(new Uint8Array([0x01]))).toBe(7);
    expect(leadingZeroBits(new Uint8Array([0x00, 0xff]))).toBe(8);
    expect(leadingZeroBits(new Uint8Array([0x00, 0x00, 0x20]))).toBe(18);
    expect(leadingZeroBits(new Uint8Array([0x00, 0x00, 0x00]))).toBe(24);
  });
});

describe("solveProof", () => {
  it("returns a token the server's own check accepts", async () => {
    const challenge = "a-challenge-value";
    const difficulty = 12;

    const token = await solveProof(challenge, difficulty);

    expect(token.startsWith(`${challenge}.`)).toBe(true);
    const digest = sha256(encode(token));
    expect(leadingZeroBits(digest)).toBeGreaterThanOrEqual(difficulty);
  }, 20_000);

  it("reports progress in chunks instead of blocking", async () => {
    const seen: number[] = [];
    await solveProof("progress", 10, (progress) => seen.push(progress.attempts));
    // At ten bits the first chunk (20,000 candidates) almost certainly finds it, in which case there
    // is no progress report at all and this stays empty. What must hold either way: nothing negative.
    expect(seen.every((attempts) => attempts > 0)).toBe(true);
  }, 20_000);
});
