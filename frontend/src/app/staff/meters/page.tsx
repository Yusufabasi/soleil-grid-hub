"use client";

import { useCallback, useState } from "react";

import { Refused } from "@/components/refused";
import { SessionGate } from "@/components/session-gate";
import {
  Badge,
  Button,
  Callout,
  Card,
  DataTable,
  Field,
  Loading,
  MeterStatus,
  PageHeader,
  SelectInput,
  TextInput,
} from "@/components/ui";
import { apiList, errorMessage, isForbidden } from "@/lib/api";
import { formatAge, formatEnergyWh } from "@/lib/format";
import { useResource } from "@/lib/use-resource";
import type { Meter, Site } from "@/lib/types";

const STATUS_OPTIONS = [
  { value: "", label: "Every status" },
  { value: "active", label: "active" },
  { value: "provisioned", label: "provisioned" },
  { value: "offline", label: "offline" },
  { value: "disconnected", label: "disconnected" },
  { value: "decommissioned", label: "decommissioned" },
];

/**
 * The ops view: every meter across every site, as the registry stores it.
 *
 * `status` and `last_seen_at` here are **columns of `hub.meters`**, not live telemetry — meter
 * telemetry is slice 08 and is not built (§0 of the plan), so the screen says "last seen" and means
 * the stored value. `credit_remaining_wh` is the same kind of fact: whatever the registry last
 * recorded, quite possibly null.
 *
 * `finance_officer` is not granted `listMeters` by the contract, so this screen refuses for that role
 * and names the one that has it.
 */
export default function StaffMetersPage() {
  return (
    <SessionGate persona="staff">
      <OpsView />
    </SessionGate>
  );
}

function OpsView() {
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [term, setTerm] = useState("");

  const loadMeters = useCallback(
    () => apiList<Meter>("/meters", { query: { status, q: term, limit: 50 } }),
    [status, term],
  );
  const loadSites = useCallback(() => apiList<Site>("/sites", { query: { limit: 25 } }), []);
  const meters = useResource(loadMeters);
  const sites = useResource(loadSites);

  if (meters.loading && meters.data === null) {
    return <Loading what="Loading the registry" />;
  }

  const rows = meters.data?.data ?? [];
  const byStatus = rows.reduce<Record<string, number>>((totals, meter) => {
    totals[meter.status] = (totals[meter.status] ?? 0) + 1;
    return totals;
  }, {});

  return (
    <>
      <PageHeader
        eyebrow="Staff console"
        title="Meters & sites"
        description="The registry as stored: status, firmware and the last time each meter was seen. No live telemetry — that slice is not built, and this screen does not imply otherwise."
        actions={
          <Button variant="secondary" loading={meters.loading} onClick={meters.reload}>
            Refresh
          </Button>
        }
      />

      <div className="grid gap-6 lg:grid-cols-[3fr_2fr]">
        <Card
          title="Meters"
          description={`${rows.length} shown${meters.data?.meta.has_next ? " (more pages available)" : ""}`}
        >
          <form
            className="mb-4 flex flex-wrap items-end gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              setTerm(query.trim());
            }}
          >
            <div className="w-full max-w-[12rem]">
              <Field label="Status">
                {(ids) => (
                  <SelectInput
                    {...ids}
                    value={status}
                    options={STATUS_OPTIONS}
                    onChange={(event) => setStatus(event.target.value)}
                  />
                )}
              </Field>
            </div>
            <div className="w-full max-w-[16rem]">
              <Field label="Search">
                {(ids) => (
                  <TextInput
                    {...ids}
                    value={query}
                    placeholder="serial number"
                    onChange={(event) => setQuery(event.target.value)}
                  />
                )}
              </Field>
            </div>
            <Button variant="secondary" type="submit">
              Apply
            </Button>
          </form>

          {Object.keys(byStatus).length > 0 ? (
            <div className="mb-4 flex flex-wrap gap-2">
              {Object.entries(byStatus).map(([name, count]) => (
                <span key={name} className="flex items-center gap-1 text-xs text-muted">
                  <MeterStatus status={name} />
                  {count}
                </span>
              ))}
            </div>
          ) : null}

          {meters.error ? (
            isForbidden(meters.error) ? (
              <Refused operation="listMeters" error={meters.error} />
            ) : (
              <Callout tone="bad" title="Could not read the registry">
                {errorMessage(meters.error)}
              </Callout>
            )
          ) : (
            <DataTable
              caption="Meters across every site"
              rows={rows}
              rowKey={(meter) => meter.id}
              empty="No meter matches that filter."
              columns={[
                {
                  header: "Serial",
                  cell: (meter) => (
                    <span>
                      <span className="font-mono">{meter.serial_number}</span>
                      <span className="block font-mono text-xs text-muted">
                        {meter.firmware_version ? `fw ${meter.firmware_version}` : "no firmware"}
                      </span>
                    </span>
                  ),
                },
                {
                  header: "Site",
                  cell: (meter) => (
                    <span className="text-xs">
                      {sites.data?.data.find((site) => site.id === meter.site_id)?.code ?? "—"}
                    </span>
                  ),
                },
                { header: "Status", cell: (meter) => <MeterStatus status={meter.status} /> },
                {
                  header: "Customer",
                  cell: (meter) => (
                    <span className="font-mono text-xs">
                      {meter.customer_id ? meter.customer_id.slice(-8) : "unassigned"}
                    </span>
                  ),
                },
                {
                  header: "Last seen",
                  cell: (meter) => (
                    <span className="text-xs text-muted">{formatAge(meter.last_seen_at)}</span>
                  ),
                },
                {
                  header: "Credit",
                  align: "right",
                  cell: (meter) => (
                    <span className="font-mono text-xs">
                      {formatEnergyWh(meter.credit_remaining_wh)}
                    </span>
                  ),
                },
              ]}
            />
          )}
        </Card>

        <div className="flex flex-col gap-6">
          <Card title="Sites">
            {sites.error ? (
              <Callout tone="bad">{errorMessage(sites.error)}</Callout>
            ) : (
              <ul className="flex flex-col gap-3">
                {(sites.data?.data ?? []).map((site) => (
                  <li key={site.id} className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-medium">
                        {site.name} <span className="font-mono text-xs text-muted">{site.code}</span>
                      </p>
                      <p className="text-xs text-muted">
                        {site.timezone} · {site.meter_count} meters
                      </p>
                    </div>
                    <div className="flex flex-col items-end gap-1">
                      <Badge tone={site.status === "active" ? "ok" : "neutral"}>{site.status}</Badge>
                      <Badge
                        tone={
                          site.health === "operational"
                            ? "ok"
                            : site.health === "degraded"
                              ? "warn"
                              : "bad"
                        }
                      >
                        {site.health}
                      </Badge>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-4 text-xs text-muted">
              A site&apos;s health is computed from gateway heartbeats and meter silence, never written;
              with no telemetry slice built, it reads as operational.
            </p>
          </Card>

          <Callout tone="neutral" title="Why there are no edit buttons">
            `createMeter`, `updateMeter` and `decommissionMeter` are declared in the contract for later
            slices and are not routed in the backend yet, so this view is read-only on purpose.
          </Callout>
        </div>
      </div>
    </>
  );
}
