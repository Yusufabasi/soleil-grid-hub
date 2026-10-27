import type { ReactNode } from "react";

export type Tone = "neutral" | "ok" | "warn" | "bad" | "info";

const TONES: Record<Tone, string> = {
  neutral: "border-line bg-paper text-muted",
  ok: "border-ok/30 bg-ok-soft text-ok",
  warn: "border-warn/30 bg-warn-soft text-warn",
  bad: "border-bad/30 bg-bad-soft text-bad",
  info: "border-accent/30 bg-accent-soft text-accent",
};

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-xs font-medium whitespace-nowrap ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

// The two enums the ops view and the top-up screens render, mapped to a tone once, here.
const METER_TONES: Record<string, Tone> = {
  active: "ok",
  provisioned: "info",
  offline: "warn",
  disconnected: "bad",
  decommissioned: "neutral",
};

const TOP_UP_TONES: Record<string, Tone> = {
  confirmed: "ok",
  pending: "info",
  initiated: "info",
  pending_unknown: "warn",
  late: "info",
  expired: "neutral",
  cancelled: "neutral",
  failed: "bad",
  reversed: "bad",
};

export function MeterStatus({ status }: { status: string }) {
  return <Badge tone={METER_TONES[status] ?? "neutral"}>{status.replace(/_/g, " ")}</Badge>;
}

export function TopUpStatus({ status }: { status: string }) {
  return <Badge tone={TOP_UP_TONES[status] ?? "neutral"}>{status.replace(/_/g, " ")}</Badge>;
}

/** A ledger line's direction, with the sign the API carries separately from the amount. */
export function DirectionBadge({ direction }: { direction: "debit" | "credit" }) {
  return <Badge tone={direction === "debit" ? "info" : "ok"}>{direction}</Badge>;
}
