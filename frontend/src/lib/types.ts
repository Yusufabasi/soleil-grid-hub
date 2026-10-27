/**
 * The schemas this demo renders, hand-written (demo-shell-plan.md §4: a dozen interfaces instead of
 * a generated client). Field names and nullability are copied from `api/openapi.yaml` — the
 * contract of record — and every interface names the schema it mirrors so a reader can check it.
 *
 * Only the operations of §2 are modelled. Anything the contract declares for a later slice is
 * absent on purpose: the backend does not route it yet.
 */

/** `Money`. Integer minor units and a currency code: never a float, never a formatted string. */
export type Money = {
  amount_minor: number;
  currency: "USD" | "CDF";
};

export type Locale = "en" | "fr";

/** `Principal`. Roles are a list of strings; the demo's `can()` reads them. */
export type Principal = {
  id: string;
  type: "staff" | "customer" | "integrator" | "gateway";
  display_name: string;
  roles: string[];
  attributes: string[];
  scopes: string[];
  site_ids: string[];
  locale: Locale;
  mfa_enrolled: boolean;
  acr: string;
  auth_time: string;
};

/** `TokenPair`. `refresh_token` is absent for browsers: the cookie carries it. */
export type TokenPair = {
  access_token: string;
  token_type: "Bearer";
  expires_in: number;
  principal: Principal;
};

/** `MfaChallenge`: step one of a staff login. */
export type MfaChallenge = {
  mfa_required: boolean;
  mfa_token: string;
  mfa_token_expires_in: number;
  enrollment_required: boolean;
  methods: string[];
};

/** `ProofChallenge`: the hashcash puzzle in front of the one-time-code request (D-21). */
export type ProofChallenge = {
  kind: "pow" | "captcha";
  challenge: string;
  difficulty: number;
  expires_at: string;
};

/** `OtpChallenge`: always answered, whether or not the number is registered. */
export type OtpChallenge = {
  challenge_id: string;
  expires_in: number;
  resend_after: number;
};

/** `Customer`. */
export type Customer = {
  id: string;
  customer_number: string;
  full_name: string;
  phone_e164: string;
  email: string | null;
  locale: Locale;
  status: "active" | "suspended" | "closed" | "erased";
  site_id: string;
  plan_id: string;
  created_at: string;
  updated_at: string;
  version: number;
};

/** `CustomerPseudonymous`: what an operations engineer sees instead of the real record. */
export type CustomerPseudonymous = {
  id: string;
  pseudonym: string;
  status: string;
  site_id: string;
  plan_id: string;
  created_at: string;
  updated_at: string;
};

/** `Wallet`. `ledger_account_id` is null until the first top-up opens the wallet account. */
export type Wallet = {
  customer_id: string;
  balance: Money;
  as_of: string;
  pending_top_ups: number;
  ledger_account_id: string | null;
};

/** `Site`. `health` is computed from gateway heartbeat and meter silence, never written. */
export type Site = {
  id: string;
  code: string;
  name: string;
  timezone: string;
  latitude: number | null;
  longitude: number | null;
  capacity_kw: number | null;
  status: "planned" | "active" | "decommissioned";
  health: "operational" | "degraded" | "offline";
  meter_count: number;
  created_at: string;
  updated_at: string;
};

/** `Tariff`. */
export type Tariff = {
  id: string;
  plan_id: string;
  price_per_kwh: Money;
  daily_fixed_fee: Money | null;
  effective_from: string;
  status: "proposed" | "active" | "superseded" | "rejected";
  approval_id: string | null;
  created_at: string;
};

/** `Plan`, with the current tariff when one is effective. */
export type Plan = {
  id: string;
  code: string;
  name: string;
  currency: "USD" | "CDF";
  min_top_up: Money;
  max_top_up: Money;
  status: "draft" | "active" | "retired";
  current_tariff?: Tariff | null;
  created_at: string;
  updated_at: string;
};

/** `Meter`. `credit_remaining_wh` and `last_seen_at` are registry columns, not live telemetry. */
export type Meter = {
  id: string;
  serial_number: string;
  customer_id: string | null;
  site_id: string;
  hardware_model: string;
  status: "provisioned" | "active" | "offline" | "disconnected" | "decommissioned";
  firmware_version: string | null;
  credit_remaining_wh: number | null;
  last_seen_at: string | null;
  last_reading_at: string | null;
  installed_at: string | null;
  created_at: string;
  updated_at: string;
  version: number;
  gateway_id: string | null;
};

export type TopUpStatus =
  | "initiated"
  | "pending"
  | "pending_unknown"
  | "confirmed"
  | "failed"
  | "expired"
  | "cancelled"
  | "reversed";

/** `TopUp`. `energy_wh` is the credited amount and stays null until a confirmation is recorded. */
export type TopUp = {
  id: string;
  customer_id: string;
  meter_id: string;
  amount: Money;
  fee: Money;
  energy_wh: number | null;
  provider: "mpesa_cd" | "airtel_cd" | "orange_cd" | "synthetic";
  provider_reference: string | null;
  payer_msisdn_masked: string;
  status: TopUpStatus;
  failure_code: string | null;
  token_id: string | null;
  ledger_transaction_id: string | null;
  reversal_transaction_id?: string | null;
  created_at: string;
  confirmed_at: string | null;
  expires_at: string;
  late: boolean;
  quoted_energy_wh?: number | null;
  quoted_tariff?: Money | null;
};

/** `LedgerAccount`, with its balance as of the read. */
export type LedgerAccount = {
  id: string;
  code: string;
  type:
    | "customer_wallet"
    | "provider_clearing"
    | "revenue_energy"
    | "fees"
    | "suspense"
    | "chargeback_receivable";
  currency: "USD" | "CDF";
  owner_customer_id: string | null;
  balance: Money;
  balance_as_of: string;
  created_at: string;
};

/** `LedgerLine`. Amounts are always positive; `direction` carries the sign (D-03). */
export type LedgerLine = {
  account_id: string;
  direction: "debit" | "credit";
  amount: Money;
};

/** `LedgerTransaction`: `sum(debits) === sum(credits)` per currency. */
export type LedgerTransaction = {
  id: string;
  posting_key: string;
  kind:
    | "topup_received"
    | "energy_purchase"
    | "fee"
    | "refund"
    | "adjustment"
    | "reconciliation_fix"
    | "reversal";
  lines: LedgerLine[];
  linesTotal?: Money;
  source_type: string;
  source_id: string | null;
  description: string | null;
  reverses_transaction_id: string | null;
  effective_at: string;
};

/** `PageMeta`: cursor pagination. No total count is returned. */
export type PageMeta = {
  has_next: boolean;
  next_cursor: string | null;
  limit: number;
};

/** `FieldError`, inside a `Problem`. */
export type FieldError = {
  field: string;
  code: string;
  message: string;
};

/** `Problem`: RFC 9457 problem details, served as application/problem+json. */
export type Problem = {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  instance?: string;
  code?: string;
  request_id?: string;
  errors?: FieldError[];
  retry_after_seconds?: number;
};
