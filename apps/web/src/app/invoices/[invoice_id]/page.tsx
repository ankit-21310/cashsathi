"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import { apiFetch, CustomerSnapshot, EvaluationResult, Invoice, InvoiceDetail, InvoiceTimeline, Payment } from "@/lib/api";
import { formatMinorAmount, humanize } from "@/lib/format";

export default function InvoiceDetailPage() {
  const { invoice_id: invoiceId } = useParams<{ invoice_id: string }>();
  const { user, loading } = useAuth();
  const [detail, setDetail] = useState<InvoiceDetail | null>(null);
  const [timeline, setTimeline] = useState<InvoiceTimeline | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!user || !invoiceId) return;
    try {
      const [invoice, history] = await Promise.all([
        apiFetch<InvoiceDetail>(user, `/api/invoices/${invoiceId}`),
        apiFetch<InvoiceTimeline>(user, `/api/invoices/${invoiceId}/timeline`),
      ]);
      setDetail(invoice); setTimeline(history); setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Invoice could not be loaded.");
    }
  }, [invoiceId, user]);

  useEffect(() => {
    if (loading) return;
    const initial = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(initial);
  }, [load, loading]);

  async function evaluate() {
    if (!user || !detail) return;
    setBusy(true); setError(null);
    try {
      await apiFetch<EvaluationResult>(user, `/api/invoices/${detail.invoice.id}/evaluate`, { method: "POST" });
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The invoice could not be evaluated.");
    } finally { setBusy(false); }
  }

  async function recordPayment(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user || !detail) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true); setError(null);
    try {
      await apiFetch<Payment>(user, `/api/invoices/${detail.invoice.id}/payments`, {
        method: "POST",
        body: JSON.stringify({ amount_decimal: data.get("amount"), currency: detail.invoice.currency, paid_at: new Date(String(data.get("paid_at"))).toISOString(), reference: data.get("reference"), idempotency_key: crypto.randomUUID(), confirmed: true }),
      });
      form.reset(); await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Payment could not be recorded.");
    } finally { setBusy(false); }
  }

  async function updateCustomer(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user || !detail) return;
    const data = new FormData(event.currentTarget);
    setBusy(true); setError(null);
    try {
      await apiFetch<CustomerSnapshot>(user, `/api/customers/${detail.invoice.customer.id}`, {
        method: "PATCH",
        body: JSON.stringify({ manual_only: data.get("manual_only") === "on", locale: data.get("locale"), confirmed: true }),
      });
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Customer controls could not be updated.");
    } finally { setBusy(false); }
  }

  async function openDispute(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user || !detail) return;
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true); setError(null);
    try {
      await apiFetch<Invoice>(user, `/api/invoices/${detail.invoice.id}/disputes`, {
        method: "POST",
        body: JSON.stringify({ reason: data.get("reason"), note: data.get("note"), confirmed: true }),
      });
      form.reset(); await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The dispute could not be opened.");
    } finally { setBusy(false); }
  }

  async function resolveDispute(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!user || !detail) return;
    const data = new FormData(event.currentTarget);
    setBusy(true); setError(null);
    try {
      await apiFetch<Invoice>(user, `/api/invoices/${detail.invoice.id}/disputes/resolve`, {
        method: "POST",
        body: JSON.stringify({ resolution_note: data.get("resolution_note"), next_check_at: new Date(String(data.get("next_check_at"))).toISOString(), confirmed: true }),
      });
      await load();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The dispute could not be resolved.");
    } finally { setBusy(false); }
  }

  const run = detail?.latest_agent_run;
  const balance = detail ? detail.invoice.amount_minor - detail.invoice.verified_paid_minor : 0;
  return (
    <AuthenticatedShell><main className="workflow-main detail-main">
      <div className="workflow-heading"><div><div className="eyebrow">Confirmed invoice</div><h1>{detail?.invoice.invoice_number ?? "Loading invoice…"}</h1></div><Link className="button button-ghost" href="/dashboard">Back to dashboard</Link></div>
      {error && <div className="alert alert-error">{error}</div>}
      {!detail ? <div className="centered-state compact">Loading secure invoice record…</div> : <>
        <section className="detail-grid"><article className="detail-card amount-card"><span>Outstanding balance</span><strong>{formatMinorAmount(balance, detail.invoice.currency)}</strong><small>{formatMinorAmount(detail.invoice.verified_paid_minor, detail.invoice.currency)} verified paid</small></article><article className="detail-card"><span>Current state</span><strong className={`state-badge state-${detail.current_state.toLowerCase()}`}>{humanize(detail.current_state)}</strong><small>{detail.invoice.next_check_at ? `Next check ${new Date(detail.invoice.next_check_at).toLocaleString("en-IN")}` : "Automatic checks paused"}</small></article><article className="detail-card"><span>Customer</span><strong>{detail.invoice.customer.name}</strong><small>{detail.invoice.customer.email ?? "Email not provided"}</small></article></section>
        <section className="safety-controls">
          <form className="integration-card" onSubmit={updateCustomer}>
            <div><div className="eyebrow">Customer safeguards</div><h2>Delivery controls</h2></div>
            <label className="checkbox-label"><input name="manual_only" type="checkbox" defaultChecked={detail.invoice.customer.manual_only} />Require owner approval for every reminder to this customer.</label>
            <label>Reminder language<select name="locale" defaultValue={detail.invoice.customer.locale}><option value="en-IN">English (India)</option><option value="hi-IN">Hindi (India)</option></select></label>
            <button className="button button-secondary" disabled={busy}>Save customer controls</button>
          </form>
          {!detail.invoice.dispute_active ? <form className="integration-card" onSubmit={openDispute}>
            <div><div className="eyebrow">Hard stop</div><h2>Open a dispute</h2><p className="currency-note">Opening a dispute pauses the invoice and cancels any unsent reminder.</p></div>
            <label>Reason<select name="reason" defaultValue="PAYMENT_CLAIM"><option value="INVOICE_ERROR">Invoice error</option><option value="SERVICE_ISSUE">Service issue</option><option value="PAYMENT_CLAIM">Payment claim</option><option value="TERMS_DISAGREEMENT">Terms disagreement</option><option value="OTHER">Other</option></select></label>
            <label>Private note (never included in public evidence)<textarea name="note" maxLength={1000} /></label>
            <button className="button button-danger" disabled={busy}>Pause for dispute</button>
          </form> : <form className="integration-card" onSubmit={resolveDispute}>
            <div><div className="eyebrow">Dispute active</div><h2>Resolve and reopen</h2><p className="currency-note">Select an explicit future check before collection can resume.</p></div>
            <label>Private resolution note<textarea name="resolution_note" maxLength={1000} /></label>
            <label>Next check<input name="next_check_at" type="datetime-local" required /></label>
            <button className="button button-primary" disabled={busy}>Resolve dispute</button>
          </form>}
        </section>
        <section className="decision-panel"><div className="panel-heading"><div><div className="eyebrow">Controlled decision</div><h2>Agent evaluation</h2></div><button className="button button-primary" disabled={busy || detail.current_state === "PAID" || detail.invoice.dispute_active} onClick={() => void evaluate()}>{busy ? "Working…" : run ? "Evaluate again" : "Evaluate invoice"}</button></div>{!run ? <div className="empty-panel"><strong>No decision yet</strong><p>Gemini proposes one bounded function call, then deterministic policy decides what is permitted.</p></div> : <div className="decision-content"><div className="decision-summary"><div><span>Model function</span><strong>{humanize(run.proposed_function ?? run.model_proposal?.decision ?? run.status)}</strong></div><div><span>Policy result</span><strong>{humanize(run.policy_result?.final_decision ?? run.status)}</strong></div><div><span>Outcome</span><strong>{humanize(run.policy_result?.outcome ?? run.status)}</strong></div></div>{run.model_proposal && <p className="rationale">{run.model_proposal.rationale}</p>}<div className="rule-list">{run.policy_result?.matched_rules.length ? run.policy_result.matched_rules.map((rule) => <span key={rule}>{humanize(rule)}</span>) : <span>No blocking safeguard matched</span>}</div>{detail.latest_action && <div className="action-preview"><strong>{detail.latest_action.subject}</strong><p>{detail.latest_action.body}</p><small>{detail.latest_action.locale} · {detail.latest_action.template_version}</small><Link className="button button-secondary" href="/approvals">Review action</Link></div>}</div>}</section>
        {detail.current_state !== "PAID" && <form className="payment-form" onSubmit={recordPayment}><div><div className="eyebrow">Owner-confirmed outcome</div><h2>Record payment</h2><p>Partial payments are supported. The invoice closes only when the verified total reaches its face value.</p></div><div className="form-grid"><label>Amount<input name="amount" required /></label><label>Paid at<input name="paid_at" type="datetime-local" required /></label></div><label>Bank / UPI reference<input name="reference" required /></label><label className="checkbox-label"><input type="checkbox" required />I confirm this payment was received.</label><button className="button button-primary" disabled={busy}>Record verified payment</button></form>}
        <section className="timeline-panel"><div className="panel-heading"><div><div className="eyebrow">Audit trail</div><h2>Invoice timeline</h2></div></div><ol className="timeline">{timeline?.items.map((item) => <li key={`${item.entry_type}-${item.id}`}><span className="timeline-dot" /><div><strong>{item.title}</strong><p>{item.detail}</p><time>{new Date(item.occurred_at).toLocaleString("en-IN")} · {item.status && humanize(item.status)}</time></div></li>)}</ol></section>
      </>}
    </main></AuthenticatedShell>
  );
}
