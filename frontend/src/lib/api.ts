/**
 * The one way this app talks to the API (demo-shell-plan.md §4).
 *
 * Four jobs, and no more:
 *
 * 1. read `NEXT_PUBLIC_API_BASE` (a relative path, so there is no CORS configuration anywhere and
 *    the same value works under `next dev` through the rewrite and behind the balancer);
 * 2. attach `Authorization: Bearer <token>` from the in-memory store;
 * 3. unwrap the `{data: ...}` envelope, keeping `meta` for the list endpoints;
 * 4. turn an error into an `ApiError` carrying the RFC 9457 problem document, so a screen can show
 *    `detail` (and the machine-readable `code`) instead of "something went wrong".
 *
 * Token lifecycle is exactly one thing: a 401 triggers one silent `POST /auth/refresh`, retried
 * once. There is no proactive timer — an access token lives ten minutes, a page render is a
 * request, and the cookie is the only thing that has to survive. A refresh is only attempted when
 * this client actually held a token (a 401 while signed out is just a 401).
 */

import { clearSession, getAccessToken, setSession } from "./token-store";
import type { FieldError, PageMeta, Problem, TokenPair } from "./types";

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

/** An error status from the API, with everything the problem document told us. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId: string | null;
  readonly fieldErrors: FieldError[];
  readonly retryAfterSeconds: number | null;

  constructor(status: number, problem: Problem) {
    super(problem.detail || problem.title || `Request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.code = problem.code ?? "unknown";
    this.requestId = problem.request_id ?? null;
    this.fieldErrors = problem.errors ?? [];
    this.retryAfterSeconds = problem.retry_after_seconds ?? null;
  }
}

type Query = Record<string, string | number | boolean | undefined | null>;

export type RequestOptions = {
  method?: "GET" | "POST" | "PATCH" | "DELETE";
  body?: unknown;
  query?: Query;
  headers?: Record<string, string>;
  /** `false` for the public endpoints (`getProofChallenge`, `requestCustomerOtp`). */
  auth?: boolean;
};

function url(path: string, query?: Query): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== null && value !== "") {
      search.append(key, String(value));
    }
  }
  const suffix = search.toString();
  return `${API_BASE}${path}${suffix ? `?${suffix}` : ""}`;
}

async function send(path: string, options: RequestOptions = {}): Promise<Response> {
  const token = options.auth === false ? null : getAccessToken();
  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return fetch(url(path, options.query), {
    method: options.method ?? "GET",
    headers,
    // The refresh cookie is httpOnly and scoped to /api/v1/auth (D-17): the browser sends it on its
    // own, and only to that path.
    credentials: "same-origin",
    body: options.body === undefined ? undefined : JSON.stringify(options.body),
    cache: "no-store",
  });
}

let refreshInFlight: Promise<boolean> | null = null;

/** One refresh at a time: five screens that hit a 401 should cause one request, not five. */
function refreshOnce(): Promise<boolean> {
  if (refreshInFlight) {
    return refreshInFlight;
  }
  refreshInFlight = (async () => {
    try {
      const response = await send("/auth/refresh", { method: "POST", body: {}, auth: false });
      if (!response.ok) {
        return false;
      }
      const document = (await response.json()) as { data: TokenPair };
      setSession(document.data.access_token, document.data.principal);
      return true;
    } catch {
      return false;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

async function problemOf(response: Response): Promise<Problem> {
  try {
    const parsed = (await response.json()) as Problem;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    // A proxy error, a 502 from the balancer or an empty body: still an error, just not a documented
    // one. The status is what the caller gets to branch on.
    return { status: response.status, title: response.statusText };
  }
}

export type Envelope<T> = { data: T; meta?: PageMeta };

/** One request, with the silent-refresh retry. Returns the parsed envelope. */
export async function request<T>(path: string, options: RequestOptions = {}): Promise<Envelope<T>> {
  const hadToken = options.auth === false ? null : getAccessToken();
  let response = await send(path, options);

  if (response.status === 401 && hadToken) {
    const refreshed = await refreshOnce();
    if (refreshed) {
      response = await send(path, options);
    } else {
      // The refresh cookie is gone or revoked: the session is over, and a screen showing "signed
      // out" is more honest than one retrying forever.
      clearSession();
    }
  }

  if (!response.ok) {
    throw new ApiError(response.status, await problemOf(response));
  }
  if (response.status === 204) {
    return { data: undefined as T };
  }
  return (await response.json()) as Envelope<T>;
}

/** The document of a single-resource operation. */
export async function apiGet<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return (await request<T>(path, options)).data;
}

/** A list operation: the rows and the cursor metadata together. */
export async function apiList<T>(
  path: string,
  options: RequestOptions = {},
): Promise<{ data: T[]; meta: PageMeta }> {
  const envelope = await request<T[]>(path, options);
  return {
    data: envelope.data ?? [],
    meta: envelope.meta ?? { has_next: false, next_cursor: null, limit: 0 },
  };
}

export async function apiPost<T>(
  path: string,
  body?: unknown,
  options: RequestOptions = {},
): Promise<T> {
  return apiGet<T>(path, { ...options, method: "POST", body });
}

/**
 * Whether the API refused this caller by role (403).
 *
 * The staff console uses it to tell "you may not read this" from "this is broken": the contract's
 * matrix is real and the demo says so out loud rather than hiding the section.
 */
export function isForbidden(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 403;
}

/** Whether the API answered 404: a row that does not exist, or one this caller cannot see. */
export function isMissing(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 404;
}

/** A message for a screen: the server's own detail when it sent one. */
export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const fields = error.fieldErrors
      .map((field) => `${field.field}: ${field.message}`)
      .join("; ");
    return fields ? `${error.message} (${fields})` : error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return "Unexpected error.";
}
