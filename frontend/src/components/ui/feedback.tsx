import type { ReactNode } from "react";

import type { Tone } from "./badge";

const TONES: Record<Tone, string> = {
  neutral: "border-line bg-paper text-ink",
  ok: "border-ok/30 bg-ok-soft text-ink",
  info: "border-accent/30 bg-accent-soft text-ink",
  warn: "border-warn/30 bg-warn-soft text-ink",
  bad: "border-bad/30 bg-bad-soft text-ink",
};

/**
 * A short block of explanation.
 *
 * Used for three things in this demo, and it is worth saying which: what a screen is for, why a
 * screen refused (a 403 the contract really does enforce), and what the sandbox panel is about to do
 * to the provider double. Never for anything that would be a lie if it were stale — no cached
 * success messages live here.
 */
export function Callout({
  tone = "neutral",
  title,
  children,
}: {
  tone?: Tone;
  title?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div
      className={`rounded-md border px-4 py-3 text-sm ${TONES[tone]}`}
      role={tone === "bad" ? "alert" : undefined}
    >
      {title ? <p className="font-semibold">{title}</p> : null}
      <div className={title ? "mt-1 text-muted" : "text-muted"}>{children}</div>
    </div>
  );
}

/** The one loading state the demo needs: a line of text that is not a claim about anything. */
export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <p className="flex items-center gap-2 py-6 text-sm text-muted" role="status">
      <span
        aria-hidden="true"
        className="size-3.5 animate-spin rounded-full border-2 border-muted border-t-transparent"
      />
      {what}…
    </p>
  );
}

export function EmptyState({ title, children }: { title: ReactNode; children?: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-line bg-paper px-4 py-8 text-center">
      <p className="text-sm font-medium">{title}</p>
      {children ? <p className="mt-1 text-sm text-muted">{children}</p> : null}
    </div>
  );
}
