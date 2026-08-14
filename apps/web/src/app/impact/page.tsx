"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import { apiFetch, MetricsResponse } from "@/lib/api";
import { formatMinorAmount } from "@/lib/format";

export default function ImpactPage() {
  const { user } = useAuth();
  const [metrics, setMetrics] = useState<MetricsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!user) return;
    apiFetch<MetricsResponse>(user, "/api/metrics").then(setMetrics).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Impact metrics unavailable."));
  }, [user]);
  return (
    <AuthenticatedShell><main className="dashboard-main">
      <div className="dashboard-heading"><div><div className="eyebrow">Verified outcomes</div><h1>Receivables impact</h1></div><Link className="button button-secondary" href="/admin/impact">Admin evidence</Link></div>
      {error && <div className="alert alert-error">{error}</div>}
      <section className="metric-grid metric-grid-six">
        <article><span>AI decisions</span><strong>{metrics?.ai_decisions ?? 0}</strong><small>Validated production runs</small></article>
        <article><span>Successful actions</span><strong>{metrics?.successful_actions ?? 0}</strong><small>Confirmed deliveries</small></article>
        <article><span>Automation rate</span><strong>{Math.round((metrics?.automation_rate ?? 0) * 100)}%</strong><small>Automatic / delivered</small></article>
        <article><span>Average days overdue</span><strong>{metrics?.average_days_overdue ?? 0}</strong><small>Currently overdue invoices</small></article>
      </section>
      <section className="currency-grid">{metrics?.currencies.map((item) => <article className="detail-card" key={item.currency}><span>{item.currency}</span><strong>{formatMinorAmount(item.monitored_minor, item.currency)} monitored</strong><small>{formatMinorAmount(item.outstanding_minor, item.currency)} outstanding · {formatMinorAmount(item.overdue_minor, item.currency)} overdue</small><small>{formatMinorAmount(item.verified_paid_minor, item.currency)} verified paid · {formatMinorAmount(item.post_action_paid_minor, item.currency)} after logged actions</small></article>)}</section>
      <aside className="tenant-note"><strong>Conservative attribution</strong><span>Post-action payment means timing correlation after a confirmed action; it does not claim the reminder caused payment.</span></aside>
    </main></AuthenticatedShell>
  );
}
