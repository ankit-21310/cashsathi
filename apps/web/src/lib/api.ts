import type { User } from "firebase/auth";

import { getPublicEnvironment } from "@/lib/env";

export interface Business {
  id: string;
  name: string;
  owner_user_id: string;
  created_at: string;
  data_classification: "UNCLASSIFIED" | "DEMO" | "REAL";
  relationship: "UNCLASSIFIED" | "ARMS_LENGTH" | "RELATED" | "PREEXISTING";
  evidence_pseudonym: string;
}

export interface Membership {
  business_id: string;
  user_id: string;
  role: "OWNER";
  status: "ACTIVE";
  created_at: string;
}

export interface MeResponse {
  uid: string;
  email: string | null;
  display_name: string | null;
  business: Business | null;
  membership: Membership | null;
  is_platform_admin: boolean;
}

export type InvoiceState =
  | "UPCOMING"
  | "DUE"
  | "OVERDUE"
  | "WAITING_FOR_REPLY"
  | "DISPUTED"
  | "HUMAN_REVIEW"
  | "PAID";

export interface ConsentStatus {
  consent_type: "product_processing";
  version: string;
  statement: string;
  granted: boolean;
  granted_at: string | null;
}

export interface ExtractionWarning {
  code: string;
  field: string | null;
  message: string;
}

export interface ExtractedInvoiceDraft {
  invoice_number: string | null;
  customer_name: string | null;
  customer_email: string | null;
  amount_decimal: string | null;
  currency: string | null;
  issue_date: string | null;
  due_date: string | null;
  payment_terms: string | null;
  confidence: Record<string, "HIGH" | "MEDIUM" | "LOW">;
  warnings: ExtractionWarning[];
}

export interface ExtractionResult {
  extraction_id: string;
  draft: ExtractedInvoiceDraft;
  model_id: string;
  prompt_version: string;
  latency_ms: number;
}

export interface CustomerSnapshot {
  id: string;
  name: string;
  email: string | null;
  manual_only: boolean;
}

export interface Invoice {
  id: string;
  business_id: string;
  extraction_id: string;
  invoice_number: string;
  customer: CustomerSnapshot;
  amount_minor: number;
  currency: string;
  issue_date: string | null;
  due_date: string | null;
  payment_terms: string | null;
  review_required: boolean;
  review_reason: string | null;
  created_at: string;
  updated_at: string;
  verified_paid_minor: number;
  next_check_at: string | null;
  workflow_status: "OPEN" | "PAUSED" | "CLOSED";
  active_action_id: string | null;
}

export interface InvoiceSummary {
  id: string;
  invoice_number: string;
  customer_name: string;
  amount_minor: number;
  currency: string;
  due_date: string | null;
  current_state: InvoiceState;
  created_at: string;
}

export interface InvoicePage {
  items: InvoiceSummary[];
  next_cursor: string | null;
}

export interface ModelDecision {
  decision: string;
  rationale: string;
  risk_flags: string[];
  requires_human_approval: boolean;
  next_check_at: string | null;
}

export interface PolicyResult {
  outcome: "ALLOW" | "REQUIRE_APPROVAL" | "BLOCK";
  final_decision: string;
  matched_rules: string[];
  requires_approval: boolean;
  next_check_at: string | null;
  policy_version: string;
}

export interface AgentRun {
  id: string;
  invoice_id: string;
  business_id: string;
  status: "SUCCEEDED" | "FAILED" | "HUMAN_REVIEW";
  invoice_state: InvoiceState;
  model_proposal: ModelDecision | null;
  policy_result: PolicyResult | null;
  model_id: string;
  prompt_version: string;
  attempt_count: number;
  latency_ms: number;
  failure_code: string | null;
  created_at: string;
  action_id: string | null;
}

