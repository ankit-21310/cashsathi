"use client";

import { useCallback, useEffect, useState } from "react";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import { RazorpayCheckout } from "@/components/razorpay-checkout";
import { apiFetch, BillingCurrent } from "@/lib/api";
import { formatMinorAmount, humanize } from "@/lib/format";

export default function BillingPage() {
  const { user } = useAuth();
  const [billing, setBilling] = useState<BillingCurrent | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!user) return;
    try {
      setBilling(await apiFetch<BillingCurrent>(user, "/api/billing/current"));
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Billing information is unavailable.");
    }
  }, [user]);

  useEffect(() => {
    const pending = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(pending);
  }, [load]);

  return (
    <AuthenticatedShell>
      <main className="dashboard-main billing-page">
        <div className="dashboard-heading">
          <div><div className="eyebrow">Workspace billing</div><h1>Founder Recovery Plan</h1></div>
          <span className="billing-price">₹299 <small>one time</small></span>
        </div>
        {error && <div className="alert alert-error">{error}</div>}
        <section className="billing-hero">
          <div>
            <span className="billing-kicker">Built for the first recovery loop</span>
            <h2>Track ten confirmed invoices with AI-assisted follow-up.</h2>
            <p>Invoice extraction, policy-controlled reminders, approvals, activity history, and receivables impact in one workspace.</p>
          </div>
          <div className="billing-purchase-card">
            {billing?.plan ? (
              <>
                <span className={`state-badge state-${billing.plan.status.toLowerCase()}`}>{humanize(billing.plan.status)}</span>
                <strong>{billing.plan.invoices_used} / {billing.plan.invoice_limit}</strong>
                <p>confirmed invoices used · {humanize(billing.plan.source)} payment</p>
              </>
            ) : (
              <>
                <strong>10 invoices</strong>
                <p>One payment. No recurring subscription.</p>
                <RazorpayCheckout onConfirmed={() => void load()} />
              </>
            )}
          </div>
        </section>
        <section className="foundation-panel billing-history">
          <div className="panel-heading"><div><div className="eyebrow">Payment history</div><h2>Verified transactions</h2></div></div>
          {!billing ? <div className="centered-state compact">Loading billing…</div> : billing.transactions.length === 0 ? (
            <div className="empty-panel"><strong>No payments yet</strong><p>Captured and refunded payments will appear here.</p></div>
          ) : billing.transactions.map((transaction) => (
            <div className="foundation-row" key={transaction.id}>
              <span className="status-icon">₹</span>
              <div><strong>{formatMinorAmount(transaction.amount_minor, transaction.currency)} · {humanize(transaction.status)}</strong><p>{humanize(transaction.payment_method ?? transaction.provider)} · {new Date(transaction.created_at).toLocaleString("en-IN")}</p></div>
              <span className="row-status">{transaction.amount_refunded_minor ? `${formatMinorAmount(transaction.amount_refunded_minor, transaction.currency)} refunded` : transaction.provider_payment_id}</span>
            </div>
          ))}
        </section>
      </main>
    </AuthenticatedShell>
  );
}
