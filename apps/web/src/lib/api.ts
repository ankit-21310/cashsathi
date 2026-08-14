import type { User } from "firebase/auth";

import { getPublicEnvironment } from "@/lib/env";

export interface Business {
  id: string;
  name: string;
  owner_user_id: string;
  created_at: string;
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
  status: "SUCCEEDED" | "FAILED" | "HUMAN_REVIEW";
  invoice_state: InvoiceState;
  model_proposal: ModelDecision | null;
  policy_result: PolicyResult | null;
  model_id: string;
  prompt_version: string;
  attempt_count: number;
  latency_ms: number;
  created_at: string;
}

export interface ProposedAction {
  id: string;
  action_type: "SEND_REMINDER";
  state: "PROPOSED" | "AWAITING_APPROVAL";
  created_at: string;
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

export async function getApiReadiness(): Promise<boolean> {
  const { apiBaseUrl } = getPublicEnvironment();
  try {
    return (await fetch(`${apiBaseUrl}/readyz`, { cache: "no-store" })).ok;
  } catch {
    return false;
  }
}
