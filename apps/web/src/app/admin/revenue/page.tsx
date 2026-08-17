"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import {
  apiDownload,
  apiFetch,
  BillingTransaction,
  MeResponse,
  Page,
  RevenueCustomer,
  RevenuePaymentDetail,
  RevenueSummary,
} from "@/lib/api";
import { formatMinorAmount, humanize } from "@/lib/format";

interface Filters {
  q: string;
  from_date: string;
  to_date: string;
  status: string;
  payment_method: string;
  data_classification: string;
  relationship: string;
}

const emptyFilters: Filters = {
  q: "",
  from_date: "",
  to_date: "",
  status: "",
  payment_method: "",
  data_classification: "",
  relationship: "",
};

function queryString(filters: Filters) {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  const query = params.toString();
  return query ? `?${query}` : "";
}

function maskIdentifier(value: string | null | undefined) {
  if (!value) return "Not available";
  if (value.length <= 8) return `${value.slice(0, 2)}••••${value.slice(-2)}`;
  return `${value.slice(0, 6)}••••${value.slice(-4)}`;
}

export default function RevenueCommandPage() {
  const { user } = useAuth();
  const router = useRouter();
  const [authorized, setAuthorized] = useState(false);
  const [filters, setFilters] = useState<Filters>(emptyFilters);
  const [draftFilters, setDraftFilters] = useState<Filters>(emptyFilters);
  const [summary, setSummary] = useState<RevenueSummary | null>(null);
  const [customers, setCustomers] = useState<RevenueCustomer[]>([]);
  const [payments, setPayments] = useState<BillingTransaction[]>([]);
  const [detail, setDetail] = useState<RevenuePaymentDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!user || !authorized) return;
    try {
      const filterQuery = queryString(filters);
      const [summaryResult, customerPage, paymentPage] = await Promise.all([
        apiFetch<RevenueSummary>(user, `/api/admin/revenue/summary${filterQuery}`),
        apiFetch<Page<RevenueCustomer>>(user, `/api/admin/revenue/customers${filterQuery}`),
        apiFetch<Page<BillingTransaction>>(user, `/api/admin/revenue/payments${filterQuery ? `${filterQuery}&limit=20` : "?limit=20"}`),
      ]);
      setSummary(summaryResult);
      setCustomers(customerPage.items);
      setPayments(paymentPage.items);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Revenue data is unavailable.");
    }
  }, [authorized, filters, user]);

  useEffect(() => {
    if (!user) return;
    apiFetch<MeResponse>(user, "/api/me")
      .then((account) => {
        if (!account.is_platform_admin) router.replace("/dashboard");
        else setAuthorized(true);
      })
      .catch(() => router.replace("/dashboard"));
  }, [router, user]);

  useEffect(() => {
    const pending = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(pending);
  }, [load]);

  async function showPayment(paymentId: string) {
    if (!user) return;
    try {
      setDetail(await apiFetch<RevenuePaymentDetail>(user, `/api/admin/revenue/payments/${paymentId}`));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Payment detail is unavailable.");
    }
  }

  async function downloadCsv() {
    if (!user) return;
    const { blob, filename } = await apiDownload(user, `/api/admin/revenue/export.csv${queryString(filters)}`);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    link.click();
    URL.revokeObjectURL(url);
  }

  function applyFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFilters(draftFilters);
  }

  if (!authorized) {
    return <AuthenticatedShell><main className="centered-state">Checking company access…</main></AuthenticatedShell>;
  }

  const money = (amount: number) => formatMinorAmount(amount, summary?.currency ?? "INR");

  return (
    <AuthenticatedShell>
      <main className="revenue-command">
        <header className="revenue-command-header">
          <div><div className="eyebrow">Private company workspace</div><h1>Revenue Command Center</h1><p>Captured CashSathi plan payments, refunds, customers, and entitlement health.</p></div>
          <div className="button-row"><Link className="button revenue-button-secondary" href="/admin/impact">Impact evidence</Link><button className="button revenue-button-primary" onClick={() => void downloadCsv()}>Export CSV</button></div>
        </header>
        {error && <div className="alert alert-error">{error}</div>}
        <section className="revenue-kpis" aria-label="Revenue metrics">
          <article><span>Gross captured</span><strong>{money(summary?.gross_captured_minor ?? 0)}</strong><small>{summary?.paying_businesses ?? 0} paying businesses</small></article>
          <article><span>Net captured</span><strong>{money(summary?.net_captured_minor ?? 0)}</strong><small>After {money(summary?.refunded_minor ?? 0)} refunds</small></article>
          <article><span>Provider deductions</span><strong>{money((summary?.provider_fees_minor ?? 0) + (summary?.provider_tax_minor ?? 0))}</strong><small>Reported fee and tax</small></article>
          <article><span>Capture rate</span><strong>{Math.round((summary?.capture_rate ?? 0) * 100)}%</strong><small>Across recorded attempts</small></article>
          <article><span>Plan health</span><strong>{summary?.active_plans ?? 0} active</strong><small>{summary?.exhausted_plans ?? 0} exhausted · {summary?.refunded_plans ?? 0} refunded</small></article>
        </section>
        <form className="revenue-filters" onSubmit={applyFilters}>
          <label>Search<input value={draftFilters.q} placeholder="Business, email, payment ID" onChange={(event) => setDraftFilters({ ...draftFilters, q: event.target.value })} /></label>
          <label>From<input type="date" value={draftFilters.from_date} onChange={(event) => setDraftFilters({ ...draftFilters, from_date: event.target.value })} /></label>
          <label>To<input type="date" value={draftFilters.to_date} onChange={(event) => setDraftFilters({ ...draftFilters, to_date: event.target.value })} /></label>
          <label>Status<select value={draftFilters.status} onChange={(event) => setDraftFilters({ ...draftFilters, status: event.target.value })}><option value="">All statuses</option><option>CAPTURED</option><option>FAILED</option><option>PARTIALLY_REFUNDED</option><option>REFUNDED</option></select></label>
          <label>Method<select value={draftFilters.payment_method} onChange={(event) => setDraftFilters({ ...draftFilters, payment_method: event.target.value })}><option value="">All methods</option><option value="upi">UPI</option><option value="card">Card</option><option value="netbanking">Netbanking</option><option value="wallet">Wallet</option></select></label>
          <label>Data<select value={draftFilters.data_classification} onChange={(event) => setDraftFilters({ ...draftFilters, data_classification: event.target.value })}><option value="">All data</option><option>REAL</option><option>DEMO</option><option>UNCLASSIFIED</option></select></label>
          <label>Relationship<select value={draftFilters.relationship} onChange={(event) => setDraftFilters({ ...draftFilters, relationship: event.target.value })}><option value="">All relationships</option><option>ARMS_LENGTH</option><option>RELATED</option><option>PREEXISTING</option><option>UNCLASSIFIED</option></select></label>
          <div className="button-row"><button className="button revenue-button-primary">Apply</button><button className="button revenue-button-secondary" type="button" onClick={() => { setDraftFilters(emptyFilters); setFilters(emptyFilters); }}>Reset</button></div>
        </form>
        <section className="revenue-panel">
          <div className="revenue-panel-heading"><div><span>Customer portfolio</span><h2>Plan revenue by workspace</h2></div><small>{customers.length} shown</small></div>
          <div className="revenue-table-wrap"><table className="revenue-table"><thead><tr><th>Customer</th><th>Payment</th><th>Plan usage</th><th>Gross</th><th>Refunded</th><th>Classification</th></tr></thead><tbody>
            {customers.map((customer) => <tr key={customer.business_id}><td><strong>{customer.business_name}</strong><span>{customer.payer_email ?? customer.business_id}</span></td><td><span className={`revenue-status status-${(customer.payment_status ?? "none").toLowerCase()}`}>{humanize(customer.payment_status ?? "No payment")}</span><small>{humanize(customer.payment_method ?? "—")}</small></td><td><strong>{customer.invoices_used}/{customer.invoice_limit}</strong><span>{humanize(customer.plan_status ?? "No plan")}</span></td><td>{money(customer.gross_captured_minor)}</td><td>{money(customer.refunded_minor)}</td><td><span>{humanize(customer.data_classification)}</span><small>{humanize(customer.relationship)}</small></td></tr>)}
            {customers.length === 0 && <tr><td colSpan={6}><div className="empty-panel"><strong>No matching customers</strong><p>Adjust the filters to widen this view.</p></div></td></tr>}
          </tbody></table></div>
        </section>
        <section className="revenue-panel">
          <div className="revenue-panel-heading"><div><span>Transaction monitor</span><h2>Recent payment attempts</h2></div></div>
          <div className="revenue-payment-list">{payments.map((payment) => <button type="button" key={payment.id} onClick={() => void showPayment(payment.id)}><span className={`revenue-payment-icon payment-${payment.status.toLowerCase()}`}>₹</span><div><strong>{formatMinorAmount(payment.amount_minor, payment.currency)} · {humanize(payment.status)}</strong><small>{payment.payer_email ?? "No payer email"} · {humanize(payment.payment_method ?? payment.provider)}</small></div><time>{new Date(payment.updated_at).toLocaleString("en-IN")}</time></button>)}</div>
        </section>
        {detail && <div className="revenue-detail-backdrop" role="presentation" onMouseDown={() => setDetail(null)}><aside className="revenue-detail" role="dialog" aria-modal="true" aria-label="Payment detail" onMouseDown={(event) => event.stopPropagation()}><button className="revenue-detail-close" onClick={() => setDetail(null)} aria-label="Close payment detail">×</button><div className="eyebrow">Sanitized payment detail</div><h2>{detail.business?.name ?? "Deleted account"}</h2><dl><div><dt>Status</dt><dd>{humanize(detail.payment.status)}</dd></div><div><dt>Payment</dt><dd>{maskIdentifier(detail.payment.provider_payment_id)}</dd></div><div><dt>Order</dt><dd>{maskIdentifier(detail.payment.provider_order_id ?? detail.payment.billing_order_id)}</dd></div><div><dt>Method</dt><dd>{humanize(detail.payment.payment_method ?? detail.payment.provider)}</dd></div><div><dt>Captured</dt><dd>{detail.payment.captured_at ? new Date(detail.payment.captured_at).toLocaleString("en-IN") : "Not captured"}</dd></div><div><dt>Refunded</dt><dd>{formatMinorAmount(detail.payment.amount_refunded_minor, detail.payment.currency)}</dd></div><div><dt>Plan</dt><dd>{humanize(detail.plan?.status ?? "No plan")}</dd></div></dl><p className="revenue-detail-note">Card, bank account, VPA, signatures, secrets, and raw webhook payloads are never stored here.</p></aside></div>}
      </main>
    </AuthenticatedShell>
  );
}
