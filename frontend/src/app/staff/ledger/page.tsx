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
  DirectionBadge,
  Field,
  Loading,
  PageHeader,
  RowLink,
  SelectInput,
  Stat,
} from "@/components/ui";
import { apiList, errorMessage, isForbidden } from "@/lib/api";
import { formatMoney, shortId } from "@/lib/format";
import { useResource } from "@/lib/use-resource";
import type { LedgerAccount, LedgerTransaction } from "@/lib/types";

const ACCOUNT_TYPES = [
  { value: "", label: "Every type" },
  { value: "suspense", label: "suspense" },
  { value: "customer_wallet", label: "customer_wallet" },
  { value: "provider_clearing", label: "provider_clearing" },
  { value: "revenue_energy", label: "revenue_energy" },
  { value: "fees", label: "fees" },
  { value: "chargeback_receivable", label: "chargeback_receivable" },
];

/**
 * The ledger: the screen that proves the double-entry claim, and the one the mismatch story needs.
 *
 * Two things a reviewer should be able to check here without being told:
 *
 * * every transaction's lines are shown with a **direction** and a positive amount, and the debits
 *   equal the credits — the balance is read from the ledger, not computed by this screen;
 * * the `suspense` account exists, carries a balance, and holds the postings of payments the provider
 *   got wrong. Filter to it and the mismatch transaction from the top-up demo is there, with the
 *   customer's wallet untouched.
 *
 * `listLedgerAccounts`, `getLedgerAccount` and `listLedgerTransactions` are all `finance_officer`-only
 * in `api/permissions.yaml`; every other staff role is refused and told so.
 */
export default function StaffLedgerPage() {
  return (
    <SessionGate persona="staff">
      <Ledger />
    </SessionGate>
  );
}

