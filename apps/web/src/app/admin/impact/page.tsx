"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import {
  AdminImpact,
  apiDownload,
  apiFetch,
  Business,
  EvidenceLedgerEntry,
  Page,
} from "@/lib/api";
import { formatMinorAmount, humanize } from "@/lib/format";

export default function AdminImpactPage() {
  const { user } = useAuth();
  const [impact, setImpact] = useState<AdminImpact | null>(null);
  const [businesses, setBusinesses] = useState<Business[]>([]);
  const [ledger, setLedger] = useState<EvidenceLedgerEntry[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!user) return;
    try {
      const [summary, businessPage, ledgerPage] = await Promise.all([
        apiFetch<AdminImpact>(user, "/api/admin/impact"),
        apiFetch<Page<Business>>(user, "/api/admin/businesses?limit=100"),
        apiFetch<Page<EvidenceLedgerEntry>>(user, "/api/admin/evidence-ledger?limit=100"),
      ]);
      setImpact(summary);
      setBusinesses(businessPage.items);
      setLedger(ledgerPage.items);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Admin evidence is unavailable.");
    }
  }, [user]);

  useEffect(() => {
    const initial = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(initial);
  }, [load]);

  async function classify(business: Business, data: string, relationship: string) {
    if (!user) return;
    await apiFetch(user, `/api/admin/businesses/${business.id}/classification`, {
      method: "POST",
      body: JSON.stringify({ data_classification: data, relationship }),
    });
    await load();
  }

  async function addLedger(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      await apiFetch(user, "/api/admin/evidence-ledger", {
        method: "POST",
        body: JSON.stringify({
          kind: data.get("kind"), amount_decimal: data.get("amount"),
          currency: data.get("currency"), occurred_on: data.get("date"),
          category: data.get("category"), reference: data.get("reference"),
          business_id: data.get("business_id") || null,
          marketing: data.get("marketing") === "on", reversal_of: null,
        }),
      });
      form.reset();
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Ledger entry could not be recorded.");
    }
  }

  async function activatePlan(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    try {
      await apiFetch(user, "/api/admin/founder-plans/activate", {
        method: "POST",
        body: JSON.stringify({
          business_id: data.get("business_id"), paid_on: data.get("paid_on"),
          receipt_reference: data.get("receipt_reference"),
          idempotency_key: crypto.randomUUID(), confirmed: true,
        }),
      });
      form.reset();
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Founder Plan could not be activated.");
    }
  }

  async function downloadEvidence() {
    if (!user) return;
    try {
      const { blob, filename } = await apiDownload(user, "/api/admin/evidence-export");
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Evidence export failed.");
    }
  }

  const moneyRows = (values: Record<string, number>) =>
    Object.entries(values).map(([currency, amount]) => formatMinorAmount(amount, currency)).join(" · ") || "—";

  return (
    <AuthenticatedShell><main className="dashboard-main">
      <div className="dashboard-heading"><div><div className="eyebrow">Submission evidence</div><h1>Platform impact</h1></div><div className="button-row"><a className="button button-secondary" href="/admin/validation">Validation workspace</a><button className="button button-primary" onClick={() => void downloadEvidence()}>Download evidence ZIP</button></div></div>
      {error && <div className="alert alert-error">{error}</div>}
      {impact && <section className="metric-grid metric-grid-six">
        <article><span>Arms-length paying businesses</span><strong>{impact.paying_businesses}</strong><small>{impact.related_paying_businesses} related · {impact.preexisting_paying_businesses} pre-existing</small></article>
        <article><span>Product revenue</span><strong>{moneyRows(impact.revenue_by_currency)}</strong><small>Related {moneyRows(impact.related_revenue_by_currency)} · pre-existing {moneyRows(impact.preexisting_revenue_by_currency)}</small></article>
        <article><span>Expenses</span><strong>{moneyRows(impact.expenses_by_currency)}</strong><small>{moneyRows(impact.marketing_spend_by_currency)} marketing</small></article>
        <article><span>Validation funnel</span><strong>{impact.design_partners} partners</strong><small>{impact.prospects} prospects · {impact.interviews} interviews</small></article>
        <article><span>Founder plans</span><strong>{impact.active_founder_plans} active</strong><small>{impact.exhausted_founder_plans} exhausted</small></article>
        <article><span>Consented testimonials</span><strong>{impact.consented_testimonials}</strong><small>CAC {moneyRows(impact.cac_by_currency)}</small></article>
      </section>}
      <section className="admin-grid">
        <div className="foundation-panel"><div className="panel-heading"><div><h2>Business classification</h2></div></div><div className="activity-list">{businesses.map((business) => <div className="activity-row" key={business.id}><div><strong>{business.name}</strong><p>{humanize(business.data_classification)} · {humanize(business.relationship)}</p></div><div className="button-row"><button className="button button-secondary" onClick={() => void classify(business, "REAL", "ARMS_LENGTH")}>Arms-length</button><button className="button button-ghost" onClick={() => void classify(business, "REAL", "RELATED")}>Related</button><button className="button button-ghost" onClick={() => void classify(business, "REAL", "PREEXISTING")}>Pre-existing</button><button className="button button-ghost" onClick={() => void classify(business, "DEMO", "UNCLASSIFIED")}>Demo</button></div></div>)}</div></div>
        <form className="workflow-card ledger-form" onSubmit={addLedger}><div className="step-number">₹</div><div><div className="eyebrow">Append-only ledger</div><h2>Record evidence</h2><label>Kind<select name="kind"><option value="PRODUCT_REVENUE">Product revenue</option><option value="EXPENSE">Expense</option></select></label><div className="form-grid"><label>Amount<input name="amount" required /></label><label>Currency<input name="currency" defaultValue="INR" required /></label><label>Date<input name="date" type="date" required /></label><label>Category<input name="category" required /></label></div><label>Reference<input name="reference" required /></label><label>Business<select name="business_id"><option value="">Not linked</option>{businesses.map((business) => <option value={business.id} key={business.id}>{business.name}</option>)}</select></label><label className="checkbox-label"><input name="marketing" type="checkbox" />Marketing or acquisition expense</label><button className="button button-primary">Append ledger entry</button></div></form>
      </section>
      <form className="workflow-card ledger-form" onSubmit={activatePlan}><div className="step-number">10</div><div><div className="eyebrow">Manual payment verified</div><h2>Activate Founder Recovery Plan</h2><p>₹299 one-time for ten confirmed invoices. Classify the business first.</p><label>Business<select name="business_id" required><option value="">Select business</option>{businesses.map((business) => <option value={business.id} key={business.id}>{business.name}</option>)}</select></label><div className="form-grid"><label>Paid on<input name="paid_on" type="date" required /></label><label>Receipt reference<input name="receipt_reference" required /></label></div><button className="button button-primary">Verify payment and activate</button></div></form>
      <section className="foundation-panel ledger-list"><div className="panel-heading"><div><h2>Evidence ledger</h2></div><span>{ledger.length} entries</span></div>{ledger.map((entry) => <div className="foundation-row" key={entry.id}><span className="status-icon">•</span><div><strong>{humanize(entry.kind)} · {formatMinorAmount(entry.amount_minor, entry.currency)}</strong><p>{entry.reference} · {entry.occurred_on}</p></div><span className="row-status">{entry.reversal_of ? "Reversal" : "Recorded"}</span></div>)}</section>
    </main></AuthenticatedShell>
  );
}
