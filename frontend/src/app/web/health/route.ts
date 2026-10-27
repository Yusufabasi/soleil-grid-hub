/**
 * The `be_web` health check (`infra/lb/haproxy.cfg`):
 *
 *   http-check send meth GET uri /web/health hdr Host hub
 *   http-check expect status 200
 *
 * Cheap, cacheable by nobody, and it says nothing about the API — the balancer already checks
 * `be_api` separately, and a web tier whose liveness depends on the database would take the pool out
 * during a database incident instead of continuing to serve the shell.
 */
export function GET() {
  return new Response("ok\n", {
    status: 200,
    headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
  });
}

// A health check is a GET; anything else on this path is not a health check.
export const dynamic = "force-static";
