/**
 * The two server-only values the sandbox routes need, read from the repository root `.env`.
 *
 * Everything the stack runs on lives in one file — the root `.env` that `make env` writes and
 * `docker compose` reads — and nothing is copied out of it (demo-shell-plan.md §4). `next.config.ts`
 * loads that file for config-time values, but in this Next version the load does not reach the route
 * handler runtime (`@next/env` finds the file there and parses nothing), so the token arrived empty
 * and every sandbox button answered "not set". This module closes that gap in the only place that
 * can: the server process itself, where reading the file demonstrably works.
 *
 * Three properties keep it from becoming a second, shadow configuration:
 * - it reads exactly the two names below, not the whole file. Anything else the stack has in `.env`
 *   is not this app's business, and widening this list later should be a deliberate act.
 * - it never overrides a value already in the environment, so `STUB_CONTROL_TOKEN=... npm run dev`
 *   and the container's own variables win over the file.
 * - it does nothing in production, where `compose.web.yaml` passes both values to the container.
 *
 * It is imported only by `@/lib/stub-control`, which is imported only by the two route handlers
 * under `src/app/api/demo/`, so neither this code nor either value can reach a client chunk.
 */

import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

/** The whole of this app's server-only surface. `NEXT_PUBLIC_*` values are not read here. */
const SERVER_ONLY = ["STUB_CONTROL_TOKEN", "PROVIDER_STUB_BASE_URL"] as const;

let attempted = false;

/** `NAME=value` for the names asked for; anything else in the file is ignored. */
function readEnvFile(path: string, names: readonly string[]): Record<string, string> {
  const found: Record<string, string> = {};
  let text: string;
  try {
    text = readFileSync(path, "utf8");
  } catch {
    // A directory without a `.env` is normal (a production image, a CI job). Not an error.
    return found;
  }
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }
    const equals = trimmed.indexOf("=");
    if (equals < 1) {
      continue;
    }
    const name = trimmed.slice(0, equals).trim();
    if (!names.includes(name)) {
      continue;
    }
    let value = trimmed.slice(equals + 1).trim();
    if (value.length >= 2 && (value.startsWith('"') || value.startsWith("'"))) {
      value = value.slice(1, -1);
    }
    found[name] = value;
  }
  return found;
}

export function loadServerEnv(): void {
  if (attempted) {
    return;
  }
  attempted = true;
  if (process.env.NODE_ENV === "production") {
    return;
  }
  const wanted = SERVER_ONLY.filter((name) => !process.env[name]);
  if (wanted.length === 0) {
    return;
  }
  // `npm run dev` runs from this directory; a tool that starts the server from the repository root
  // is just as valid, so both spellings of "the root" are tried.
  for (const directory of [resolve(process.cwd(), ".."), process.cwd()]) {
    const values = readEnvFile(join(directory, ".env"), wanted);
    for (const name of wanted) {
      if (values[name] && !process.env[name]) {
        process.env[name] = values[name];
      }
    }
    if (wanted.every((name) => process.env[name])) {
      return;
    }
  }
}