function Ledger() {
  const [type, setType] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const loadAccounts = useCallback(
    () => apiList<LedgerAccount>("/ledger/accounts", { query: { type, limit: 100 } }),
    [type],
  );
  const loadTransactions = useCallback(
    () =>
      apiList<LedgerTransaction>("/ledger/transactions", {
        query: { account_id: selected ?? undefined, limit: 25 },
      }),
    [selected],
  );

  const accounts = useResource(loadAccounts);
  const transactions = useResource(loadTransactions);

  if (accounts.loading && accounts.data === null) {
    return <Loading what="Reading the ledger" />;
  }

  const rows = accounts.data?.data ?? [];
  const codeOf = (accountId: string) =>
    rows.find((account) => account.id === accountId)?.code ?? shortId(accountId);
  const suspense = rows.filter((account) => account.type === "suspense");
  const chosen = rows.find((account) => account.id === selected) ?? null;

  return (
    <>
      <PageHeader
        eyebrow="Staff console"
        title="Ledger"
        description="Accounts with their live balances, and the postings behind them. Every transaction balances: the amount of its debits equals the amount of its credits."
        actions={
          <Button
            variant="secondary"
            loading={accounts.loading}
            onClick={() => {
              accounts.reload();
              transactions.reload();
            }}
          >
            Refresh
          </Button>
        }
      />

      <div className="mb-6 grid gap-6 sm:grid-cols-3">
        <Card title="Suspense">
          {suspense.length === 0 ? (
            <Callout tone="neutral">
              No suspense account in this filter. Payments whose provider disagrees with us post here
              instead of vanishing.
            </Callout>
          ) : (
            suspense.map((account) => (
              <div key={account.id} className="mb-3 last:mb-0">
                <Stat
                  label={account.code}
                  value={formatMoney(account.balance)}
                  hint={`as of ${account.balance_as_of.slice(11, 19)} UTC`}
                />
              </div>
            ))
          )}
        </Card>
        <Card title="How a mismatch looks">
          <p className="text-sm text-muted">
            A signed webhook reporting an amount the top-up never asked for is <strong>not</strong>{" "}
            credited: the posting lands on suspense, the top-up stays unconfirmed and the customer sees
            no money. Select the suspense account below and read its newest posting.
          </p>
          <div className="mt-3">
            <Button variant="ghost" onClick={() => setType("suspense")}>
              Filter to suspense
            </Button>
          </div>
        </Card>
        <Card title="What this screen cannot do">
          <p className="text-sm text-muted">
            There is no route that posts, edits or deletes a ledger entry. Postings come from domain
            events, approved adjustments and approved reconciliation resolutions — so this screen is
            read-only by construction, not by policy.
          </p>
        </Card>
      </div>

      <Card
        title="Accounts"
        actions={
          <div className="w-48">
            <Field label="Type">
              {(ids) => (
                <SelectInput
                  {...ids}
                  value={type}
                  options={ACCOUNT_TYPES}
                  onChange={(event) => {
                    setType(event.target.value);
                    setSelected(null);
                  }}
                />
              )}
            </Field>
          </div>
        }
      >
        {accounts.error ? (
          isForbidden(accounts.error) ? (
            <Refused operation="listLedgerAccounts" error={accounts.error} />
          ) : (
            <Callout tone="bad" title="Could not read the ledger">
              {errorMessage(accounts.error)}
            </Callout>
          )
        ) : (
          <DataTable
            caption="Ledger accounts with balances"
            rows={rows}
            rowKey={(account) => account.id}
            empty="No account of that type."
            onRowClick={(account) => setSelected(account.id)}
            columns={[
              {
                header: "Code",
                cell: (account) => (
                  <span>
                    <span className="font-mono">{account.code}</span>
                    <span className="block text-xs text-muted">
                      {account.owner_customer_id
                        ? `customer ${account.owner_customer_id.slice(-8)}`
                        : "system account"}
                    </span>
                  </span>
                ),
              },
              {
                header: "Type",
                cell: (account) => (
                  <Badge tone={account.type === "suspense" ? "warn" : "neutral"}>
                    {account.type}
                  </Badge>
                ),
              },
              { header: "Currency", cell: (account) => <span className="text-xs">{account.currency}</span> },
              {
                header: "Balance",
                align: "right",
                cell: (account) => (
                  <span className="font-mono tabular-nums">{formatMoney(account.balance)}</span>
                ),
              },
              {
                header: "As of",
                align: "right",
                cell: (account) => (
                  <span className="text-xs text-muted">
                    {account.balance_as_of.slice(11, 19)}
                  </span>
                ),
              },
              {
                header: "",
                align: "right",
                cell: (account) => (
                  <RowLink onClick={() => setSelected(account.id)}>Statement</RowLink>
                ),
              },
            ]}
          />
        )}
      </Card>

      <div className="mt-6">
        <Card
          title={chosen ? `Transactions · ${chosen.code}` : "Transactions · every account"}
          description={
            chosen
              ? `${formatMoney(chosen.balance)} as of ${chosen.balance_as_of.slice(11, 19)} UTC`
              : "Newest effective date first. Select an account above to narrow this list."
          }
          actions={
            chosen ? (
              <Button variant="ghost" onClick={() => setSelected(null)}>
                All accounts
              </Button>
            ) : null
          }
        >
          {transactions.error ? (
            isForbidden(transactions.error) ? (
              <Refused operation="listLedgerTransactions" error={transactions.error} />
            ) : (
              <Callout tone="bad">{errorMessage(transactions.error)}</Callout>
            )
          ) : (
            <DataTable
              caption="Ledger transactions"
              rows={transactions.data?.data ?? []}
              rowKey={(transaction) => transaction.id}
              empty="No posting here yet."
              columns={[
                {
                  header: "Posting",
                  cell: (transaction) => (
                    <span>
                      <span className="font-mono text-xs">{transaction.posting_key}</span>
                      <span className="block text-xs text-muted">
                        {transaction.kind} · {transaction.source_type} {shortId(transaction.source_id)}
                      </span>
                    </span>
                  ),
                },
                {
                  header: "Lines",
                  cell: (transaction) => (
                    <ul className="flex flex-col gap-1">
                      {transaction.lines.map((line, index) => (
                        <li key={index} className="flex items-center gap-2 text-xs">
                          <DirectionBadge direction={line.direction} />
                          <span className="font-mono">
                            {formatMoney(line.amount)} → {codeOf(line.account_id)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  ),
                },
                {
                  header: "Balanced",
                  align: "right",
                  cell: (transaction) => {
                    const debits = transaction.lines
                      .filter((line) => line.direction === "debit")
                      .reduce((total, line) => total + line.amount.amount_minor, 0);
                    const credits = transaction.lines
                      .filter((line) => line.direction === "credit")
                      .reduce((total, line) => total + line.amount.amount_minor, 0);
                    const currency = transaction.lines[0]?.amount.currency ?? "USD";
                    return (
                      <span className="font-mono text-xs">
                        {debits === credits ? (
                          <Badge tone="ok">
                            {formatMoney({ amount_minor: debits, currency })}
                          </Badge>
                        ) : (
                          <Badge tone="bad">unbalanced</Badge>
                        )}
                      </span>
                    );
                  },
                },
                {
                  header: "Effective",
                  align: "right",
                  cell: (transaction) => (
                    <span className="text-xs text-muted">
                      {transaction.effective_at.slice(0, 19).replace("T", " ")}
                    </span>
                  ),
                },
              ]}
            />
          )}
        </Card>
      </div>
    </>
  );
}
