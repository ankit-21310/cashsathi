"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import { apiFetch, getApiReadiness, InvoicePage, MeResponse } from "@/lib/api";
import { formatMinorAmount, humanize } from "@/lib/format";

export default function DashboardPage() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const [me, setMe] = useState<MeResponse | null>(null);
  const [invoices, setInvoices] = useState<InvoicePage | null>(null);
  const [apiReady, setApiReady] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (loading || !user) return;
    Promise.all([
      apiFetch<MeResponse>(user, "/api/me"),
      apiFetch<InvoicePage>(user, "/api/invoices"),
      getApiReadiness(),
    ])
      .then(([account, invoicePage, ready]) => {
        if (!account.business) return router.replace("/onboarding");
        setMe(account);
        setInvoices(invoicePage);
        setApiReady(ready);
      })
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "The workspace could not be loaded."),
      );
  }, [loading, router, user]);

  const reviewCount = invoices?.items.filter(
    (item) => item.current_state === "HUMAN_REVIEW",
  ).length ?? 0;

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
        <section className="metric-grid" aria-label="Invoice status">
          <article><span>Invoices monitored</span><strong>{invoices?.items.length ?? 0}</strong><small>Confirmed invoice records</small></article>
          <article><span>Needs review</span><strong>{reviewCount}</strong><small>Missing dates or policy exceptions</small></article>
          <article><span>Tenant role</span><strong>{me?.membership?.role ?? "—"}</strong><small>One owner in this preview</small></article>
        </section>
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
        <aside className="tenant-note"><strong>Tenant isolation is active.</strong><span>Every invoice is resolved through business <code>{me?.business?.id ?? "…"}</code> from your verified membership.</span></aside>
      </main>
    </AuthenticatedShell>
  );
}
