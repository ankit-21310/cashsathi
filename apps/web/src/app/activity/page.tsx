"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { AuthenticatedShell } from "@/components/authenticated-shell";
import { useAuth } from "@/components/auth-provider";
import { AgentRunPage, apiFetch } from "@/lib/api";
import { humanize } from "@/lib/format";

export default function ActivityPage() {
  const { user } = useAuth();
  const [runs, setRuns] = useState<AgentRunPage | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!user) return;
    try {
      setRuns(await apiFetch<AgentRunPage>(user, "/api/agent-runs?limit=50"));
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Activity could not be loaded.");
    }
  }, [user]);

  useEffect(() => {
    const initial = window.setTimeout(() => void load(), 0);
    const interval = window.setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 30_000);
    return () => { window.clearTimeout(initial); window.clearInterval(interval); };
  }, [load]);

  return (
    <AuthenticatedShell>
      <main className="dashboard-main">
        <div className="dashboard-heading"><div><div className="eyebrow">Production evidence</div><h1>Agent activity</h1></div><button className="button button-secondary" onClick={() => void load()}>Refresh</button></div>
        {error && <div className="alert alert-error">{error}</div>}
        <section className="foundation-panel">
          <div className="panel-heading"><div><h2>Chronological runs</h2></div><span>{runs?.items.length ?? 0} shown</span></div>
          {!runs ? <div className="empty-panel"><strong>Loading agent activity…</strong></div> : runs.items.length === 0 ? <div className="empty-panel"><strong>No agent runs yet</strong><p>Evaluate an invoice to create the first auditable decision.</p></div> : (
            <div className="activity-list">{runs.items.map((run) => (
              <Link href={`/invoices/${run.invoice_id}`} className="activity-row" key={run.id}>
                <span className={`state-badge state-${run.status.toLowerCase()}`}>{humanize(run.status)}</span>
                <div><strong>{humanize(run.policy_result?.final_decision ?? run.status)}</strong><p>{run.model_proposal?.rationale ?? run.failure_code ?? "No rationale available"}</p></div>
                <time>{new Date(run.created_at).toLocaleString("en-IN")}</time>
              </Link>
            ))}</div>
          )}
        </section>
      </main>
    </AuthenticatedShell>
  );
}
