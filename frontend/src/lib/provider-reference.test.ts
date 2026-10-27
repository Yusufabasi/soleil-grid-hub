import { describe, expect, it } from "vitest";

import { providerReference } from "./provider-reference";

/**
 * The sandbox panel addresses the provider double's collection by a reference it derives locally.
 * `tools/provider_stub/src/provider_stub/state.py::provider_reference_for` computes the same value,
 * and the end-to-end suite (`backend/tests/e2e/test_topup_with_stub.py`) asserts the two agree —
 * these are the values Python's implementation produces, so a change here fails before a demo does.
 */
describe("providerReference", () => {
  it("derives the double's own reference from the top-up id", () => {
    expect(providerReference("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0003")).toBe("MP385C6B7212");
    expect(providerReference("abc")).toBe("MPBA7816BF8F");
  });

  it("is ten uppercase hex characters after the prefix", () => {
    const reference = providerReference("0192f3a4-7b1c-7d2e-8a3f-5c6d7e8f0101");
    expect(reference).toMatch(/^MP[0-9A-F]{10}$/);
  });
});
