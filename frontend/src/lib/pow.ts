/**
 * The proof of work the customer sign-in solves in the browser (D-21, demo-shell-plan.md §2).
 *
 * `GET /auth/proof-challenge` answers a random secret and a difficulty in **zero bits**; the client
 * finds a nonce such that `sha256("<secret>.<nonce>")` starts with at least that many zero bits, and
 * presents `"<secret>.<nonce>"` with the code request. `hub.identity.application.proof.verify` pops
 * the secret before it checks anything, so a solve is spent whether or not it was correct — the
 * client gets one attempt per challenge and asks for a new one otherwise.
 *
 * SHA-256 is implemented here rather than taken from a library for two reasons: `crypto.subtle` is
 * asynchronous, and a quarter of a million awaited digests (the expected work at 18 bits) would take
 * minutes; and a synchronous hash lets the loop yield on a timer so the browser keeps painting. The
 * result is checked against the server's own function on every sign-in, and against the published
 * vectors in `pow.test.ts`.
 */

const SHA256_K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

function rotateRight(value: number, bits: number): number {
  return ((value >>> bits) | (value << (32 - bits))) >>> 0;
}

/** SHA-256 of `bytes`. Synchronous on purpose: see the module note. */
export function sha256(bytes: Uint8Array): Uint8Array {
  const state = new Uint32Array([
    0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
  ]);
  const length = bytes.length;
  // The message, the 0x80 byte, zero padding to 56 mod 64, then the 64-bit big-endian length.
  const padded = new Uint8Array((((length + 8) >> 6) + 1) << 6);
  padded.set(bytes);
  padded[length] = 0x80;
  const view = new DataView(padded.buffer);
  view.setUint32(padded.length - 8, Math.floor(length / 0x20000000));
  view.setUint32(padded.length - 4, (length << 3) >>> 0);

  const schedule = new Uint32Array(64);
  for (let block = 0; block < padded.length; block += 64) {
    for (let index = 0; index < 16; index += 1) {
      schedule[index] = view.getUint32(block + index * 4);
    }
    for (let index = 16; index < 64; index += 1) {
      const previous15 = schedule[index - 15]!;
      const previous2 = schedule[index - 2]!;
      const s0 = rotateRight(previous15, 7) ^ rotateRight(previous15, 18) ^ (previous15 >>> 3);
      const s1 = rotateRight(previous2, 17) ^ rotateRight(previous2, 19) ^ (previous2 >>> 10);
      schedule[index] = (schedule[index - 16]! + s0 + schedule[index - 7]! + s1) >>> 0;
    }

    let a = state[0]!;
    let b = state[1]!;
    let c = state[2]!;
    let d = state[3]!;
    let e = state[4]!;
    let f = state[5]!;
    let g = state[6]!;
    let h = state[7]!;

    for (let index = 0; index < 64; index += 1) {
      const sigma1 = rotateRight(e, 6) ^ rotateRight(e, 11) ^ rotateRight(e, 25);
      const choose = (e & f) ^ (~e & g);
      const temp1 = (h + sigma1 + choose + SHA256_K[index]! + schedule[index]!) >>> 0;
      const sigma0 = rotateRight(a, 2) ^ rotateRight(a, 13) ^ rotateRight(a, 22);
      const majority = (a & b) ^ (a & c) ^ (b & c);
      const temp2 = (sigma0 + majority) >>> 0;

      h = g;
      g = f;
      f = e;
      e = (d + temp1) >>> 0;
      d = c;
      c = b;
      b = a;
      a = (temp1 + temp2) >>> 0;
    }

    state[0] = (state[0]! + a) >>> 0;
    state[1] = (state[1]! + b) >>> 0;
    state[2] = (state[2]! + c) >>> 0;
    state[3] = (state[3]! + d) >>> 0;
    state[4] = (state[4]! + e) >>> 0;
    state[5] = (state[5]! + f) >>> 0;
    state[6] = (state[6]! + g) >>> 0;
    state[7] = (state[7]! + h) >>> 0;
  }

  const digest = new Uint8Array(32);
  const digestView = new DataView(digest.buffer);
  for (let index = 0; index < 8; index += 1) {
    digestView.setUint32(index * 4, state[index]!);
  }
  return digest;
}

export function toHex(bytes: Uint8Array): string {
  let hex = "";
  for (const byte of bytes) {
    hex += byte.toString(16).padStart(2, "0");
  }
  return hex;
}

/**
 * How many zero bits a digest starts with — the server's `leading_zero_bits`, to the bit.
 *
 * It counts whole bytes while they are zero, then the leading zeros of the first non-zero byte,
 * which is exactly what `hub.identity.application.proof.leading_zero_bits` does.
 */
export function leadingZeroBits(digest: Uint8Array): number {
  let count = 0;
  for (const byte of digest) {
    if (byte === 0) {
      count += 8;
      continue;
    }
    count += 8 - byte.toString(2).length;
    break;
  }
  return count;
}

export type ProofProgress = { attempts: number; seconds: number };

/** How many candidates to try between two yields to the event loop. */
const CHUNK = 20_000;

/**
 * Find the nonce that solves `challenge` at `difficulty` zero bits.
 *
 * The loop is synchronous inside a chunk and yields between chunks, so a slow solve shows progress
 * instead of freezing the tab. At the default 18 bits the expected work is 2^18 candidates, well
 * under a second on a laptop.
 */
export async function solveProof(
  challenge: string,
  difficulty: number,
  onProgress?: (progress: ProofProgress) => void,
): Promise<string> {
  const encoder = new TextEncoder();
  const started = Date.now();
  let attempts = 0;

  for (;;) {
    for (let index = 0; index < CHUNK; index += 1) {
      const candidate = `${challenge}.${attempts}`;
      if (leadingZeroBits(sha256(encoder.encode(candidate))) >= difficulty) {
        return candidate;
      }
      attempts += 1;
    }
    onProgress?.({ attempts, seconds: (Date.now() - started) / 1000 });
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}
