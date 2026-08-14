"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import {
  apiFetch,
  getApiReadiness,
  GmailStatus,
  InvoicePage,
  MeResponse,
  MetricsResponse,
} from "@/lib/api";
import { formatMinorAmount, humanize } from "@/lib/format";

export default function DashboardPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const [me, setMe] = useState<MeResponse | null>(null);
  const [invoices, setInvoices] = useState<InvoicePage | null>(null);
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [gmail, setGmail] = useState<GmailStatus | null>(null);
  const [apiReady, setApiReady] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (loading || !user) return;
    Promise.all([
      apiFetch<MeResponse>(user, "/api/me"),
      apiFetch<InvoicePage>(user, "/api/invoices"),
      apiFetch<MetricsResponse>(user, "/api/metrics"),
      apiFetch<GmailStatus>(user, "/api/integrations/gmail/status"),
      getApiReadiness(),
    ])
      .then(([account, invoicePage, metricResult, gmailResult, ready]) => {
        if (!account.business) return router.replace("/onboarding");
        setMe(account);
        setInvoices(invoicePage);
        setMetrics(metricResult);
        setGmail(gmailResult);
        setApiReady(ready);
      })
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "The workspace could not be loaded."),
      );
  }, [loading, router, user]);

  const primary = metrics?.currencies[0];
  const amount = (value: number | undefined) =>
    primary && value !== undefined ? formatMinorAmount(value, primary.currency) : "—";

  return (
    <AuthenticatedShell>
      <main className="dashboard-main">
        <div className="dashboard-heading">
          <div><div className="eyebrow">Receivables workspace</div><h1>{me?.business?.name ?? "Loading workspace…"}</h1></div>
          <div className="heading-actions">
            <div className={`service-pill ${apiReady === false ? "offline" : ""}`}><span />{apiReady === null ? "Checking API" : apiReady ? "Services ready" : "API unavailable"}</div>
            <Link className="button button-primary" href="/invoices/new">Upload invoice</Link>
          </div>
        </div>
        {error && <div className="alert alert-error">{error}</div>}
        {me?.business && me.business.data_classification !== "REAL" && (
          <div className="data-banner"><strong>{humanize(me.business.data_classification)} data</strong><span>This workspace is excluded from real-customer impact claims.</span></div>
        )}
        <section className="metric-grid metric-grid-six" aria-label="Receivables metrics">
          <article><span>Monitored value</span><strong>{amount(primary?.monitored_minor)}</strong><small>{metrics?.invoice_count ?? 0} invoices</small></article>
          <article><span>Outstanding</span><strong>{amount(primary?.outstanding_minor)}</strong><small>{metrics?.overdue_count ?? 0} overdue</small></article>
          <article><span>Verified paid</span><strong>{amount(primary?.verified_paid_minor)}</strong><small>Owner-confirmed only</small></article>
          <article><span>AI decisions</span><strong>{metrics?.ai_decisions ?? 0}</strong><small>{metrics?.successful_actions ?? 0} successful actions</small></article>
          <article><span>Automation rate</span><strong>{Math.round((metrics?.automation_rate ?? 0) * 100)}%</strong><small>Confirmed deliveries</small></article>
          <article><span>Gmail loop</span><strong>{gmail?.connected ? (gmail.automation_enabled ? "Live" : "Approval") : "Off"}</strong><small>{metrics?.pending_approvals ?? 0} approvals</small></article>
        </section>
        {metrics && metrics.currencies.length > 1 && <div className="currency-note">Currencies remain separate; no exchange-rate conversion is applied.</div>}
        <section className="foundation-panel">
          <div className="panel-heading"><div><div className="eyebrow">Invoice intelligence</div><h2>Confirmed invoices</h2></div><span>{invoices ? `${invoices.items.length} shown` : "Loading"}</span></div>
          {invoices?.items.length === 0 ? (
            <div className="empty-panel"><strong>No invoices yet</strong><p>Upload a PDF to extract and confirm its receivables facts.</p><Link className="button button-secondary" href="/invoices/new">Start first invoice</Link></div>
          ) : (
            <div className="invoice-list">
              {invoices?.items.map((invoice) => (
                <Link className="invoice-row" href={`/invoices/${invoice.id}`} key={invoice.id}>
                  <div><strong>{invoice.invoice_number}</strong><p>{invoice.customer_name}</p></div>
                  <div><strong>{formatMinorAmount(invoice.amount_minor, invoice.currency)}</strong><p>{invoice.due_date ? `Due ${invoice.due_date}` : "Due date missing"}</p></div>
                  <span className={`state-badge state-${invoice.current_state.toLowerCase()}`}>{humanize(invoice.current_state)}</span>
                </Link>
              ))}
            </div>
          )}
        </section>
      </main>
    </AuthenticatedShell>
  );
}
