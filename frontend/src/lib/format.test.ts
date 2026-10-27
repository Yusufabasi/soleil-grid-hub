import { describe, expect, it } from "vitest";

import { formatAge, formatEnergyWh, formatMoney, parseAmountToMinor } from "./format";

/**
 * Money is integer minor units plus a currency (D-03), and this demo renders a lot of it. The cases
 * that matter are the ones a float would get wrong: an odd number of centimes on a large amount, and
 * a plan that allows an amount with no decimals at all.
 */
describe("formatMoney", () => {
  it("keeps the minor units of a large amount", () => {
    // 28,000,000.05 CDF: `28000000.05 * 100` is 2800000004.9999995 in a double.
    expect(formatMoney({ amount_minor: 2_800_000_005, currency: "CDF" }, { code: true })).toContain(
      ".05",
    );
    expect(formatMoney({ amount_minor: 123_456_789, currency: "USD" })).toContain("1,234,567.89");
  });

  it("renders small, zero and negative amounts", () => {
    expect(formatMoney({ amount_minor: 1, currency: "USD" })).toContain("0.01");
    expect(formatMoney({ amount_minor: 0, currency: "USD" })).toContain("0.00");
    // The sign goes in front of the symbol en-US renders it with; either way it is written once.
    const negative = formatMoney({ amount_minor: -250, currency: "USD" });
    expect(negative.startsWith("-")).toBe(true);
    expect(negative).toContain("$2.50");
  });

  it("says so when there is no amount rather than showing zero", () => {
    expect(formatMoney(null)).toBe("—");
    expect(formatMoney(undefined)).toBe("—");
  });
});

describe("parseAmountToMinor", () => {
  it("parses what a person types without going through a float", () => {
    expect(parseAmountToMinor("51")).toBe(5100);
    expect(parseAmountToMinor("51.5")).toBe(5150);
    expect(parseAmountToMinor("51.05")).toBe(5105);
    expect(parseAmountToMinor("0.01")).toBe(1);
    expect(parseAmountToMinor(" 12 ")).toBe(1200);
  });

  it("refuses anything that is not an amount", () => {
    expect(parseAmountToMinor("")).toBeNull();
    expect(parseAmountToMinor("5.123")).toBeNull();
    expect(parseAmountToMinor("5,10")).toBeNull();
    expect(parseAmountToMinor("-5")).toBeNull();
    expect(parseAmountToMinor("five")).toBeNull();
  });
});

describe("formatEnergyWh", () => {
  it("switches to kilowatt-hours at a thousand and keeps one decimal", () => {
    expect(formatEnergyWh(420)).toBe("420 Wh");
    expect(formatEnergyWh(42500)).toBe("42.5 kWh");
    expect(formatEnergyWh(0)).toBe("0 Wh");
    expect(formatEnergyWh(null)).toBe("—");
  });
});

describe("formatAge", () => {
  const now = new Date("2026-10-28T12:00:00Z");

  it("reports a registry timestamp as an age, not as live telemetry", () => {
    expect(formatAge("2026-10-28T11:59:10Z", now)).toBe("50s ago");
    expect(formatAge("2026-10-28T11:30:00Z", now)).toBe("30m ago");
    expect(formatAge("2026-10-28T08:00:00Z", now)).toBe("4h ago");
    expect(formatAge("2026-10-25T12:00:00Z", now)).toBe("3d ago");
  });

  it("says never rather than inventing a time", () => {
    expect(formatAge(null, now)).toBe("never");
    expect(formatAge("", now)).toBe("never");
  });
});
