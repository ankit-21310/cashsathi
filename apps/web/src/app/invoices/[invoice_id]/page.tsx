"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import { apiFetch, EvaluationResult, InvoiceDetail } from "@/lib/api";
import { formatMinorAmount, humanize } from "@/lib/format";

export default function InvoiceDetailPage() {
  const { invoice_id: invoiceId } = useParams<{ invoice_id: string }>();
  const { user, loading } = useAuth();
  const [detail, setDetail] = useState<InvoiceDetail | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (loading || !user || !invoiceId) return;
    apiFetch<InvoiceDetail>(user, `/api/invoices/${invoiceId}`)
      .then(setDetail)
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "Invoice could not be loaded."),
      );
  }, [invoiceId, loading, user]);

  async function evaluate() {
    if (!user || !detail) return;
    setBusy(true);
    setError(null);
    try {
      const result = await apiFetch<EvaluationResult>(
        user,
        `/api/invoices/${detail.invoice.id}/evaluate`,
        { method: "POST" },
      );
      setDetail((current) => current ? {
        ...current,
        latest_agent_run: result.agent_run,
        latest_action: result.action,
        current_state: result.agent_run.invoice_state,
      } : current);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The invoice could not be evaluated.");
    } finally {
      setBusy(false);
    }
  }

  const run = detail?.latest_agent_run;
  return (
    <AuthenticatedShell>
      <main className="workflow-main detail-main">
        <div className="workflow-heading">
          <div><div className="eyebrow">Confirmed invoice</div><h1>{detail?.invoice.invoice_number ?? "Loading invoice…"}</h1></div>
          <Link className="button button-ghost" href="/dashboard">Back to dashboard</Link>
        </div>
        {error && <div className="alert alert-error">{error}</div>}
        {!detail ? <div className="centered-state compact">Loading secure invoice record…</div> : <>
          <section className="detail-grid">
            <article className="detail-card amount-card"><span>Outstanding face value</span><strong>{formatMinorAmount(detail.invoice.amount_minor, detail.invoice.currency)}</strong><small>No payment inference is used.</small></article>
            <article className="detail-card"><span>Current state</span><strong className={`state-badge state-${detail.current_state.toLowerCase()}`}>{humanize(detail.current_state)}</strong><small>{detail.invoice.due_date ? `Due ${detail.invoice.due_date}` : "Due date requires owner review"}</small></article>
            <article className="detail-card"><span>Customer</span><strong>{detail.invoice.customer.name}</strong><small>{detail.invoice.customer.email ?? "Email not provided"}</small></article>
          </section>
          <section className="decision-panel">
            <div className="panel-heading"><div><div className="eyebrow">Phase 3 · Controlled decision</div><h2>Agent evaluation</h2></div><button className="button button-primary" disabled={busy} onClick={evaluate}>{busy ? "Evaluating safely…" : run ? "Evaluate again" : "Evaluate invoice"}</button></div>
            {!run ? <div className="empty-panel"><strong>No decision yet</strong><p>Gemini proposes one bounded next step, then deterministic policy decides what is permitted.</p></div> : <div className="decision-content">
              <div className="decision-summary"><div><span>Model proposal</span><strong>{humanize(run.model_proposal?.decision ?? run.status)}</strong></div><div><span>Policy result</span><strong>{humanize(run.policy_result?.final_decision ?? run.status)}</strong></div><div><span>Outcome</span><strong>{humanize(run.policy_result?.outcome ?? run.status)}</strong></div></div>
              {run.model_proposal && <p className="rationale">{run.model_proposal.rationale}</p>}
              {run.policy_result?.matched_rules.length ? <div className="rule-list">{run.policy_result.matched_rules.map((rule) => <span key={rule}>{humanize(rule)}</span>)}</div> : <div className="rule-list"><span>No blocking safeguard matched</span></div>}
              <dl className="metadata-list"><div><dt>Action</dt><dd>{detail.latest_action ? `${humanize(detail.latest_action.action_type)} · ${humanize(detail.latest_action.state)}` : "No external action created"}</dd></div><div><dt>Next check</dt><dd>{run.policy_result?.next_check_at ? new Date(run.policy_result.next_check_at).toLocaleString("en-IN") : "Not scheduled"}</dd></div><div><dt>Evidence</dt><dd>{run.model_id} · {run.prompt_version} · {run.attempt_count} attempt(s)</dd></div></dl>
            </div>}
          </section>
          <aside className="tenant-note"><strong>Extraction provenance</strong><span>Source event <code>{detail.invoice.extraction_id}</code>. The original PDF was not retained.</span></aside>
        </>}
      </main>
    </AuthenticatedShell>
  );
}