export interface ProposedAction {
  id: string;
  invoice_id: string;
  agent_run_id: string;
  action_type: "SEND_REMINDER";
  state:
    | "PROPOSED"
    | "AWAITING_APPROVAL"
    | "EXECUTING"
    | "SUCCEEDED"
    | "FAILED"
    | "UNKNOWN"
    | "CANCELLED";
  created_at: string;
  recipient_email: string | null;
  subject: string | null;
  body: string | null;
  automatic: boolean;
  approved_at: string | null;
  provider_message_id: string | null;
  failure_code: string | null;
  failure_message: string | null;
  delivery_possible: boolean | null;
  resolution: "CONFIRMED_DELIVERED" | "CONFIRMED_NOT_DELIVERED" | "MANUALLY_SENT" | null;
  attempt_count: number;
}

export interface InvoiceDetail {
  invoice: Invoice;
  current_state: InvoiceState;
  latest_agent_run: AgentRun | null;
  latest_action: ProposedAction | null;
}

export interface EvaluationResult {
  agent_run: AgentRun;
  action: ProposedAction | null;
}

export interface AgentRunPage {
  items: AgentRun[];
  next_cursor: string | null;
}

export interface ActionPage {
  items: ProposedAction[];
  next_cursor: string | null;
}

export interface GmailStatus {
  connected: boolean;
  connected_at: string | null;
  last_error_code: string | null;
  automation_enabled: boolean;
}

export interface PolicySettings {
  reminder_cooldown_hours: number;
  high_value_threshold_minor: number;
  high_value_currency: string;
  automation_enabled: boolean;
}

export interface CurrencyMetrics {
  currency: string;
  monitored_minor: number;
  outstanding_minor: number;
  overdue_minor: number;
  verified_paid_minor: number;
  post_action_paid_minor: number;
}

export interface MetricsResponse {
  currencies: CurrencyMetrics[];
  invoice_count: number;
  overdue_count: number;
  human_review_count: number;
  ai_decisions: number;
  successful_actions: number;
  automatic_successful_actions: number;
  automation_rate: number;
  average_days_overdue: number;
  pending_approvals: number;
}

export interface TimelineEntry {
  id: string;
  entry_type: string;
  title: string;
  detail: string;
  occurred_at: string;
  status: string | null;
}

export interface InvoiceTimeline {
  items: TimelineEntry[];
}

export interface Payment {
  id: string;
  invoice_id: string;
  amount_minor: number;
  currency: string;
  paid_at: string;
  reference: string;
  created_at: string;
}

