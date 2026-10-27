import { NextResponse } from "next/server";

import { providerReference } from "@/lib/provider-reference";
import { control } from "@/lib/stub-control";

/**
 * The sandbox's first button: tell the provider double that the payer entered their PIN.
 *
 * This is the hop the real world does with a phone. The double then delivers its signed webhook
 * through the real balancer to `hub-ingest-webhooks`, and the top-up the customer is watching turns
 * `confirmed` on its own — no backend route is added for the demo, and nothing about the payment path
 * is faked (demo-shell-plan.md §6).
 *
 * The provider's own reference is derived from the top-up id (`"MP" + sha256(id)[:10]`), which is a
 * pure function of a value the browser already has, so no extra endpoint is needed to learn it.
 */
export async function POST(request: Request) {
  const body = (await request.json().catch(() => null)) as { top_up_id?: unknown } | null;
  const topUpId = typeof body?.top_up_id === "string" ? body.top_up_id : null;
  if (!topUpId) {
    return NextResponse.json({ error: "top_up_id is required" }, { status: 400 });
  }

  const reference = providerReference(topUpId);
  const result = await control(`/control/collections/${reference}/complete`, {
    outcome: "successful",
  });
  return NextResponse.json(
    { provider_reference: reference, stub_status: result.status, stub: result.body, error: result.error },
    { status: result.ok ? 200 : 502 },
  );
}
