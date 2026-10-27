"use client";

/**
 * The smallest data hook that is still honest: load once, keep the error object, expose a reload.
 *
 * No cache, no dedupe, no retry policy — the demo has no data layer and does not pretend to
 * (demo-shell-plan.md §0: no SSE, plain polling). What it does have: a sequence number so a slow
 * first response cannot overwrite a newer one, and an error kept as the `ApiError` rather than a
 * string, so a screen can tell a 403 ("your role cannot read this") from a 500.
 */

import { useCallback, useEffect, useRef, useState } from "react";

export type Resource<T> = {
  data: T | null;
  error: unknown;
  loading: boolean;
  reload: () => void;
};

export function useResource<T>(load: () => Promise<T>): Resource<T> {
  const sequence = useRef(0);
  const [state, setState] = useState<{ data: T | null; error: unknown; loading: boolean }>({
    data: null,
    error: null,
    loading: true,
  });

  const run = useCallback(() => {
    const current = sequence.current + 1;
    sequence.current = current;
    setState((previous) => ({ ...previous, loading: true, error: null }));
    load()
      .then((data) => {
        if (sequence.current === current) {
          setState({ data, error: null, loading: false });
        }
      })
      .catch((error: unknown) => {
        if (sequence.current === current) {
          // The previous data is kept: a failed refresh should not blank a screen that already had
          // something true on it.
          setState((previous) => ({ data: previous.data, error, loading: false }));
        }
      });
  }, [load]);

  useEffect(() => {
    run();
  }, [run]);

  return { ...state, reload: run };
}

/**
 * Re-run `callback` every `intervalMs` while `active` is true.
 *
 * The top-up detail screen uses this to watch a `pending` top-up turn `confirmed` after the sandbox
 * completes the collection at the provider. The API's own guidance is "poll every 3 s for 2 minutes,
 * then back off" (`api/openapi.yaml`, `getTopUp`); the demo polls at 2 s and stops as soon as the
 * status is settled, which is the same idea with the sandbox's one-second delivery in mind.
 */
export function usePolling(callback: () => void, intervalMs: number, active: boolean): void {
  const latest = useRef(callback);
  latest.current = callback;

  useEffect(() => {
    if (!active) {
      return;
    }
    const timer = setInterval(() => latest.current(), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs, active]);
}
