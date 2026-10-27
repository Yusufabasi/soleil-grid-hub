import { NextResponse } from "next/server";

import { control } from "@/lib/stub-control";

/**
 * The sandbox's second button: make the provider send a *different* amount than it collected.
 *
 * The mismatch is the demo's centrepiece (§6). A signed `payment.confirmed` for another amount is
 * exactly what the backend must refuse to credit — it routes the posting to the suspense account and
 * leaves the customer's top-up unconfirmed, which is the "every franc is accounted for even when the
 * provider disagrees with us" story.
 *
 * The scenario matches on `{provider, reference}`. The double's `reference` is the value the hub sent
 * when it initiated the collection — the top-up id itself
 * (`application/initiate_top_up.py`: `provider_ref=str(top_up_id)`), *not* the `MP…` id the double
 * derives from it. Matching on the top-up id is what keeps the fault to this one collection; a match
 * on the amount instead (as the end-to-end test does, having no id in hand) would touch any pending
 * collection for the same amount.
 */
export async function POST(request: Request) {
  const body = (await request.json().catch(() => null)) as {
    top_up_id?: unknown;
    provider?: unknown;
    mode?: unknown;
    params?: unknown;
  } | null;

  const topUpId = typeof body?.top_up_id === "string" ? body.top_up_id : null;
  const mode = typeof body?.mode === "string" ? body.mode : null;
  const provider = typeof body?.provider === "string" ? body.provider : "mpesa_cd";
  const params =
    body?.params && typeof body.params === "object" ? (body.params as Record<string, unknown>) : {};

  if (!topUpId || !mode) {
    return NextResponse.json({ error: "top_up_id and mode are required" }, { status: 400 });
  }

  const result = await control("/control/scenarios", {
    mode,
    params,
    match: { provider, reference: topUpId },
  });
  return NextResponse.json(
    {
      hub_reference: topUpId,
      mode,
      stub_status: result.status,
      stub: result.body,
      error: result.error,
    },
    { status: result.ok ? 200 : 502 },
  );
}
