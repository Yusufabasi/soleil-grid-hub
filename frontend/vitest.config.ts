import { defineConfig } from "vitest/config";

// Node environment on purpose: the demo's tests cover the values the client computes itself —
// the proof-of-work solve, the TOTP code, the provider reference, the formatting — and those are
// plain functions. No jsdom, no component testing library: the demo scope has no UI test
// harness (demo-shell-plan.md §0).
export default defineConfig({
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