export interface EvidenceLedgerEntry {
  id: string;
  kind: "PRODUCT_REVENUE" | "EXPENSE";
  amount_minor: number;
  currency: string;
  occurred_on: string;
  category: string;
  reference: string;
  business_id: string | null;
  marketing: boolean;
  reversal_of: string | null;
  created_at: string;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export type OptionalConsentType =
  | "ANONYMIZED_METRICS"
  | "TESTIMONIAL"
  | "IDENTITY_DISCLOSURE";

export interface OptionalConsentEvent {
  id: string;
  consent_type: OptionalConsentType;
  action: "GRANTED" | "WITHDRAWN";
  version: string;
  approved_text: string | null;
  channels: string[];
  withdraws_grant_id: string | null;
  occurred_at: string;
}

export interface OptionalConsentDefinition {
  consent_type: OptionalConsentType;
  version: string;
  statement: string;
  active_grant: OptionalConsentEvent | null;
  history: OptionalConsentEvent[];
}

export interface OptionalConsentResponse {
  items: OptionalConsentDefinition[];
}

export interface FounderPlan {
  id: string;
  business_id: string;
  plan_version: "FOUNDER_RECOVERY_2026_V1";
  status: "ACTIVE" | "EXHAUSTED";
  price_minor: 29900;
  currency: "INR";
  invoice_limit: 10;
  invoices_used: number;
  ledger_entry_id: string;
  receipt_reference: string;
  activated_at: string;
}

export type ProspectStatus =
  | "NOT_CONTACTED"
  | "CONTACTED"
  | "INTERVIEW_SCHEDULED"
  | "INTERVIEWED"
  | "DESIGN_PARTNER"
  | "CONVERTED"
  | "DECLINED"
  | "DO_NOT_CONTACT";

export interface Prospect {
  id: string;
  company: string;
  city: string | null;
  segment: string;
  public_website: string | null;
  public_contact_channel: string;
  status: ProspectStatus;
  notes: string | null;
  next_follow_up_on: string | null;
  linked_business_id: string | null;
  updated_at: string;
}

export interface Interview {
  id: string;
  prospect_id: string;
  occurred_on: string;
  top_pain: string;
  trust_boundary: string;
  willingness_to_pay: "NONE" | "MAYBE" | "YES";
  feedback: string;
  follow_up_on: string | null;
}

export interface AdminImpact {
  paying_businesses: number;
  related_paying_businesses: number;
  preexisting_paying_businesses: number;
  real_businesses: number;
  demo_businesses: number;
  unclassified_businesses: number;
  revenue_by_currency: Record<string, number>;
  related_revenue_by_currency: Record<string, number>;
  preexisting_revenue_by_currency: Record<string, number>;
  expenses_by_currency: Record<string, number>;
  marketing_spend_by_currency: Record<string, number>;
  cac_by_currency: Record<string, number>;
  prospects: number;
  interviews: number;
  design_partners: number;
  converted_prospects: number;
  active_founder_plans: number;
  exhausted_founder_plans: number;
  consented_testimonials: number;
  operational: MetricsResponse;
}

interface ErrorEnvelope {
  error: { code: string; message: string; request_id: string; details?: unknown };
}

export class ApiClientError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code: string,
    public readonly requestId: string | null,
  ) {
    super(message);
    this.name = "ApiClientError";
  }
}

async function request(
  user: User,
  path: string,
  init: RequestInit,
  forceRefresh = false,
): Promise<Response> {
  const environment = getPublicEnvironment();
  const token = await user.getIdToken(forceRefresh);
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${token}`);
  if (!(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  headers.set("X-Request-ID", crypto.randomUUID());
  return fetch(`${environment.apiBaseUrl}${path}`, { ...init, headers, cache: "no-store" });
}

export async function apiFetch<T>(user: User, path: string, init: RequestInit = {}): Promise<T> {
  let response = await request(user, path, init);
  if (response.status === 401) response = await request(user, path, init, true);

  if (!response.ok) {
    let envelope: ErrorEnvelope | null = null;
    try {
      envelope = (await response.json()) as ErrorEnvelope;
    } catch {
      // A platform/proxy error may not use the API error envelope.
    }
    throw new ApiClientError(
      envelope?.error.message ?? "The service could not complete the request.",
      response.status,
      envelope?.error.code ?? "request_failed",
      envelope?.error.request_id ?? response.headers.get("X-Request-ID"),
    );
  }
  return (await response.json()) as T;
}

export async function apiDownload(user: User, path: string): Promise<{ blob: Blob; filename: string }> {
  let response = await request(user, path, {});
  if (response.status === 401) response = await request(user, path, {}, true);
  if (!response.ok) {
    let envelope: ErrorEnvelope | null = null;
    try {
      envelope = (await response.json()) as ErrorEnvelope;
    } catch {
      // A platform/proxy error may not use the API error envelope.
    }
    throw new ApiClientError(
      envelope?.error.message ?? "The export could not be generated.",
      response.status,
      envelope?.error.code ?? "request_failed",
      envelope?.error.request_id ?? response.headers.get("X-Request-ID"),
    );
  }
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const filename = disposition.match(/filename="?([^";]+)"?/)?.[1] ?? "cashsathi-export";
  return { blob: await response.blob(), filename };
}

export async function getApiReadiness(): Promise<boolean> {
  const { apiBaseUrl } = getPublicEnvironment();
  try {
    return (await fetch(`${apiBaseUrl}/readyz`, { cache: "no-store" })).ok;
  } catch {
    return false;
  }
}
