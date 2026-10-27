/**
 * The provider's own reference for a collection, derived exactly as the double derives it.
 *
 * `tools/provider_stub/src/provider_stub/state.py` (and the e2e suite that pins it,
 * `backend/tests/e2e/test_topup_with_stub.py`'s `provider_reference`) compute
 * `"MP" + sha256(reference).hexdigest()[:10].upper()`, where the reference is the top-up's own id.
 *
 * That is what makes the sandbox panel possible without a second backend endpoint: the top-up id is
 * already in the URL, so the demo can address the provider's collection directly and tell the double
 * to complete it.
 */

import { sha256, toHex } from "./pow";

export const PROVIDER_REFERENCE_PREFIX = "MP";

export function providerReference(topUpId: string): string {
  return PROVIDER_REFERENCE_PREFIX + toHex(sha256(new TextEncoder().encode(topUpId))).slice(0, 10).toUpperCase();
}
