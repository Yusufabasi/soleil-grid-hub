import { resolve } from "node:path";

import { loadEnvConfig } from "@next/env";
import type { NextConfig } from "next";

// The stack's variables live in the repository root `.env` (the file `make env` writes and compose
// reads). Nothing is copied out of it, and no value with SECRET/KEY/TOKEN/PASSWORD in its name ever
// gets a NEXT_PUBLIC_ prefix (demo-shell-plan.md §4).
//
// This load covers the values read *here*, at config time (HUB_DEV_API_UPSTREAM, below). It does
// not reach the route handler runtime in this Next version, so the two server-only values the
// sandbox routes need are read by `src/lib/server-env.ts`, in the server process, where it works.
loadEnvConfig(resolve(__dirname, ".."), process.env.NODE_ENV !== "production");

// Where `next dev` forwards /api/v1 to: the HAProxy balancer of the local stack.
const DEV_API_UPSTREAM = process.env.HUB_DEV_API_UPSTREAM ?? "http://localhost:8080";

const nextConfig: NextConfig = {
  // What compose.web.yaml and the balancer already assume: the standalone server on port 3000 with
  // a `/web/health` route for the `be_web` health check.
  output: "standalone",
  reactStrictMode: true,
  poweredByHeader: false,
  images: { unoptimized: true },
  // One origin for the browser in both modes, which is why no CORS configuration exists anywhere:
  // during `next dev` this forwards the API calls to the balancer, and in the deployed shape
  // (`make up WEB=real`) the browser reaches the balancer directly and this rewrite is not
  // installed at all (demo-shell-plan.md §4).
  async rewrites() {
    if (process.env.NODE_ENV === "production") {
      return [];
    }
    return [
      {
        source: "/api/v1/:path*",
        destination: `${DEV_API_UPSTREAM}/api/v1/:path*`,
      },
    ];
  },
};

export default nextConfig;
