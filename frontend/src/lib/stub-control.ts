/**
 * Server-only access to the provider double's control API.
 *
 * `tools/provider_stub` guards everything under `/control/` with a bearer token
 * (`STUB_CONTROL_TOKEN`). That value is a secret in the sense that matters here: it lets its holder
 * make the provider complete, fail or misreport any collection, so it must never reach client-side
 * JavaScript. Only the two route handlers under `src/app/sandbox/` import this module, and they run
 * on the server (demo-shell-plan.md §6).
 *
 * Deliberately outside `/api/`: the edge balancer routes every path beginning `/api/` to the Django
 * backend (`infra/lb/haproxy.cfg`'s `is_api` ACL, evaluated before any catch-all), so a route handler
 * under `src/app/api/` is unreachable in the deployed shape (`make up WEB=real`) — the balancer 404s
 * it before Next.js ever sees the request. `/sandbox/` is not claimed by any ACL, so it reaches
 * `be_web` like any other page.
 *
 * The base URL is chosen the same way compose and a developer machine differ: inside the compose
 * network the service answers to `provider-stub:9100`, on a laptop it is published on `localhost:9100`.
 *
 * Both values come from the environment. `loadServerEnv()` reads the repository root `.env` into it
 * first, because `next dev` does not do that for the route handler runtime on its own (see that
 * module for the exact reason).
 */

import { loadServerEnv } from "./server-env";

loadServerEnv();

const CONTROL_TOKEN = process.env.STUB_CONTROL_TOKEN ?? "";

export const STUB_BASE_URL =
  process.env.PROVIDER_STUB_BASE_URL ??
  (process.env.NODE_ENV === "production"
    ? "http://provider-stub:9100"
    : "http://localhost:9100");

export type ControlResult = {
  ok: boolean;
  status: number;
  body: unknown;
  error?: string;
};

/** One token-guarded call to the double. A failure is a value, not a throw: the caller reports it. */
export async function control(path: string, body?: unknown): Promise<ControlResult> {
  if (!CONTROL_TOKEN) {
    return {
      ok: false,
      status: 0,
      body: null,
      error:
        "STUB_CONTROL_TOKEN is not set for hub-web; the sandbox panel cannot reach the provider double.",
    };
  }
  try {
    const response = await fetch(`${STUB_BASE_URL}${path}`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${CONTROL_TOKEN}`,
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
    const raw = await response.text();
    let parsed: unknown = raw;
    try {
      parsed = raw ? JSON.parse(raw) : null;
    } catch {
      // The double answers JSON; a proxy in between may not. Keep the text, report the status.
    }
    return { ok: response.ok, status: response.status, body: parsed };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      body: null,
      error: `the provider double at ${STUB_BASE_URL} did not answer: ${String(error)}`,
    };
  }
}
