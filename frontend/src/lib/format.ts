/**
 * Formatting, kept in one place so no screen invents its own.
 *
 * The money rule is the project's (D-03, and the fuller plan's NFR-11): an amount is integer minor
 * units plus a currency, and it is rendered by this function — never by arithmetic that produces a
 * float. `formatMoney` splits the integer and the minor units itself and lets `Intl` place the
 * currency symbol, which is why a large CDF amount never loses its centimes to a double.
 */

import type { Money } from "./types";

const LOCALE = process.env.NEXT_PUBLIC_DEFAULT_LOCALE === "fr" ? "fr-FR" : "en-US";

const EM_DASH = "—";

function moneyFormatter(currency: string): Intl.NumberFormat {
  return new Intl.NumberFormat(LOCALE, {
    style: "currency",
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/**
 * `{amount_minor: 123450, currency: "USD"}` renders as `$1,234.50` in `en`, `1 234,50 $US` in `fr`.
 *
 * The template comes from `Intl` (so the symbol, its position, the group separator and the decimal
 * separator are the locale's), and the digits are substituted in — no division, no rounding.
 */
export function formatMoney(money: Money | null | undefined, options: { code?: boolean } = {}): string {
  if (!money) {
    return EM_DASH;
  }
  const negative = money.amount_minor < 0;
  const absolute = Math.abs(money.amount_minor);
  const units = Math.trunc(absolute / 100);
  const minor = absolute % 100;
  const formatter = moneyFormatter(money.currency);
  const template = formatter.formatToParts(0);
  const fractionDigits = template.find((part) => part.type === "fraction")?.value.length ?? 2;
  let written = "";
  let wroteInteger = false;
  for (const part of template) {
    if (part.type === "integer") {
      // The template's own "0" is replaced once, by the grouped integer. Burning the flag rather
      // than the part keeps a group of digits from being written twice.
      if (!wroteInteger) {
        written += units.toLocaleString(LOCALE);
        wroteInteger = true;
      }
      continue;
    }
    if (part.type === "fraction") {
      written += String(minor).padStart(fractionDigits, "0");
      continue;
    }
    if (part.type === "minusSign") {
      continue; // the sign is written once, in front
    }
    written += part.value;
  }
  const signed = `${negative ? "-" : ""}${written}`;
  return options.code ? `${signed} ${money.currency}` : signed;
}

/**
 * A typed amount (`"51", "51.5", "51.05"`) as integer minor units, or `null` if it is not one.
 *
 * Parsed by splitting the string rather than by multiplying a float: `51.05 * 100` is
 * 5104.999999999999, and a top-up request is not the place to learn that.
 */
export function parseAmountToMinor(input: string): number | null {
  const trimmed = input.trim();
  if (!/^\d+(\.\d{1,2})?$/.test(trimmed)) {
    return null;
  }
  const [whole = "0", fraction = ""] = trimmed.split(".");
  return Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
}

/** A meter's credit in the unit an operator reads: watt-hours under a kilowatt-hour, else kWh. */
export function formatEnergyWh(wattHours: number | null | undefined): string {
  if (wattHours === null || wattHours === undefined) {
    return EM_DASH;
  }
  if (Math.abs(wattHours) < 1000) {
    return `${wattHours.toLocaleString(LOCALE)} Wh`;
  }
  const kilowattHours = Math.trunc(wattHours / 100) / 10;
  return `${kilowattHours.toLocaleString(LOCALE, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} kWh`;
}

function parse(iso: string | null | undefined): Date | null {
  if (!iso) {
    return null;
  }
  const value = new Date(iso);
  return Number.isNaN(value.getTime()) ? null : value;
}

export function formatDateTime(iso: string | null | undefined): string {
  const value = parse(iso);
  if (value === null) {
    return EM_DASH;
  }
  return value.toLocaleString(LOCALE, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatDate(iso: string | null | undefined): string {
  const value = parse(iso);
  if (value === null) {
    return EM_DASH;
  }
  return value.toLocaleDateString(LOCALE, { year: "numeric", month: "short", day: "2-digit" });
}

export function formatTime(iso: string | null | undefined): string {
  const value = parse(iso);
  if (value === null) {
    return EM_DASH;
  }
  return value.toLocaleTimeString(LOCALE, { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

/**
 * How long ago something was registered, in the units an operator thinks in.
 *
 * `last_seen_at` on a meter is a registry column, not live telemetry (demo-shell-plan.md §0), so the
 * ops view says "4 days ago" and means exactly the stored value.
 */
export function formatAge(iso: string | null | undefined, now: Date = new Date()): string {
  const value = parse(iso);
  if (value === null) {
    return "never";
  }
  const seconds = Math.round((now.getTime() - value.getTime()) / 1000);
  if (seconds < 0) {
    return "in the future";
  }
  if (seconds < 60) {
    return `${seconds}s ago`;
  }
  if (seconds < 3600) {
    return `${Math.floor(seconds / 60)}m ago`;
  }
  if (seconds < 86400) {
    return `${Math.floor(seconds / 3600)}h ago`;
  }
  return `${Math.floor(seconds / 86400)}d ago`;
}

/** A short id for a table cell: the last group of a uuid, which is what a human reads out loud. */
export function shortId(id: string | null | undefined): string {
  if (!id) {
    return EM_DASH;
  }
  return id.length > 8 ? id.slice(-8) : id;
}
